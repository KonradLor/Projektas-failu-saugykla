"""
Alembic migracijos aplinkos failas.

Šis failas kviečiamas kiekvieną kartą vykdant alembic komandą.
Jo užduotis – sujungti Alembic su mūsų SQLAlchemy modeliais ir DB URL.

DU VEIKIMO REŽIMAI:
    1. offline – generuoja SQL skriptą (be realaus DB ryšio)
    2. online  – jungiasi prie DB ir vykdo migraciją tiesiogiai
"""

# ============================================
# IMPORTAI
# ============================================
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ----------------------------------------
# SVARBU: Pridedame backend/ į Python kelią,
# kad galėtume importuoti app modulius
# ----------------------------------------
# migrations/env.py yra backend/migrations/env.py
# Norime importuoti iš backend/app/ → reikia backend/ kelyje
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importuojame mūsų konfigūraciją ir modelius
from app.config import settings          # DB URL ir kiti nustatymai
from app.database import Base            # SQLAlchemy deklaratyvi bazė

# Importuojame VISUS modelius – Alembic turi juos "matyti" autogenerate metu
# Jei pridėsi naują modelį, privaloma jį čia importuoti!
from app.models import (                 # noqa: F401 – importas reikalingas šalutiniam poveikiui
    File,
    Folder,
    Session,
    ShareLink,
    User,
)

# ============================================
# ALEMBIC KONFIGŪRACIJA
# ============================================

# Alembic Config objektas – prieiga prie alembic.ini reikšmių
config = context.config

# Nustatome DB URL iš mūsų konfigūracijos (perrašome alembic.ini reikšmę)
# Tai užtikrina, kad migracija naudoja TĄ PATĮ URL kaip ir aplikacija
config.set_main_option("sqlalchemy.url", settings.database_url)

# Nustatome Python logging konfigūraciją iš alembic.ini [loggers] sekcijos
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metaduomenys iš mūsų Base – Alembic naudoja juos autogenerate metu
# (lygina DB schemą su modeliais ir generuoja reikalingus ALTER TABLE ir kt.)
target_metadata = Base.metadata


# ============================================
# OFFLINE MIGRACIJA (be DB ryšio)
# ============================================

def run_migrations_offline() -> None:
    """
    gauna: nieko
    daro: vykdo migraciją "offline" režime – generuoja SQL skriptą
          į stdout arba failą, NESIUNGDAMAS prie DB.
          Naudinga kai nori peržiūrėti SQL prieš vykdant.
    grąžina: None

    PALEIDIMAS: alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # compare_type=True → Alembic tikrins ir stulpelių tipų pakeitimus
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ============================================
# ONLINE MIGRACIJA (su DB ryšiu)
# ============================================

def run_migrations_online() -> None:
    """
    gauna: nieko
    daro: prisijungia prie DB ir vykdo migraciją tiesiogiai.
          Tai standartinis veikimo režimas (alembic upgrade head).
    grąžina: None
    """
    # Sukuriame engine iš alembic.ini konfigūracijos
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",

        # NullPool – Alembic naudoja tik vieną ryšį migracijos metu,
        # po to jį uždaro. Geriau nei connection pool'as vienkartiniam naudojimui.
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type=True → tikrina ir stulpelių tipų pakeitimus
            compare_type=True,
            # render_as_batch=True → SQLite nepalaikantis ALTER TABLE
            # Batch mode leidžia "pervadinti" stulpelius ir t.t. SQLite aplinkoje
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# ============================================
# REŽIMO NUSTATYMAS
# ============================================

# Alembic automatiškai nustato kurį režimą naudoti
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
