"""
Vartotojo (User) Pydantic schemos.

Schemos aprėpia:
    - AdminUserCreate   → admin kuria naują vartotoją per panel'ą
    - UserResponse      → vartotojo duomenys atsakyme (be slaptažodžio!)
    - UserUpdate        → admin keičia vartotojo statusą
    - UserStatsResponse → admin statistikos puslapis

SVARBUS SAUGUMO PRINCIPAS:
    UserResponse NIEKADA neturi:
        - password_hash
        - totp_secret
        - encryption_key_encrypted
    Šie laukai yra vidiniai – API vartotojas jų negauna.
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ============================================
# REQUEST SCHEMOS (duomenys NUO vartotojo)
# ============================================

class AdminUserCreate(BaseModel):
    """
    LEGACY schema – paliekama atgalinio suderinamumo dėliai.

    Šiuo metu admin panel'ė naudoja 2 žingsnių 2FA flow'ą per
    AdminUserCreateRequest + AdminUserConfirmRequest schemas (api/admin.py).
    Slaptažodis nebereikalingas – autentifikacija tik per TOTP.

    Šis modelis paliekamas, jei kada nors prireiks slaptažodžio kūrimo srauto
    (pvz. integracija su LDAP / kitu auth backend'u).
    """

    # Naujo vartotojo vardas
    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-z0-9_-]+$",  # Tik mažos raidės, skaičiai, _ ir -
        description="Vartotojo vardas (3-50 simbolių, tik mažos raidės/skaičiai/_/-)",
        examples=["jonas", "petras_99"],
    )

    # Pradinis slaptažodis – vartotojas turės jį pakeisti pirmą kartą prisijungus
    # (V2 funkcija – kol kas tiesiog siunčiama per Signal/kt.)
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Pradinis laikinas slaptažodis (min. 8 simboliai)",
    )

    # Ar naujam vartotojui suteikti admin teises
    # Default: False – eilinis vartotojas
    is_admin: bool = Field(
        default=False,
        description="True = suteikiamos admin teisės, False = eilinis vartotojas",
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """
        gauna: value (str) – username iš JSON
        daro: konvertuoja į mažąsias raides, tikrina ar nėra rezervuotų žodžių
        grąžina: (str) – patvirtintas username
        """
        value = value.strip().lower()

        # Rezervuoti vardai, kurių negalima naudoti
        reserved = {"admin", "root", "system", "konradvault", "api", "share"}
        if value in reserved:
            raise ValueError(
                f"Vartotojo vardas '{value}' yra rezervuotas sistemos reikmėms"
            )

        return value

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "jonas",
                "password": "laikinasPwd123",
                "is_admin": False,
            }
        }
    }


class UserUpdate(BaseModel):
    """
    Vartotojo duomenų atnaujinimas per admin panel'ą.

    Endpoint'as: PATCH /api/admin/users/{id}
    Naudojama tik admin.

    Visi laukai OPTIONAL (None = nekeisti).
    Galima siųsti tik tuos laukus, kuriuos norime keisti.
    """

    # Aktyvuoti arba deaktyvuoti vartotoją
    # None = nekeisti esamos reikšmės
    is_active: bool | None = Field(
        default=None,
        description="True = aktyvuoti, False = deaktyvuoti (None = nekeisti)",
    )

    # Ar keisti admin statusą
    is_admin: bool | None = Field(
        default=None,
        description="True = suteikti admin, False = atimti admin (None = nekeisti)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "is_active": False,
            }
        }
    }


# ============================================
# RESPONSE SCHEMOS (duomenys VARTOTOJUI)
# ============================================

class UserResponse(BaseModel):
    """
    Vartotojo duomenys atsakyme – SAUGUS variantas.

    Naudojamas:
        - GET /api/auth/me       → dabartinis vartotojas
        - GET /api/admin/users   → visų vartotojų sąrašas
        - POST /api/admin/users  → sukurto vartotojo info

    NĖRA: password_hash, totp_secret, encryption_key_encrypted
    """

    # Vartotojo identifikatorius
    id: int = Field(description="Vartotojo ID")

    # Vartotojo vardas
    username: str = Field(description="Prisijungimo vardas")

    # Rolė
    is_admin: bool = Field(description="True jei administratorius")

    # Statusas
    is_active: bool = Field(description="True jei aktyvus (gali prisijungti)")

    # Naudojama disko vieta baitais
    storage_used_bytes: int = Field(
        description="Naudojama disko vieta baitais"
    )

    # Aktyvių failų skaičius (užpildomas iškvietimo metu, ne ORM lauke)
    file_count: int = Field(
        default=0,
        description="Aktyvių (ne trash) failų skaičius",
    )

    # Sukūrimo data
    created_at: datetime = Field(description="Vartotojo sukūrimo data ir laikas (UTC)")

    # Paskutinis prisijungimas – None jei niekada
    last_login: datetime | None = Field(
        default=None,
        description="Paskutinio prisijungimo laikas (None = niekada neprisijungė)",
    )

    # ----------------------------------------
    # PYDANTIC KONFIGŪRACIJA
    # ----------------------------------------

    # from_attributes=True – leidžia kurti schemą iš SQLAlchemy modelio objekto
    # Naudojama: UserResponse.model_validate(user_db_object)
    model_config = {"from_attributes": True}

    # ----------------------------------------
    # PAPILDOMI APSKAIČIUOJAMI LAUKAI
    # ----------------------------------------

    @property
    def storage_used_mb(self) -> float:
        """
        gauna: nieko (savybė)
        daro: patogu rodyti MB vietoj baitų
        grąžina: (float) – naudojama vieta MB
        """
        return round(self.storage_used_bytes / (1024 * 1024), 2)


class AdminUserCreateResponse(BaseModel):
    """
    Atsakymas po naujo vartotojo sukūrimo per admin panel'ą.

    Papildomai prie vartotojo duomenų grąžinamas:
        - QR kodo URL (data URI arba base64) – admin nuskenuoja su Google Auth
        - Pradinis slaptažodis (rodomas TIK VIENĄ KARTĄ)
    """

    # Sukurto vartotojo duomenys
    user: UserResponse = Field(description="Sukurto vartotojo informacija")

    # QR kodo turinys – base64 PNG arba otpauth:// URI
    # Admin turi parodyti naujam vartotojui (per Signal/kt.)
    qr_code_base64: str = Field(
        description="QR kodo paveikslėlis base64 PNG formatu Google Authenticator'iui"
    )

    # TOTP URI – alternatyva QR kodui (galima rankiniu būdu įvesti į Auth app)
    totp_uri: str = Field(
        description="TOTP URI (otpauth://totp/...) rankiniam įvedimui"
    )

    # Pradinė slaptažodžio reikšmė – rodoma TIK VIENĄ KARTĄ
    # Po to neįmanoma jos sužinoti (tik hash'as DB)
    initial_password: str = Field(
        description="Pradinis slaptažodis – parodyti vartotojui per saugų kanalą"
    )

    model_config = {"from_attributes": True}


class UserStorageInfo(BaseModel):
    """
    Vieno vartotojo disko vietos naudojimo info admin overview vaizde.
    """

    id: int = Field(description="Vartotojo ID")
    username: str = Field(description="Vartotojo vardas")
    storage_used_bytes: int = Field(description="Naudojama disko vieta baitais")
    file_count: int = Field(description="Aktyvių failų skaičius")
    is_admin: bool = Field(default=False, description="Ar vartotojas administratorius")
    storage_limit_bytes: int = Field(
        default=0,
        description="Efektyvus disko vietos limitas baitais (adminui – labai didelis = neribota)",
    )
    transfer_used_bytes: int = Field(
        default=0, description="Šio mėnesio sunaudoto srauto kiekis baitais",
    )
    transfer_limit_bytes: int = Field(
        default=0, description="Efektyvus mėnesinio srauto limitas baitais",
    )

    model_config = {"from_attributes": True}


class UserStatsResponse(BaseModel):
    """
    Vartotojo statistika admin panel'e.

    Endpoint'as: GET /api/admin/stats
    Rodo bendrą sistemos statistiką.
    """

    # Bendras vartotojų skaičius
    total_users: int = Field(description="Bendras registruotų vartotojų skaičius")
    active_users: int = Field(description="Aktyvių (galinčių prisijungti) vartotojų skaičius")
    max_users: int = Field(description="Maksimalus leidžiamas vartotojų skaičius (iš konfigūracijos)")

    # Failų / aplankų statistika
    total_files: int = Field(description="Bendras aktyvių failų skaičius")
    total_folders: int = Field(description="Bendras aktyvių aplankų skaičius")
    total_uploads: int = Field(description="Bendras visų laikų upload'ų skaičius (su trash)")

    # Disko vietos statistika
    total_storage_bytes: int = Field(description="Bendras disko vietos naudojimas baitais")
    max_storage_bytes: int = Field(description="Maksimali leidžiama bendra disko vieta baitais")

    # Share linkų statistika
    total_share_links: int = Field(description="Bendras visų laikų share linkų skaičius")
    active_share_links: int = Field(description="Aktyvių (neišjungtų ir nepasibaigusių) share linkų skaičius")

    # Vartotojų vietos suvestinė (overview UI)
    users_storage: list[UserStorageInfo] = Field(
        default_factory=list,
        description="Disko vietos naudojimo eilutės kiekvienam vartotojui",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "total_users": 3,
                "active_users": 3,
                "max_users": 10,
                "total_files": 127,
                "total_folders": 12,
                "total_uploads": 245,
                "total_storage_bytes": 9021800448,
                "max_storage_bytes": 6442450944,
                "total_share_links": 8,
                "active_share_links": 3,
                "users_storage": [
                    {"id": 1, "username": "konradas", "storage_used_bytes": 3145728, "file_count": 42},
                ],
            }
        }
    }
