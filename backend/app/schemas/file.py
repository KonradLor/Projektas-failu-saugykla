"""
Failo (File), ShareLink ir Trash Pydantic schemos.

Viename faile surinktos susijusios schemos:
    - FileResponse          → failo informacija atsakyme
    - FileUpdate            → failo pervadinimas arba perkėlimas
    - FileUploadResponse    → atsakymas po sėkmingo įkėlimo
    - ShareLinkCreate       → naujo share link'o kūrimas
    - ShareLinkResponse     → share link'o duomenys atsakyme
    - TrashItemResponse     → trash bin elemento (failo arba aplanko) info

PASTABA apie upload'ą:
    Failo įkėlimas naudoja multipart/form-data (ne JSON) –
    todėl nėra atskiros FileUploadRequest schemos.
    Validacija vyksta endpoint'e (žr. api/files.py).
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ============================================
# FAILO SCHEMOS
# ============================================

class FileResponse(BaseModel):
    """
    Failo metaduomenys atsakyme.

    Grąžinamas įvairiuose endpoint'uose:
        - GET /api/files?folder_id=X  → failų sąrašas aplanke
        - GET /api/files/{id}/...     → konkretus failas
        - POST /api/files/upload      → įkeltas failas

    NĖRA: stored_filename, encryption_iv, file_hash
    (tai vidiniai duomenys, vartotojui nereikalingi)
    """

    # Identifikatorius
    id: int = Field(description="Failo ID")

    # Priklausomybė
    user_id: int = Field(description="Savininko vartotojo ID")
    folder_id: int | None = Field(
        default=None,
        description="Aplanko ID (None = root lygis)",
    )

    # Failo pavadinimas (originalus, ne UUID)
    filename: str = Field(
        alias="original_filename",
        description="Failo pavadinimas kaip įkėlė vartotojas",
    )

    # MIME tipas
    mime_type: str | None = Field(
        default=None,
        description="MIME tipas (pvz. image/jpeg, application/pdf)",
    )

    # Dydis
    size_bytes: int = Field(description="Failo dydis baitais")

    # Laiko žymė
    created_at: datetime = Field(description="Įkėlimo data ir laikas (UTC)")

    # Trash statusas
    is_deleted: bool = Field(description="True jei trash bin'e")
    deleted_at: datetime | None = Field(
        default=None,
        description="Trash perkelimo laikas",
    )

    # ----------------------------------------
    # PYDANTIC KONFIGŪRACIJA
    # ----------------------------------------

    model_config = {
        # Leidžia kurti iš SQLAlchemy objekto
        "from_attributes": True,
        # Leidžia naudoti alias laukų pavadinimus (filename → original_filename)
        "populate_by_name": True,
    }

    # ----------------------------------------
    # APSKAIČIUOJAMI LAUKAI (properties)
    # ----------------------------------------

    @property
    def size_mb(self) -> float:
        """
        gauna: nieko (savybė)
        daro: konvertuoja size_bytes į MB rodymui vartotojui
        grąžina: (float) – dydis MB (2 skaitmenys po kablelio)
        """
        return round(self.size_bytes / (1024 * 1024), 2)

    @property
    def extension(self) -> str:
        """
        gauna: nieko (savybė)
        daro: ištraukia failo plėtinį iš pavadinimo
        grąžina: (str) – plėtinys mažosiomis (pvz. 'pdf', 'jpg', '')
        """
        if "." not in self.filename:
            return ""
        return self.filename.rsplit(".", 1)[-1].lower()

    @property
    def is_previewable(self) -> bool:
        """
        gauna: nieko (savybė)
        daro: tikrina ar failui galima generuoti preview
              (paveikslėliai, PDF, tekstiniai)
        grąžina: (bool) – True jei preview įmanomas
        """
        if not self.mime_type:
            return False

        # Previewable MIME tipai
        previewable_types = {
            "image/jpeg", "image/png", "image/gif",
            "image/webp", "image/svg+xml",
            "application/pdf",
            "text/plain", "text/markdown",
            "application/json",
        }
        return self.mime_type in previewable_types


class FileUpdate(BaseModel):
    """
    Failo atnaujinimo duomenys – pervadinimas arba perkėlimas.

    Endpoint'as: PATCH /api/files/{id}
    Visi laukai optional.
    """

    # Naujas pavadinimas (None = nekeisti)
    filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Naujas failo pavadinimas (None = nekeisti)",
    )

    # Perkėlimas į kitą aplanką (None = nekeisti vietos)
    # 0 arba specialus žymuo reiškia perkelti į root
    folder_id: int | None = Field(
        default=None,
        description="Naujo aplanko ID (None = nekeisti, -1 = perkelti į root)",
    )

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        """
        gauna: value (str | None) – naujas failo pavadinimas
        daro: tikrina ar pavadinimas neturi neleistinų simbolių
              (apsauga nuo path traversal bandymų)
        grąžina: (str | None) – patvirtintas pavadinimas
        """
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            raise ValueError("Failo pavadinimas negali būti tuščias")

        # Neleistini simboliai kelyje – apsauga nuo path traversal
        forbidden_chars = set("/\\:*?\"<>|")
        found = forbidden_chars.intersection(set(stripped))
        if found:
            raise ValueError(
                f"Failo pavadinime neleistini simboliai: {', '.join(sorted(found))}"
            )

        return stripped

    model_config = {
        "json_schema_extra": {
            "example": {
                "filename": "naujas_pavadinimas.pdf",
                "folder_id": 3,
            }
        }
    }


class FileUploadResponse(BaseModel):
    """
    Atsakymas po sėkmingo failo įkėlimo.

    Endpoint'as: POST /api/files/upload
    Grąžinamas iš karto po įkėlimo ir šifravimo.
    """

    # Sėkmės pranešimas
    message: str = Field(
        default="Failas įkeltas sėkmingai",
        description="Patvirtinimo pranešimas",
    )

    # Įkelto failo duomenys
    file: FileResponse = Field(description="Įkelto failo metaduomenys")

    # Kiek liko vietos vartotojui po šio upload'o
    storage_remaining_bytes: int = Field(
        description="Likusi laisva vartotojo disko vieta baitais"
    )

    model_config = {"from_attributes": True}


# ============================================
# SHARE LINK SCHEMOS
# ============================================

class ShareLinkCreate(BaseModel):
    """
    Naujo share link'o kūrimo duomenys.

    Endpoint'as: POST /api/share
    """

    # Failas, kurį norima dalinti
    file_id: int = Field(
        ...,
        description="Dalinamo failo ID",
        examples=[42],
    )

    # Kiek kartų leidžiama atsisiųsti – PRIVALOMAS
    # UI riboja 1–100, bet schema leidžia iki 1000
    max_downloads: int = Field(
        ...,
        ge=1,               # Bent 1 atsisiuntimas (ge = greater or equal)
        le=1000,            # Max 1000 (apsauga nuo šiukšlinimo)
        description="Maksimalus leidžiamas atsisiuntimų skaičius (1–1000)",
        examples=[10, 50],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_id": 42,
                "max_downloads": 10,
            }
        }
    }


class ShareLinkResponse(BaseModel):
    """
    Share link'o duomenys atsakyme.

    Naudojamas:
        - POST /api/share          → naujai sukurtas link'as
        - GET  /api/share/list     → vartotojo share linkų sąrašas
        - GET  /api/share/{token}/info → viešas info (be autentifikacijos)
    """

    # Identifikatorius (naudojamas ištrinant per DELETE /api/share/{id})
    id: int = Field(description="Share link'o ID")

    # Failas, kurį dalijama
    file_id: int = Field(description="Dalinamo failo ID")

    # Failo pavadinimas – patogumui (nereikia papildomos užklausos)
    filename: str = Field(description="Dalinamo failo pavadinimas")

    # Pilnas share URL – frontend'as gali iš karto kopijuoti
    share_url: str = Field(
        description="Pilnas share link'o URL (http://<ip>/share/<token>)"
    )

    # Atsisiuntimų statistika
    download_count: int = Field(description="Kiek kartų jau atsisiųsta")
    max_downloads: int = Field(description="Maksimalus leidžiamas atsisiuntimų skaičius")
    downloads_remaining: int = Field(description="Kiek dar leidžiama atsisiųsti")

    # Statusas
    is_disabled: bool = Field(description="True jei link'as išjungtas")
    is_active: bool = Field(description="True jei link'as dar aktyvus")

    # Sukūrimo laikas
    created_at: datetime = Field(description="Share link'o sukūrimo laikas (UTC)")

    model_config = {"from_attributes": True}


# ============================================
# TRASH BIN SCHEMOS
# ============================================

class TrashItemResponse(BaseModel):
    """
    Trash bin elemento duomenys atsakyme.

    Trash'e gali būti ir failai, ir aplankalai – unified response.

    Endpoint'as: GET /api/trash
    """

    # Elemento identifikatorius DB'je
    id: int = Field(description="Elemento ID (failo arba aplanko)")

    # Elemento tipas – frontend'as žino ar rodyti FileIcon ar FolderIcon
    item_type: Literal["file", "folder"] = Field(
        description="Elemento tipas: 'file' arba 'folder'"
    )

    # Pavadinimas (failo – original_filename, aplanko – name)
    name: str = Field(description="Elemento pavadinimas")

    # Kada buvo perkeltas į trash
    deleted_at: datetime = Field(
        description="Kada buvo perkeltas į trash (UTC)"
    )

    # Papildoma informacija failams
    size_bytes: int | None = Field(
        default=None,
        description="Failo dydis baitais (None jei aplankas)",
    )

    # Papildoma informacija aplankalams
    color: str | None = Field(
        default=None,
        description="Aplanko spalva (None jei failas)",
    )

    model_config = {"from_attributes": True}


# ============================================
# SĄRAŠO ATSAKYMAI
# ============================================

class FileListResponse(BaseModel):
    """
    Failų sąrašo atsakymas.

    Endpoint'as: GET /api/files
    Apgaubia failų sąrašą ir bendrą kiekį (paginacijai ateityje).
    """

    files: list[FileResponse] = Field(description="Failų sąrašas")
    total: int = Field(description="Bendras failų kiekis šiame aplanke")

    model_config = {"from_attributes": True}
