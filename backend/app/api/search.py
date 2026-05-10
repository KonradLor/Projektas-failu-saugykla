"""
Paieškos API – failų ir aplankų pavadinimų paieška.

ENDPOINT'AI:
    GET /api/search?q=...         – ieško failų ir aplankų pagal pavadinimą

PAIEŠKA:
    - LIKE užklausa su % abiejose pusėse → „substring" paieška
    - SQLite LIKE pagal nutylėjimą case-insensitive ASCII simboliams
    - Ieškoma: tik aktyvūs failai (is_deleted=False) ir aplankai
    - Rezultatai rikiuojami: pirma aplankai, paskui failai (abejose –
      pagal pavadinimą abėcėlinę tvarka)
    - Grąžinamas bendras rezultatų skaičius ir ribojamas puslapis

PARAMETRAI:
    q           – paieškos frazė (1–200 simbolių, būtinas)
    type        – „all" | „files" | „folders" (default: „all")
    folder_id   – ieškoti tik šiame aplanke (None = visur)
    limit       – maks. rezultatų skaičius (1–100, default: 50)
    offset      – praleisti N rezultatų (paginacijai, default: 0)
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session as DBSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.file import File as FileModel
from app.models.folder import Folder
from app.models.user import User
from app.schemas.file import FileResponse
from app.schemas.folder import FolderResponse
from app.utils.api_helpers import folder_to_response as _folder_to_response


# ============================================
# LOGGER
# ============================================
logger = logging.getLogger(__name__)


# ============================================
# ROUTER
# ============================================
router = APIRouter()


# ============================================
# ATSAKYMO SCHEMOS
# ============================================

class SearchFileResult(BaseModel):
    """
    Vienas failas paieškos rezultatuose.
    Papildo FileResponse su „match" kontekstu.
    """

    item_type: Literal["file"] = Field(
        default="file",
        description="Elemento tipas – visada 'file'",
    )
    data: FileResponse = Field(description="Failo metaduomenys")

    model_config = {"from_attributes": True}


class SearchFolderResult(BaseModel):
    """
    Vienas aplankas paieškos rezultatuose.
    """

    item_type: Literal["folder"] = Field(
        default="folder",
        description="Elemento tipas – visada 'folder'",
    )
    data: FolderResponse = Field(description="Aplanko metaduomenys")

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    """
    Paieškos rezultatų atsakymas.

    Endpoint'as: GET /api/search
    Grąžina sujungtą failų ir aplankų sąrašą.
    """

    query: str = Field(description="Paieškos frazė kurią siuntė klientas")
    total_files: int   = Field(description="Rasta failų (prieš limit)")
    total_folders: int = Field(description="Rasta aplankų (prieš limit)")
    total: int         = Field(description="Bendras rezultatų skaičius (prieš limit)")

    files: list[FileResponse]     = Field(description="Rasti failai")
    folders: list[FolderResponse] = Field(description="Rasti aplankai")

    model_config = {"from_attributes": True}


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================
# _folder_to_response importuotas iš app/utils/api_helpers.py
# (bendras helper'is folders.py ir search.py)


# ============================================
# ENDPOINT'AI
# ============================================

@router.get(
    "",
    response_model=SearchResponse,
    summary="Ieškoti failų ir aplankų pagal pavadinimą",
)
def search(
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=200,
            description="Paieškos frazė (dalinė atitiktis pavadinime)",
        ),
    ],
    type: Annotated[
        Literal["all", "files", "folders"],
        Query(description="Kokio tipo rezultatus grąžinti"),
    ] = "all",
    folder_id: Annotated[
        Optional[int],
        Query(description="Ieškoti tik šiame aplanke (None = visur)"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maks. rezultatų skaičius (1–100)"),
    ] = 50,
    offset: Annotated[
        int,
        Query(ge=0, description="Praleisti N rezultatų (paginacijai)"),
    ] = 0,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """
    gauna: q          – paieškos frazė (substring)
           type       – „all" | „files" | „folders"
           folder_id  – ieškoti tik šiame aplanke (None = visur)
           limit      – maks. grąžinamų rezultatų skaičius
           offset     – nuo kurio rezultato pradėti (paginacija)
    daro:  LIKE paieška vartotojo failuose ir aplankuose pagal pavadinimą.
           Netraukia ištrintų elementų (trash bin).
           Rezultatai rikiuojami: aplankai pirma, paskui failai, abejose – abėcėliškai.
    grąžina: (SearchResponse) – rasti failai ir aplankai su skaičiais
    """
    # Paieškos šablonas – % abiejose pusėse (substring match)
    # strip() pašalina tarpus kraštinėse – pvz. "  pdf  " → "pdf"
    pattern = f"%{q.strip()}%"

    # ----------------------------------------
    # FAILŲ PAIEŠKA
    # ----------------------------------------
    found_files: list[FileModel] = []
    total_files = 0

    if type in ("all", "files"):
        files_query = (
            db.query(FileModel)
            .filter(
                FileModel.user_id == current_user.id,
                FileModel.is_deleted.is_(False),
                FileModel.original_filename.ilike(pattern),
            )
        )

        # Jei nurodytas aplankas – filtruoti pagal jį
        if folder_id is not None:
            files_query = files_query.filter(FileModel.folder_id == folder_id)

        # Skaičiuojame bendrą kiekį (prieš limit/offset)
        total_files = files_query.count()

        # Rikiuojame ir taikome puslapiavimą
        found_files = (
            files_query
            .order_by(FileModel.original_filename.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    # ----------------------------------------
    # APLANKŲ PAIEŠKA
    # ----------------------------------------
    found_folders: list[Folder] = []
    total_folders = 0

    if type in ("all", "folders"):
        folders_query = (
            db.query(Folder)
            .filter(
                Folder.user_id == current_user.id,
                Folder.is_deleted.is_(False),
                Folder.name.ilike(pattern),
            )
        )

        # Jei nurodytas aplankas – ieškoti tik jo tiesioginiuose vaikuose
        if folder_id is not None:
            folders_query = folders_query.filter(Folder.parent_id == folder_id)

        # Skaičiuojame bendrą kiekį (prieš limit/offset)
        total_folders = folders_query.count()

        # Kai type="all" – aplankams pritaikome mažesnį limitą,
        # kad liktų vietos failams (50/50 padalijimas)
        folder_limit = limit if type == "folders" else max(1, limit // 2)

        found_folders = (
            folders_query
            .order_by(Folder.name.asc())
            .offset(offset)
            .limit(folder_limit)
            .all()
        )

    # ----------------------------------------
    # KONVERTAVIMAS Į SCHEMAS
    # ----------------------------------------
    file_schemas = [
        FileResponse.model_validate(f) for f in found_files
    ]

    folder_schemas = [
        _folder_to_response(folder, db) for folder in found_folders
    ]

    # ----------------------------------------
    # ATSAKYMAS
    # ----------------------------------------
    total = total_files + total_folders

    logger.debug(
        "Paieška '%s' (type=%s, folder_id=%s): rasta %d failų, %d aplankų",
        q, type, folder_id, total_files, total_folders,
    )

    return SearchResponse(
        query=q.strip(),
        total_files=total_files,
        total_folders=total_folders,
        total=total,
        files=file_schemas,
        folders=folder_schemas,
    )
