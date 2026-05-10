"""
FastAPI aplikacijos paleidimo taškas (entry point).

Šis failas:
    - Sukuria FastAPI aplikacijos objektą
    - Sukonfigūruoja middleware (CORS, security headers, request logging)
    - Užregistruoja API router'ius (/api/auth, /api/files ir t.t.)
    - Apibrėžia startup/shutdown event'us
    - Pateikia health check endpointą

PALEIDIMAS:
    Lokaliai (dev):     uvicorn app.main:app --reload
    Production:         per systemd service (žr. deployment/konradvault.service)

API DOKUMENTACIJA (Swagger UI):
    http://localhost:8000/docs       → Swagger UI
    http://localhost:8000/redoc      → ReDoc
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import __app_name__, __description__, __version__
from app.config import settings
from app.core.logging_config import setup_logging
from app.database import check_db_connection, init_db


# ============================================
# LOGGER
# ============================================

# Pagrindinis aplikacijos logger'is - naudojamas startup/shutdown įvykiams
logger = logging.getLogger(__name__)


# ============================================
# LIFESPAN (STARTUP / SHUTDOWN EVENTS)
# ============================================

@asynccontextmanager
async def lifespan(application: FastAPI):
    """
    gauna: application (FastAPI) - aplikacijos objektas (FastAPI automatiškai perduoda)
    daro: paleidimo metu (prieš yield) inicializuoja resursus,
          uždarymo metu (po yield) išvalo resursus.
          Tai modernus FastAPI būdas (vietoj senų @app.on_event("startup")).
    grąžina: yield - perduoda valdymą FastAPI

    STARTUP žingsniai:
        1. Logging konfigūracija
        2. DB inicializavimas (jei reikia)
        3. DB ryšio patikrinimas
        4. Encrypted files direktorijos egzistavimo užtikrinimas

    SHUTDOWN žingsniai:
        1. Pranešimas log'uose
    """
    # ============================================
    # STARTUP - aplikacijos paleidimas
    # ============================================
    # Logging turi būti pirmasis veiksmas – kad visi sekantys žingsniai būtų logginti
    setup_logging()

    logger.info("=" * 60)
    logger.info(f"Paleidžiama {__app_name__} v{__version__}")
    logger.info("=" * 60)

    # Pranešame apie konfigūraciją (be slaptų reikšmių!)
    logger.info(f"Debug rėžimas: {settings.debug}")
    logger.info(f"DB URL: {settings.database_url}")
    logger.info(f"Encrypted dir: {settings.encrypted_files_dir}")
    logger.info(f"Max failo dydis: {settings.max_file_size_mb} MB")
    logger.info(f"Max vietos vartotojui: {settings.max_storage_per_user_mb} MB")

    # Inicializuojame duomenų bazę (sukuriame lenteles jei jų nėra)
    # PASTABA: production'e geriau naudoti Alembic, bet dev'ui šitas patogiau
    try:
        init_db()
    except Exception as exc:
        logger.critical(f"DB inicializacija NEPAVYKO: {exc}")
        # Negalim paleisti aplikacijos be DB - mes exception
        raise

    # Patikriname ar DB tikrai pasiekiama
    if not check_db_connection():
        logger.critical("DB ryšio patikrinimas nepavyko!")
        raise RuntimeError("Negalim prisijungti prie DB")

    logger.info("DB ryšys veikia tinkamai")

    # Užtikrinam, kad encrypted failų direktorija egzistuoja
    # (config.py validatorius jau sukuria, bet dar kartą patikrinam)
    settings.encrypted_files_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Aplikacija paruošta priimti užklausas")

    # ============================================
    # YIELD - aplikacija veikia
    # ============================================
    yield

    # ============================================
    # SHUTDOWN - aplikacijos uždarymas
    # ============================================
    logger.info("Aplikacija išjungiama...")
    logger.info("Iki pasimatymo!")


# ============================================
# FASTAPI APLIKACIJOS SUKŪRIMAS
# ============================================

# Sukuriame FastAPI objektą su metaduomenimis (rodomi /docs puslapyje)
app = FastAPI(
    # Pavadinimas Swagger UI puslapyje
    title=__app_name__,

    # Aprašymas Swagger UI puslapyje
    description=__description__,

    # Versija Swagger UI puslapyje
    version=__version__,

    # OpenAPI specifikacijos URL (gali būti None jei nori išjungti)
    openapi_url="/api/openapi.json",

    # Swagger UI URL (None = išjungta)
    docs_url="/docs" if settings.debug else None,

    # ReDoc URL (None = išjungta)
    redoc_url="/redoc" if settings.debug else None,

    # Lifespan funkcija (startup/shutdown)
    lifespan=lifespan,
)


# ============================================
# MIDDLEWARE
# ============================================

# Trusted Host middleware - apsauga nuo Host header injection atakų
# Production'e leidžiame tik base_url host'ą (Oracle IP arba domenas)
# Debug rėžime – "*" (lokaliai testuojant)
def _build_allowed_hosts() -> list[str]:
    """
    gauna: nieko (skaito iš settings)
    daro: sudaro leistinų hostname'ų sąrašą iš base_url konfigūracijos.
          Debug rėžime grąžina "*" (visi host'ai leidžiami).
    grąžina: (list[str]) – leistini host'ai TrustedHostMiddleware'ui
    """
    if settings.debug:
        return ["*"]

    # Production: išgauname host'ą iš base_url
    from urllib.parse import urlparse
    parsed = urlparse(settings.base_url)
    host = parsed.hostname or "localhost"

    # Leidžiame Nginx proxy lokalų ryšį + tikrąjį hostname/IP
    # localhost ir 127.0.0.1 – kad health check'ai ir lokalūs scripts veiktų
    return [host, "localhost", "127.0.0.1"]


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_build_allowed_hosts(),
)

# CORS middleware - leidžia frontend'ui kalbėti su backend'u
# Mūsų atveju frontend ir backend yra tame pačiame domain'e (per Nginx),
# bet vis tiek geriau turėti nustatymus
app.add_middleware(
    CORSMiddleware,
    # Leistini origin'ai - production'e nurodyti konkrečius
    allow_origins=["*"] if settings.debug else [],
    # Ar leisti cookie'us cross-origin užklausose
    allow_credentials=True,
    # Leistini HTTP metodai
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    # Leistini headers
    allow_headers=["*"],
)


# ============================================
# CUSTOM MIDDLEWARE - SECURITY HEADERS
# ============================================

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    gauna: request (Request) - vartotojo HTTP užklausa
           call_next - funkcija, kuri kviečia kitą middleware/endpoint
    daro: po endpoint'o atsakymo prideda saugumo HTTP header'ius
          (apsauga nuo XSS, clickjacking, MIME sniffing ir t.t.)
    grąžina: (Response) - atsakymas su pridėtais header'iais
    """
    # Kviečiam endpoint'ą ir gaunam atsakymą
    response = await call_next(request)

    # X-Content-Type-Options - apsauga nuo MIME sniffing
    # Browser'is naudos tik tą Content-Type, kurį mes nurodėm
    response.headers["X-Content-Type-Options"] = "nosniff"

    # X-Frame-Options - apsauga nuo clickjacking
    # DENY = mūsų puslapis negali būti įdėtas į <iframe> kitame domain'e
    response.headers["X-Frame-Options"] = "DENY"

    # X-XSS-Protection - legacy, bet kai kurie senesni browser'iai vis dar naudoja
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Referrer-Policy - kontroliuoja Referer header'į išeinančiose nuorodose
    # strict-origin-when-cross-origin = pilnas URL same-origin'e, tik origin cross-origin'e
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Content-Security-Policy - apsauga nuo XSS
    # default-src 'self' = visi resursai tik iš mūsų domain'o
    # 'unsafe-inline' reikalingas Tailwind CSS ir Alpine.js (CDN)
    # cdn.tailwindcss.com + cdn.jsdelivr.net = Tailwind ir Alpine CDN
    if not settings.debug:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'"
        )

    return response


# ============================================
# GLOBAL EXCEPTION HANDLER
# ============================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    gauna: request (Request) - vartotojo užklausa
           exc (Exception) - bet kokia neaprėžta klaida
    daro: pagavus bet kokią klaidą, kurią endpoint'as nepavalgė,
          užloggina ją ir grąžina švarų JSON atsakymą (be stack trace).
          Apsauga nuo informacijos nutekėjimo (kitaip rodytų pilną Python klaidą).
    grąžina: (JSONResponse) - 500 klaida su saugiu pranešimu
    """
    # Logginame pilną klaidą su stack trace (TIK į log failą, ne vartotojui!)
    logger.exception(f"Neaprėžta klaida endpoint'e {request.url.path}: {exc}")

    # Vartotojui grąžiname tik saugų pranešimą
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Įvyko vidinė serverio klaida. Susisiekite su administratoriumi.",
        },
    )


# ============================================
# API ROUTERIŲ REGISTRAVIMAS
# ============================================

# Kai realizuosi atskirą router'į - ATKOMENTUOK atitinkamą eilutę.

from app.api import auth                                                    # REALIZUOTA
from app.api import folders                                                 # REALIZUOTA
from app.api import files                                                   # REALIZUOTA
from app.api import share                                                   # REALIZUOTA
from app.api import search                                                  # REALIZUOTA
from app.api import trash                                                   # REALIZUOTA
from app.api import admin                                                   # REALIZUOTA

app.include_router(auth.router,    prefix="/api/auth",    tags=["Auth"])
app.include_router(folders.router, prefix="/api/folders", tags=["Folders"])
app.include_router(files.router,   prefix="/api/files",   tags=["Files"])
app.include_router(share.router,   prefix="/api/share",   tags=["Share"])
app.include_router(search.router,  prefix="/api/search",  tags=["Search"])
app.include_router(trash.router,   prefix="/api/trash",   tags=["Trash"])
app.include_router(admin.router,   prefix="/api/admin",   tags=["Admin"])


# ============================================
# HEALTH CHECK ENDPOINT
# ============================================

@app.get("/api/health", tags=["Health"])
async def health_check():
    """
    gauna: nieko (HTTP GET)
    daro: tikrina ar aplikacija ir DB veikia.
          Naudojama monitoring sistemoms (uptime kuratoriams).
    grąžina: (dict) - JSON su statusu

    PAVYZDYS ATSAKYMAS:
        {
            "status": "ok",
            "app": "KonradVault",
            "version": "0.1.0",
            "database": "connected"
        }
    """
    # Tikrinam DB ryšį
    db_status = "connected" if check_db_connection() else "disconnected"

    # HTTP status kodas pagal DB būseną
    # Jei DB neveikia - 503 (Service Unavailable)
    overall_status = "ok" if db_status == "connected" else "degraded"

    return {
        "status": overall_status,
        "app": __app_name__,
        "version": __version__,
        "database": db_status,
    }


# ============================================
# ROOT ENDPOINT
# ============================================

@app.get("/", tags=["Root"])
async def root():
    """
    gauna: nieko
    daro: paprastas welcome endpoint'as. Production'e Nginx perima šitą URL
          ir rodo frontend'ą - šitas endpoint'as iškviečiamas tik jei kreipiamasi
          tiesiai į FastAPI (ne per Nginx).
    grąžina: (dict) - paprasta žinutė
    """
    return {
        "message": f"{__app_name__} API",
        "version": __version__,
        "docs": "/docs" if settings.debug else "Documentation disabled in production",
    }
