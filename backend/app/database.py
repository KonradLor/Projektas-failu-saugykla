"""
Duomenų bazės konfigūracijos modulis.

Šis modulis sukonfiguruoja SQLAlchemy ORM darbui su SQLite duomenų baze:
    - engine          → Pagrindinis prisijungimo objektas
    - SessionLocal    → Sesijų gamintojas (factory)
    - Base            → Bazinis ORM modelių klasė (paveldima models/ failuose)
    - get_db()        → FastAPI dependency endpointams (su automatic cleanup)

NAUDOJIMAS API ENDPOINTUOSE:
    from fastapi import Depends
    from sqlalchemy.orm import Session
    from app.database import get_db

    @router.get("/users")
    def list_users(db: Session = Depends(get_db)):
        return db.query(User).all()
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


# ============================================
# LOGGER
# ============================================

# Šio modulio logger'is - log'ina DB inicializacijos įvykius
logger = logging.getLogger(__name__)


# ============================================
# SQLALCHEMY ENGINE
# ============================================

# Connect args - papildomi parametrai SQLite ryšiui
# check_same_thread=False - reikalingas SQLite + FastAPI (kitaip mes klaidą,
# nes FastAPI naudoja kelias gijas)
connect_args = {"check_same_thread": False}

# Engine - pagrindinis SQLAlchemy objektas, valdantis DB ryšius (connection pool)
# Kuriamas tik kartą paleidimo metu, naudojamas visam aplikacijos gyvenimui
engine = create_engine(
    # DB URL iš konfigūracijos (pvz. sqlite:///./konradvault.db)
    url=settings.database_url,

    # Connection args (žr. aukščiau)
    connect_args=connect_args,

    # Echo - jei True, spausdina visas SQL užklausas į konsolę
    # Naudinga debug'ui, bet production'e visada False
    echo=settings.database_echo,

    # Pool pre-ping - tikrina ar ryšys gyvas prieš naudojimą
    # Apsaugo nuo "MySQL has gone away" tipo klaidų (čia mažiau aktualu, bet good practice)
    pool_pre_ping=True,
)


# ============================================
# SQLITE PERFORMANCE OPTIMIZACIJOS
# ============================================

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    gauna: dbapi_connection - žemo lygio DB ryšio objektas
           connection_record - SQLAlchemy connection record (nenaudojamas)
    daro: kiekvieno naujo SQLite ryšio metu nustato PRAGMA optimizacijas:
          - foreign_keys = ON  (reikalinga FK constraints veikti)
          - journal_mode = WAL (geresnė concurrent access performance)
          - synchronous = NORMAL (geras balansas tarp greičio ir saugumo)
    grąžina: None
    """
    # Tikrina ar tai SQLite ryšys (jei kada migruosim į PostgreSQL - šitas nesveiks)
    if "sqlite" not in settings.database_url.lower():
        return

    cursor = dbapi_connection.cursor()

    # Įjungiame foreign key palaikymą - SQLite pagal nutylėjimą jį išjungia (!)
    # Be šito FK constraints neveikia, ON DELETE CASCADE neveikia ir t.t.
    cursor.execute("PRAGMA foreign_keys = ON")

    # WAL (Write-Ahead Logging) - leidžia rašyti ir skaityti vienu metu
    # Geresnė performance kai daug skaitymo + retas rašymas (mūsų atvejis)
    cursor.execute("PRAGMA journal_mode = WAL")

    # NORMAL synchronous - greitesnis nei FULL, bet vis tiek saugus
    # Tinkamas mūsų use case'ui (ne finansų sistema)
    cursor.execute("PRAGMA synchronous = NORMAL")

    # Užšifruoja temp failus į RAM (ne į diską) - greičiau ir saugiau
    cursor.execute("PRAGMA temp_store = MEMORY")

    cursor.close()


# ============================================
# SESSION FACTORY
# ============================================

# SessionLocal - tai NĖRA pati sesija, o "fabrikas" sesijoms kurti
# Kiekviena užklausa gauna savo sesiją (per get_db dependency)
SessionLocal = sessionmaker(
    # Engine, su kuriuo bus jungiamasi
    bind=engine,

    # autocommit=False - reikia rankiniu būdu kviesti db.commit()
    # Apsaugo nuo netyčinio duomenų pakeitimo
    autocommit=False,

    # autoflush=False - SQLAlchemy automatiškai nesisinchronizuoja prieš query
    # Geresnis predictability ir performance
    autoflush=False,
)


# ============================================
# BAZINĖ MODELIŲ KLASĖ
# ============================================

class Base(DeclarativeBase):
    """
    Visi SQLAlchemy ORM modeliai paveldi šitą klasę.

    SQLAlchemy 2.0 stiliaus DeclarativeBase - moderniau nei senas declarative_base().
    Visi modeliai (User, File, Folder ir t.t.) bus apibrėžti kaip:

        from app.database import Base

        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(primary_key=True)
            ...
    """
    # Galima pridėti bendras savybes/metodus, kuriuos paveldės visi modeliai
    # Pvz. __repr__ metodą, kuris automatiškai parodo lentelės pavadinimą + id
    pass


# ============================================
# FASTAPI DEPENDENCY
# ============================================

def get_db() -> Generator[Session, None, None]:
    """
    gauna: nieko
    daro: sukuria naują DB sesiją užklausai, atiduoda ją endpoint'ui (yield),
          po endpoint'o pabaigos automatiškai uždaro sesiją (net jei buvo klaida).
          Tai užtikrina, kad nepaliksim "leaking" connections.
    grąžina: (Generator[Session]) - DB sesija (vienai užklausai)

    NAUDOJIMAS:
        @router.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    # Sukuriame naują sesiją iš fabriko
    db = SessionLocal()

    try:
        # Atiduodame sesiją endpoint'ui per yield
        # Endpoint'as gaus šitą sesiją per Depends(get_db)
        yield db
    finally:
        # PRIVALOMA uždaryti sesiją - net jei buvo exception
        # Be šito - "connection leak" (greitai išnaudosi pool)
        db.close()


# ============================================
# DB INICIALIZAVIMO FUNKCIJOS
# ============================================

def init_db() -> None:
    """
    gauna: nieko
    daro: sukuria visas DB lenteles pagal modelius (jei dar neegzistuoja).
          Importuoja visus modelius, kad SQLAlchemy juos "matytų".
          Kviečiama paleidimo metu (main.py startup event).

          PASTABA: Production'e geriau naudoti Alembic migracijas (alembic upgrade).
          Šitas init_db() yra dev/quick-start režimui.
    grąžina: None
    """
    logger.info("Inicializuojama duomenų bazė...")

    # SVARBU: importuoti modelius PRIEŠ create_all()!
    # Kitaip SQLAlchemy nežino apie lenteles ir niekas nesukurs.
    # Šitas importas atrodo "nenaudojamas", bet jis būtinas - tai šalutinis poveikis (registracija).
    # Importas viduje funkcijos (ne viršuje) - apeiti circular imports
    # noinspection PyUnresolvedReferences
    from app import models  # noqa: F401

    # create_all() sukuria visas lenteles, kurių dar nėra
    # Egzistuojančių neperdaro - jei reikia pakeisti schemą, naudoti Alembic
    Base.metadata.create_all(bind=engine)

    logger.info("Duomenų bazė inicializuota sėkmingai")


def check_db_connection() -> bool:
    """
    gauna: nieko
    daro: bando prisijungti prie DB ir įvykdyti paprastą užklausą.
          Naudojama health check endpoint'e ir paleidimo metu.
    grąžina: (bool) - True jei DB pasiekiama, False jei ne
    """
    try:
        # Bandom sukurti sesiją ir įvykdyti SELECT 1
        # Jei DB neveikia - mes exception
        with SessionLocal() as db:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
        return True

    except Exception as exc:
        # Logginame klaidą, bet negriunam aplikacijos
        logger.error(f"DB connection check nepavyko: {exc}")
        return False
