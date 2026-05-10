"""
Logging konfigūracijos modulis.

Nustato MINIMALŲ loggingą – logginama tik tai, kas svarbu:
    - auth.log  → prisijungimo bandymai (sėkmingi ir nesėkmingi) su IP
    - error.log → kritinės klaidos (500 HTTP errors, DB klaidos)
    - Konsolė   → viskas (dev aplinkoje patogiam stebėjimui)

NELOGGINAMA (triukšmas, nereikalinga demo projektui):
    - Kiekvienas API iškvietimas
    - Failų upload'ai ir download'ai
    - Aplankalų operacijos
    - Sėkmingi autorizuoti veiksmai

NAUDOJIMAS kituose moduliuose:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Pranešimas")
    logger.warning("Įspėjimas")
    logger.error("Klaida")

KONFIGŪRACIJA inicializuojama main.py lifespan event'e per:
    from app.core.logging_config import setup_logging
    setup_logging()
"""

# ============================================
# IMPORTAI
# ============================================
import logging
import logging.handlers
import sys
from pathlib import Path

from app.config import settings


# ============================================
# LOG FORMATAI
# ============================================

# Pilnas formatas – failų log'ui (detalesnė informacija)
# Pavyzdys: 2026-05-07 14:23:01 | WARNING  | app.api.auth | Failed login | IP: 1.2.3.4
DETAILED_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)

# Trumpas formatas – konsolei (dev aplinkoje lengviau skaityti)
# Pavyzdys: 14:23:01 | WARNING | Failed login attempt
CONSOLE_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(message)s"
)

# Data ir laiko formatas log įrašuose
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ============================================
# KONFIGŪRACIJOS FUNKCIJA
# ============================================

def setup_logging() -> None:
    """
    gauna: nieko
    daro: sukonfiguruoja visą Python logging sistemą:
          - Konsolės handler'is (visada)
          - Failų handler'iai (auth.log ir error.log)
          - Tinkamus log lygius kiekvienam handler'iui
          - Išjungia triukšmingus trečių šalių loggerius
    grąžina: None

    KVIEČIAMA: main.py lifespan startup event'e (vieną kartą)
    """

    # ----------------------------------------
    # 1. PAGRINDINIS (root) LOGGER
    # ----------------------------------------

    # Gauname root logger'į – nuo jo paveldi visi kiti loggeriai
    root_logger = logging.getLogger()

    # Nustatome minimalų lygį – DEBUG dev'ui, INFO production'e
    # WARNING ir aukščiau visada praeis, nepriklausomai nuo šito
    root_logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Pašaliname visus esamus handler'ius (jei buvo nustatyti anksčiau)
    root_logger.handlers.clear()

    # ----------------------------------------
    # 2. KONSOLĖS HANDLER'IS
    # ----------------------------------------

    console_handler = logging.StreamHandler(sys.stdout)

    # Konsolėje rodome DEBUG lygį dev'ui, INFO production'e
    console_handler.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Konsolei – trumpesnis formatas (lengviau skaityti realiu laiku)
    console_formatter = logging.Formatter(
        fmt=CONSOLE_FORMAT,
        datefmt=DATE_FORMAT,
    )
    console_handler.setFormatter(console_formatter)

    root_logger.addHandler(console_handler)

    # ----------------------------------------
    # 3. AUTH LOG FAILAS (prisijungimo įvykiai)
    # ----------------------------------------

    auth_log_path = settings.log_dir / "auth.log"

    # RotatingFileHandler – kai failas pasiekia 5MB, sukuria naują
    # backupCount=5 → saugo 5 senesnius failus (auth.log.1, auth.log.2 ...)
    auth_handler = logging.handlers.RotatingFileHandler(
        filename=auth_log_path,
        maxBytes=5 * 1024 * 1024,   # 5MB
        backupCount=5,
        encoding="utf-8",
    )

    # Auth log'e saugome INFO ir aukščiau (prisijungimai, atsijungimai)
    auth_handler.setLevel(logging.INFO)

    auth_formatter = logging.Formatter(
        fmt=DETAILED_FORMAT,
        datefmt=DATE_FORMAT,
    )
    auth_handler.setFormatter(auth_formatter)

    # Auth log'as skirtas TIK auth moduliui
    auth_logger = logging.getLogger("app.api.auth")
    auth_logger.addHandler(auth_handler)

    # ----------------------------------------
    # 4. ERROR LOG FAILAS (kritinės klaidos)
    # ----------------------------------------

    error_log_path = settings.log_dir / "error.log"

    error_handler = logging.handlers.RotatingFileHandler(
        filename=error_log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=3,
        encoding="utf-8",
    )

    # Error log'e saugome tik WARNING ir aukščiau (klaidos, kritiniai įvykiai)
    error_handler.setLevel(logging.WARNING)

    error_formatter = logging.Formatter(
        fmt=DETAILED_FORMAT,
        datefmt=DATE_FORMAT,
    )
    error_handler.setFormatter(error_formatter)

    # Error handler'is priskirtas root logger'iui – gaus visų modulių klaidas
    root_logger.addHandler(error_handler)

    # ----------------------------------------
    # 5. TRIUKŠMINGŲ TREČIŲJŲ ŠALIŲ LOGGER'IŲ NUTILDYMAS
    # ----------------------------------------

    # SQLAlchemy echo lygis – net jei database_echo=False,
    # kai kurie SQLAlchemy loggeriai vis tiek siunčia daug žinučių
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)

    # Uvicorn access log'as – kiekviena HTTP užklausa (per daug triukšmo)
    # Išjungiame access log'ą, paliekame tik error log'ą
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    # Multipart (failų upload biblioteka) – per daug debug žinučių
    logging.getLogger("multipart").setLevel(logging.WARNING)

    # ----------------------------------------
    # 6. PATVIRTINIMAS
    # ----------------------------------------

    startup_logger = logging.getLogger(__name__)
    startup_logger.info(
        f"Logging sukonfiguruotas | "
        f"Lygis: {'DEBUG' if settings.debug else 'INFO'} | "
        f"Auth log: {auth_log_path} | "
        f"Error log: {error_log_path}"
    )
