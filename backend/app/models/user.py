"""
Vartotojo (User) SQLAlchemy ORM modelis.

Lentelė "users" saugo visus sistemos vartotojus:
    - Prisijungimo duomenis (username + Argon2 slaptažodžio hash)
    - 2FA paslaptį (TOTP - Google Authenticator)
    - Šifravimo raktą (per-user encryption key, užšifruotas master_key)
    - Rolę (admin ar eilinis vartotojas)
    - Statusą (aktyvus / deaktyvuotas)
    - Naudojamą disko vietą (storage_used_bytes - 2GB limito stebėjimui)

RYŠIAI:
    User → Folder   (vienas vartotojas gali turėti daug aplankalų)
    User → File     (vienas vartotojas gali turėti daug failų)
    User → Session  (vienas vartotojas gali turėti daug aktyvių sesijų)
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================
# MODELIS
# ============================================

class User(Base):
    """
    Vartotojo duomenų bazės modelis.

    Lentelės pavadinimas: users
    """

    # SQLAlchemy lentelės pavadinimas duomenų bazėje
    __tablename__ = "users"

    # ----------------------------------------
    # PIRMINIAI LAUKAI
    # ----------------------------------------

    # Pirminis raktas - automatiškai didinamas sveikasis skaičius
    # SQLite automatiškai sukuria ROWID kaip INTEGER PRIMARY KEY
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        comment="Unikalus vartotojo identifikatorius",
    )

    # ----------------------------------------
    # AUTENTIFIKACIJOS LAUKAI
    # ----------------------------------------

    # Vartotojo vardas - unikalus visoje sistemoje
    # max 50 simbolių, tik mažosios raidės, skaičiai ir brūkšniai (validuojama schemos lygiu)
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,         # Indeksas greitesnei paieškai (login metu dažnai ieškosim)
        comment="Unikalus vartotojo prisijungimo vardas",
    )

    # Slaptažodžio hash - NIEKADA nesaugoti plaintext slaptažodžio!
    # Argon2id hash formatas: $argon2id$v=19$m=65536,t=3,p=4$...
    # Ilgis: Argon2 hash paprastai ~97 simboliai, bet geriau imti su atsarga
    password_hash: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="Argon2id slaptažodžio hash (niekada ne plaintext)",
    )

    # TOTP paslaptis - Google Authenticator kodų generavimui
    # base32 encoded, 32 simboliai (pyotp.random_base32() sugeneruotas)
    # Saugoma aiškiai (plaintext), nes reikia kiekvienai TOTP verifikacijai
    # Galima šifruoti master_key, bet tai komplikuoja logiką
    totp_secret: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Google Authenticator TOTP paslaptis (base32 formatas)",
    )

    # ----------------------------------------
    # ŠIFRAVIMO RAKTAS
    # ----------------------------------------

    # Vartotojo šifravimo raktas - užšifruotas sistemos master_key
    # Žiūrėti core/encryption.py - kaip generuojamas ir naudojamas
    # BLOB tipas - baitai (ne tekstas), nes Fernet raktas yra baitai
    # NULL = raktas dar nesugeneruotas (naujam vartotojui sukuriamas iš karto)
    encryption_key_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        comment="Vartotojo failų šifravimo raktas, užšifruotas sistemos master_key",
    )

    # ----------------------------------------
    # ROLĖ IR STATUSAS
    # ----------------------------------------

    # Ar vartotojas yra administratorius
    # Adminas gali: kurti/šalinti vartotojus, matyti statistiką, reset'inti 2FA
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True = administratorius, False = eilinis vartotojas",
    )

    # Ar vartotojas aktyvus - neaktyvus negali prisijungti
    # Geriau deaktyvuoti nei ištrinti (istoriniai duomenys išsaugomi)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True = aktyvus vartotojas, False = deaktyvuotas (negali prisijungti)",
    )

    # ----------------------------------------
    # DISKO VIETOS STEBĖJIMAS
    # ----------------------------------------

    # Kiek baitų vartotojas užima diske (visuose savo failuose)
    # Atnaujiname šitą lauką kiekvieno upload ir delete metu
    # Tai greitesnis sprendimas nei kiekvieną kartą skaičiuoti SUM(files.size_bytes)
    # Maksimumas: 2GB = 2 * 1024^3 = 2147483648 baitų
    # BigInteger - kad tilptų didelės reikšmės (Integer max ~2.1 mlrd, BigInteger ~9.2 mlrd)
    storage_used_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Bendras disko vietos naudojimas baitais (visų failų suma)",
    )

    # ----------------------------------------
    # LAIKO ŽYMĖS
    # ----------------------------------------

    # Kada vartotojas buvo sukurtas
    # timezone.utc - visada saugome UTC laiką (ne lokalų laiko juostą!)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="Vartotojo sukūrimo data ir laikas (UTC)",
    )

    # Paskutinis sėkmingas prisijungimas - rodoma admin panel'e
    # NULL = niekada neprisijungė
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Paskutinio prisijungimo data ir laikas (UTC), NULL = niekada",
    )

    # ----------------------------------------
    # RYŠIAI (relationships)
    # ----------------------------------------

    # Vartotojo aplankalai - visi Folder įrašai su šiuo user_id
    # back_populates="owner" - Folder modelyje turi būti atitinkamas relationship
    # cascade="all, delete-orphan" - ištrynus vartotoją, ištrinami ir jo aplankalai
    folders: Mapped[list["Folder"]] = relationship(
        "Folder",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="select",      # Neįkelia automatiškai, tik kai priklausiamas
    )

    # Vartotojo failai
    files: Mapped[list["File"]] = relationship(
        "File",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # Vartotojo aktyvios sesijos
    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ----------------------------------------
    # PAGALBINIAI METODAI
    # ----------------------------------------

    @property
    def storage_used_mb(self) -> float:
        """
        gauna: nieko (savybė)
        daro: konvertuoja storage_used_bytes į megabaitus
        grąžina: (float) - naudojama vieta MB (apvalinta 2 skaitmenimis po kablelio)
        """
        return round(self.storage_used_bytes / (1024 * 1024), 2)

    def __repr__(self) -> str:
        """
        gauna: nieko
        daro: sukuria žmoniškai skaitomą eilutę objekto reprezentacijai
              (naudojama debug'e, log'uose)
        grąžina: (str) - objekto tekstinis vaizdas
        """
        return (
            f"<User id={self.id} "
            f"username='{self.username}' "
            f"is_admin={self.is_admin} "
            f"is_active={self.is_active}>"
        )
