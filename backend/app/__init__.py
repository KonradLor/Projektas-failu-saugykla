"""
KonradVault backend aplikacijos paketas.

Šis paketas yra pagrindinė FastAPI aplikacijos vieta.
Jame yra:
    - main.py        → FastAPI aplikacijos paleidimo taškas
    - config.py      → Konfigūracija iš .env failo
    - database.py    → SQLAlchemy duomenų bazės nustatymai
    - models/        → SQLAlchemy ORM modeliai (lentelės)
    - schemas/       → Pydantic schemos (validacija)
    - api/           → REST API endpointai
    - core/          → Branduolinė logika (šifravimas, auth)
    - utils/         → Pagalbinės funkcijos
"""

# ============================================
# PAKETO METADUOMENYS
# ============================================

# Aplikacijos pavadinimas - rodomas Swagger dokumentacijoje, log'uose ir t.t.
__app_name__ = "KonradVault"

# Aplikacijos versija - SemVer formatas (MAJOR.MINOR.PATCH)
# MVP versija prasideda nuo 0.1.0 (vystoma)
__version__ = "0.1.0"

# Autoriaus informacija
__author__ = "Konradas"

# Trumpas aprašymas (naudojamas FastAPI metaduomenims)
__description__ = "Privati web-based failų saugykla su šifravimu ir 2FA"
