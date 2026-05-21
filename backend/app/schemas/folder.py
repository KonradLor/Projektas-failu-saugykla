"""
Aplanko (Folder) Pydantic schemos.

Aprėpia:
    - FolderCreate      → naujo aplanko kūrimas
    - FolderUpdate      → pervardijimas arba spalvos keitimas
    - FolderResponse    → aplanko duomenys atsakyme
    - FolderTreeResponse → visų aplankalų medžio struktūra

SPALVŲ VALIDACIJA:
    Leidžiamos tik 8 preset spalvos HEX formatu.
    Tai užtikrina vizualų vienodumą UI'je.
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ============================================
# KONSTANTOS
# ============================================

# Leidžiamos aplankalų spalvos – 8 preset reikšmės
# Atitinka UI spalvų pasirinkimo mygtukus
ALLOWED_FOLDER_COLORS = {
    "#3B82F6",   # Mėlyna  (default)
    "#22C55E",   # Žalia
    "#EF4444",   # Raudona
    "#EAB308",   # Geltona
    "#F97316",   # Oranžinė
    "#8B5CF6",   # Violetinė
    "#EC4899",   # Rožinė
    "#6B7280",   # Pilka
}


# ============================================
# REQUEST SCHEMOS (duomenys NUO vartotojo)
# ============================================

class FolderCreate(BaseModel):
    """
    Naujo aplanko kūrimo duomenys.

    Endpoint'as: POST /api/folders
    """

    # Aplanko pavadinimas – unikalumas netikrinamas DB lygiu
    # (galima turėti du "Darbas" skirtinguose aplankuose)
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Aplanko pavadinimas (1–255 simboliai)",
        examples=["Dokumentai", "2025 metų nuotraukos"],
    )

    # Tėvinis aplankas – None reiškia root lygis
    parent_id: int | None = Field(
        default=None,
        description="Tėvinio aplanko ID (None = root lygis)",
        examples=[None, 5],
    )

    # Spalva – validuojama pagal ALLOWED_FOLDER_COLORS
    color: str = Field(
        default="#3B82F6",
        description="Aplanko spalva HEX formatu (tik iš leistinų 8 spalvų)",
        examples=["#3B82F6", "#22C55E"],
    )

    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, value: str) -> str:
        """
        gauna: value (str) – aplanko pavadinimas iš JSON
        daro: pašalina tarpus iš kraštuose,
              tikrina ar pavadinimas nėra tuščias po strip'o
        grąžina: (str) – sutvarkytas pavadinimas
        """
        stripped = value.strip()

        if not stripped:
            raise ValueError("Aplanko pavadinimas negali būti tuščias arba tik tarpai")

        return stripped

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        """
        gauna: value (str) – spalva HEX formatu iš JSON
        daro: konvertuoja į didžiąsias raides ir tikrina ar spalva leistina
        grąžina: (str) – patvirtinta spalva didžiosiomis raidėmis
        """
        upper = value.strip().upper()

        if upper not in ALLOWED_FOLDER_COLORS:
            allowed = ", ".join(sorted(ALLOWED_FOLDER_COLORS))
            raise ValueError(
                f"Spalva '{value}' neleidžiama. "
                f"Leistinos spalvos: {allowed}"
            )

        return upper

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Dokumentai",
                "parent_id": None,
                "color": "#3B82F6",
            }
        }
    }


class FolderUpdate(BaseModel):
    """
    Aplanko atnaujinimo duomenys – pervardijimas arba spalvos keitimas.

    Endpoint'as: PATCH /api/folders/{id}
    Visi laukai optional – siųsk tik tuos, kuriuos keisi.
    """

    # Naujas pavadinimas – None reiškia nekeisti
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Naujas aplanko pavadinimas (None = nekeisti)",
    )

    # Nauja spalva – None reiškia nekeisti
    color: str | None = Field(
        default=None,
        description="Nauja aplanko spalva HEX formatu (None = nekeisti)",
    )

    # Naujas tėvinis aplankas (perkėlimas). None = nekeisti; <=0 = į šakninį.
    parent_id: int | None = Field(
        default=None,
        description="Naujas tėvinis aplankas (perkėlimas). None = nekeisti; <=0 = šakninis.",
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        """
        gauna: value (str | None) – naujas pavadinimas
        daro: jei ne None – pašalina tarpus ir patikrina tuštumą
        grąžina: (str | None) – sutvarkytas pavadinimas arba None
        """
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            raise ValueError("Aplanko pavadinimas negali būti tuščias")

        return stripped

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        """
        gauna: value (str | None) – nauja spalva
        daro: jei ne None – validuoja pagal leistinų spalvų sąrašą
        grąžina: (str | None) – patvirtinta spalva arba None
        """
        if value is None:
            return None

        upper = value.strip().upper()
        if upper not in ALLOWED_FOLDER_COLORS:
            allowed = ", ".join(sorted(ALLOWED_FOLDER_COLORS))
            raise ValueError(
                f"Spalva '{value}' neleidžiama. Leistinos spalvos: {allowed}"
            )

        return upper

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Naujas pavadinimas",
                "color": "#22C55E",
            }
        }
    }


# ============================================
# RESPONSE SCHEMOS (duomenys VARTOTOJUI)
# ============================================

class FolderResponse(BaseModel):
    """
    Aplanko duomenys atsakyme.

    Naudojamas visiems endpoint'ams, kurie grąžina aplanko informaciją.
    """

    # Unikalus identifikatorius
    id: int = Field(description="Aplanko ID")

    # Kuriam vartotojui priklauso
    user_id: int = Field(description="Savininko vartotojo ID")

    # Tėvinis aplankas (None = root)
    parent_id: int | None = Field(
        default=None,
        description="Tėvinio aplanko ID (None = root lygis)",
    )

    # Pavadinimas
    name: str = Field(description="Aplanko pavadinimas")

    # Spalva HEX
    color: str = Field(description="Aplanko spalva (#RRGGBB)")

    # Laiko žymės
    created_at: datetime = Field(description="Sukūrimo data ir laikas (UTC)")
    updated_at: datetime = Field(description="Paskutinio pakeitimo data ir laikas (UTC)")

    # Trash bin statusas
    is_deleted: bool = Field(description="True jei trash bin'e")
    deleted_at: datetime | None = Field(
        default=None,
        description="Trash perkelimo laikas (None jei nėra)",
    )

    # Failų skaičius šiame aplanke (apskaičiuojamas endpoint'e)
    # Nėra DB lauke – apskaičiuojama per query
    file_count: int = Field(
        default=0,
        description="Aktyvių failų skaičius šiame aplanke",
    )

    # Leidžia kurti iš SQLAlchemy modelio objekto
    model_config = {"from_attributes": True}


class FolderTreeResponse(BaseModel):
    """
    Aplanko medžio mazgas – naudojamas sidebar'o atvaizdavimui.

    Kiekvienas aplankas gali turėti vaikiniuose aplankalus (children),
    kurie irgi yra FolderTreeResponse – rekursinė struktūra.

    PAVYZDYS JSON:
        {
            "id": 1,
            "name": "Dokumentai",
            "color": "#3B82F6",
            "children": [
                {"id": 3, "name": "Darbas", "color": "#22C55E", "children": []},
                {"id": 4, "name": "Asmeninis", "color": "#EF4444", "children": []}
            ]
        }
    """

    # Pagrindiniai laukai
    id: int = Field(description="Aplanko ID")
    name: str = Field(description="Aplanko pavadinimas")
    color: str = Field(description="Aplanko spalva")
    parent_id: int | None = Field(default=None)

    # Failų skaičius (tik tiesiogiai šiame aplanke, ne rekursyviai)
    file_count: int = Field(default=0, description="Failų skaičius šiame aplanke")

    # Vaikiniai aplankalai – rekursinė struktūra
    # list["FolderTreeResponse"] – ciklinė nuoroda į save
    children: list["FolderTreeResponse"] = Field(
        default_factory=list,
        description="Vaikiniai aplankalai (gali būti tušti)",
    )

    model_config = {"from_attributes": True}


# Būtina po klasės apibrėžimo – Pydantic turi žinoti rekursinį tipą
FolderTreeResponse.model_rebuild()
