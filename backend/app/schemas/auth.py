"""
Autentifikacijos Pydantic schemos.

Apima visą 2 žingsnių prisijungimo srautą:
    1 žingsnis → LoginRequest  → (sėkmė) → TempTokenResponse
    2 žingsnis → TwoFARequest  → (sėkmė) → TokenResponse (session cookie)

PASTABA apie session token:
    Sėkmingos 2FA verifikacijos metu session token NEGRĄŽINAMAS JSON body'je –
    jis nustatomas kaip HTTP-only cookie serverio pusėje.
    TokenResponse grąžina tik vartotojo informaciją, ne patį token'ą.
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ============================================
# REQUEST SCHEMOS (duomenys NUO vartotojo)
# ============================================

class LoginRequest(BaseModel):
    """
    Prisijungimo formos duomenys – pirmasis žingsnis.

    Vartotojas siunčia: username + password
    Endpoint'as: POST /api/auth/login
    """

    # Vartotojo vardas – tikrinamas ar egzistuoja DB
    # strip() – pašaliname tarpus iš pradžios/pabaigos (dažna vartotojo klaida)
    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Vartotojo prisijungimo vardas",
        examples=["konradas"],
    )

    # Slaptažodis – perduodamas Argon2 verifikacijai
    # Minimalus ilgis 8 – UI pusėje bus tikrinama stiprumas
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,     # Apsauga nuo labai ilgų Argon2 DoS atakų
        description="Vartotojo slaptažodis (min. 8 simboliai)",
    )

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        """
        gauna: value (str) – username iš JSON
        daro: pašalina tarpus iš pradžios ir pabaigos,
              konvertuoja į mažąsias raides (login case-insensitive)
        grąžina: (str) – sutvarkytas username
        """
        return value.strip().lower()

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "konradas",
                "password": "stiprusSlaptazodis123",
            }
        }
    }


class TwoFARequest(BaseModel):
    """
    2FA verifikacijos duomenys – antrasis žingsnis.

    Vartotojas siunčia: temp_token (gautas iš 1 žingsnio) + TOTP kodas
    Endpoint'as: POST /api/auth/verify-2fa
    """

    # Laikinas token'as – gautas iš POST /api/auth/login atsakymo
    # Galioja tik 5 minutes (konfigūruojama settings.temp_token_expire_minutes)
    temp_token: str = Field(
        ...,
        min_length=10,
        description="Laikinas token'as iš pirmojo prisijungimo žingsnio",
    )

    # 6 skaitmenų TOTP kodas iš Google Authenticator
    # Tikrinamas su pyotp.TOTP(secret).verify(code)
    totp_code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",     # Tik 6 skaitmenys, jokie kiti simboliai
        description="6 skaitmenų kodas iš Google Authenticator",
        examples=["123456"],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "temp_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "totp_code": "123456",
            }
        }
    }


# ============================================
# RESPONSE SCHEMOS (duomenys VARTOTOJUI)
# ============================================

class TempTokenResponse(BaseModel):
    """
    Atsakymas po sėkmingo 1 žingsnio (password patikrinimo).

    Grąžinamas temp_token, kurį vartotojas turi siųsti 2 žingsnyje.
    Endpoint'as: POST /api/auth/login → šis atsakymas
    """

    # Laikinas token'as 2FA žingsniui
    # Saugomas memory'je arba Redis'e (ne DB) su TTL
    temp_token: str = Field(
        description="Laikinas token'as 2FA žingsniui (galioja 5 min.)"
    )

    # Kada baigiasi – vartotojui rodyti laikmatį UI'je
    expires_in_seconds: int = Field(
        description="Kiek sekundžių galioja temp_token"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "temp_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "expires_in_seconds": 300,
            }
        }
    }


class TokenResponse(BaseModel):
    """
    Atsakymas po sėkmingo viso prisijungimo (password + 2FA).

    Session token NĖRA šiame response – jis nustatomas kaip HTTP-only
    cookie serverio pusėje (response.set_cookie).
    Grąžinama tik vartotojo informacija redirect'ui.
    """

    # Prisijungusio vartotojo id – naudojamas frontend'e užklausoms
    user_id: int = Field(description="Prisijungusio vartotojo ID")

    # Vartotojo vardas – rodomas UI header'e
    username: str = Field(description="Prisijungusio vartotojo vardas")

    # Ar administratorius – UI sprendžia ar rodyti Admin nuorodą
    is_admin: bool = Field(description="True jei vartotojas turi admin teises")

    # Pranešimas sėkmingam login'ui
    message: str = Field(
        default="Prisijungta sėkmingai",
        description="Sėkmingo prisijungimo pranešimas",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "user_id": 1,
                "username": "konradas",
                "is_admin": True,
                "message": "Prisijungta sėkmingai",
            }
        }
    }


class LogoutResponse(BaseModel):
    """
    Atsakymas po atsijungimo.
    Endpoint'as: POST /api/auth/logout
    """

    message: str = Field(
        default="Atsijungta sėkmingai",
        description="Atsijungimo patvirtinimo pranešimas",
    )
