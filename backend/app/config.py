"""
Aplikacijos konfigūracijos modulis.

Naudoja pydantic-settings biblioteką - automatiškai nuskaito reikšmes
iš .env failo arba sistemos environment variables. Visos reikšmės
yra validuojamos pagal tipus (jei netinkamas tipas → klaida paleidimo metu).

Tai centralizuotas vienintelis vietas kur saugomi visi nustatymai.
NIEKADA nehardcodinti kelių, slaptažodžių ar URL'ų kituose failuose -
visada importuoti per: from app.config import settings
"""

# ============================================
# IMPORTAI
# ============================================
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================
# KONSTANTOS
# ============================================

# Bazinis projekto kelias - naudojamas santykiniams keliams konvertuoti į absoliučius
# __file__ rodo į šitą failą (config.py), .parent → app/, .parent → backend/
BASE_DIR = Path(__file__).resolve().parent.parent

# Numatytas .env failo kelias - production aplinkoje gali būti /etc/konradvault/.env
DEFAULT_ENV_FILE = BASE_DIR / ".env"


# ============================================
# NUSTATYMŲ KLASĖ
# ============================================
class Settings(BaseSettings):
    """
    Pagrindinė konfigūracijos klasė.

    Visi laukai automatiškai užpildomi iš .env failo arba environment variables.
    Jei reikšmės nėra ir nėra default - aplikacija nepasileis (validation error).

    Kintamųjų pavadinimai .env faile turi sutapti su laukų pavadinimais
    (case insensitive pagal numatymą).
    """

    # ----------------------------------------
    # APLIKACIJOS METADUOMENYS
    # ----------------------------------------

    # Aplikacijos pavadinimas - rodomas API dokumentacijoje
    app_name: str = "KonradVault"

    # Aplikacijos versija - SemVer
    app_version: str = "0.1.0"

    # Debug rėžimas - JOKIU BŪDU production'e netūrėtų būti True
    # Debug rėžime rodomi pilni stack trace'ai, įjungtas auto-reload ir t.t.
    debug: bool = False

    # ----------------------------------------
    # SAUGUMO NUSTATYMAI
    # ----------------------------------------

    # Master key - pagrindinis šifravimo raktas, 256-bit (Fernet formatas)
    # KRITINIS: jei prarandamas - VISI failai prarasti amžinai!
    # Generuojamas: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    master_key: str = Field(..., min_length=32)

    # Secret key - rezervuotas ateities funkcijoms (signed URLs, CSRF token'ai)
    # ŠIUO METU NENAUDOJAMAS faktiškai – sesijos token'ai yra atsitiktiniai
    # (secrets.token_urlsafe) ir NEPASIRAŠOMI; apsauga remiasi į HTTP-only
    # cookie + SameSite=Strict. Privalomas .env'e dėl ateities funkcijų.
    secret_key: str = Field(..., min_length=32)

    # ----------------------------------------
    # VIEŠAS BASE URL (share linkams)
    # ----------------------------------------

    # Pilnas serverio URL be trailing slash
    # Naudojama generuojant share linkų URL (ShareLinkResponse.share_url)
    # Production: https://<oracle-ip>  arba  https://yourdomain.com
    # Dev: http://localhost:8000
    base_url: str = "http://localhost:8000"

    # ----------------------------------------
    # DUOMENŲ BAZĖS NUSTATYMAI
    # ----------------------------------------

    # SQLAlchemy duomenų bazės URL
    # SQLite formatas: sqlite:///path/to/db.db (3 brūkšniai = absoliutus kelias)
    # Pavyzdys: sqlite:////var/konradvault/konradvault.db
    database_url: str = "sqlite:///./konradvault.db"

    # Ar SQL užklausas spausdinti į konsolę (debug'ui)
    # Production'e visada False - kitaip log'uose pilna SQL triukšmo
    database_echo: bool = False

    # ----------------------------------------
    # FAILŲ SAUGYKLOS NUSTATYMAI
    # ----------------------------------------

    # Direktorija, kur saugomi užšifruoti failai
    # Production: /var/konradvault/encrypted/
    # Lokaliai: ./encrypted/
    encrypted_files_dir: Path = Path("./encrypted")

    # Direktorija log failams
    # Production: /var/log/konradvault/
    log_dir: Path = Path("./logs")

    # Maksimalus failo dydis MB (taikoma upload'inant)
    # 500MB = 500 * 1024 * 1024 = 524288000 bytes
    max_file_size_mb: int = Field(default=500, gt=0, le=2048)

    # Maksimalus disko vietos kiekis vienam vartotojui (MB)
    # 2GB = 2048MB - apsauga nuo disk space išnaudojimo
    max_storage_per_user_mb: int = Field(default=2048, gt=0)

    # Chunk dydis šifravimui/dešifravimui (bytes)
    # 64KB = pakankamai mažas, kad netektų krauti viso failo į RAM
    # Bet ne per mažas, kad nesukeltų per daug I/O operacijų
    encryption_chunk_size: int = Field(default=65536, gt=0)

    # ----------------------------------------
    # AUTENTIFIKACIJOS NUSTATYMAI
    # ----------------------------------------

    # Session cookie galiojimo laikas valandomis
    # 24h = patogu vartotojui, bet ne per ilgai (saugumas)
    session_expire_hours: int = Field(default=24, gt=0, le=168)

    # Temp token (po password, prieš 2FA) galiojimo laikas minutėmis
    # 5 min - pakankamai laiko įvesti TOTP kodą, bet ne ilgiau
    temp_token_expire_minutes: int = Field(default=5, gt=0, le=30)

    # Maksimalus registruotų vartotojų skaičius sistemoje
    # Exclusive – tik 10 vietų (marketing urgency + resursų kontrolė)
    max_users: int = Field(default=10, gt=0)

    # Maksimalus prisijungimo bandymų skaičius prieš ban'ą
    # Naudojamas su Fail2ban
    max_login_attempts: int = Field(default=5, gt=0)

    # Argon2 hash parametrai - reikia balansuoti saugumą ir greitį
    # Ant ARM serverio reiktų testuoti - jei per lėta, sumažinti memory_cost
    argon2_time_cost: int = Field(default=3, gt=0)
    argon2_memory_cost: int = Field(default=65536, gt=0)  # 64MB
    argon2_parallelism: int = Field(default=4, gt=0)

    # ----------------------------------------
    # SERVERIO NUSTATYMAI
    # ----------------------------------------

    # Host adresas, kuriame klausosi Uvicorn
    # 127.0.0.1 = tik lokaliai (per Nginx proxy) - SAUGIAU
    # 0.0.0.0 = priimama iš bet kur (NEREIKIA jei naudojamas Nginx)
    server_host: str = "127.0.0.1"

    # Port'as, kuriame klausosi FastAPI
    # 8000 - standartinis FastAPI/Uvicorn port'as
    server_port: int = Field(default=8000, gt=0, le=65535)

    # ----------------------------------------
    # PYDANTIC NUSTATYMAI
    # ----------------------------------------

    # Pydantic-settings konfigūracija - kur ieškoti .env failo, kaip elgtis ir t.t.
    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),     # .env failo kelias
        env_file_encoding="utf-8",          # Encoding (svarbu jei yra ne-ASCII simbolių)
        case_sensitive=False,               # MASTER_KEY ir master_key yra tas pats
        extra="ignore",                     # Ignoruoti nežinomus kintamuosius .env faile
    )

    # ----------------------------------------
    # VALIDATORIAI
    # ----------------------------------------

    @field_validator("encrypted_files_dir", "log_dir")
    @classmethod
    def ensure_directory_exists(cls, value: Path) -> Path:
        """
        gauna: value (Path) - direktorijos kelias iš .env failo
        daro: konvertuoja į absoliutų kelią ir įsitikina, kad direktorija egzistuoja
              (jei ne - sukuria su mkdir parents=True)
        grąžina: (Path) - patikrintas absoliutus kelias
        """
        # Konvertuojame į absoliutų kelią (jei buvo santykinis)
        absolute_path = value.resolve()

        # Sukuriame direktoriją jei jos dar nėra
        # parents=True → sukurs ir tėvines direktorijas
        # exist_ok=True → nemes klaidos jei jau egzistuoja
        absolute_path.mkdir(parents=True, exist_ok=True)

        return absolute_path

    @field_validator("master_key", "secret_key")
    @classmethod
    def validate_key_not_default(cls, value: str) -> str:
        """
        gauna: value (str) - rakto reikšmė iš .env failo
        daro: tikrina ar raktas nėra žinoma "default" reikšmė
              (apsauga nuo žmogiškosios klaidos - kai pamiršti pakeisti)
        grąžina: (str) - patvirtinta rakto reikšmė
        """
        # Sąrašas žinomų netinkamų raktų - jei toks rastas, mesti klaidą
        forbidden_values = [
            "changeme",
            "your-secret-key-here",
            "default",
            "test",
            "12345",
        ]

        if value.lower().strip() in forbidden_values:
            raise ValueError(
                f"Master/secret key reikšmė atrodo nepatikima ('{value}'). "
                f"Sugeneruok naują raktą: "
                f"python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        return value

    # ----------------------------------------
    # PAGALBINĖS SAVYBĖS (computed properties)
    # ----------------------------------------

    @property
    def max_file_size_bytes(self) -> int:
        """
        gauna: nieko (savybė)
        daro: konvertuoja max_file_size_mb į bytes
        grąžina: (int) - max failo dydis bytes
        """
        return self.max_file_size_mb * 1024 * 1024

    @property
    def max_storage_per_user_bytes(self) -> int:
        """
        gauna: nieko (savybė)
        daro: konvertuoja max_storage_per_user_mb į bytes
        grąžina: (int) - max vietos vienam vartotojui bytes
        """
        return self.max_storage_per_user_mb * 1024 * 1024

    @property
    def session_expire_seconds(self) -> int:
        """
        gauna: nieko (savybė)
        daro: konvertuoja session_expire_hours į sekundes
              (naudinga cookie Max-Age atributui)
        grąžina: (int) - session galiojimo laikas sekundėmis
        """
        return self.session_expire_hours * 3600


# ============================================
# SINGLETON PATTERN - VIENA NUSTATYMŲ INSTANCIJA
# ============================================

@lru_cache()
def get_settings() -> Settings:
    """
    gauna: nieko
    daro: sukuria Settings objektą (perskaito .env) tik vieną kartą.
          Visi sekantys iškvietimai grąžina tą patį objektą iš cache.
          @lru_cache užtikrina, kad .env failas perskaitomas tik kartą.
    grąžina: (Settings) - aplikacijos nustatymų objektas
    """
    return Settings()


# ============================================
# GLOBALI INSTANCIJA
# ============================================

# Sukuriame globalų settings objektą - importuojamas iš kitų modulių taip:
#   from app.config import settings
#   print(settings.master_key)
settings = get_settings()
