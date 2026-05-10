"""
Pagalbinės funkcijos, naudojamos keliuose API moduliuose.

TIKSLAS:
    Apsaugoti nuo kodo dubliavimosi tarp api/files.py, api/share.py,
    api/folders.py, api/search.py ir kitų vietų.

VEIKIA SU:
    - Vartotojo šifravimo raktais (decrypt_user_key)
    - Aplankų atsakymo schemomis (FolderResponse + file_count)
"""

# ============================================
# IMPORTAI
# ============================================
import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.encryption import decrypt_user_key
from app.models.file import File as FileModel
from app.models.folder import Folder
from app.models.user import User
from app.schemas.folder import FolderResponse


# ============================================
# LOGGER
# ============================================
logger = logging.getLogger(__name__)


# ============================================
# VARTOTOJO ŠIFRAVIMO RAKTAS
# ============================================

def get_user_decryption_key(user: User) -> bytes:
    """
    gauna: user (User) – vartotojo ORM objektas
    daro: dešifruoja vartotojo šifravimo raktą naudojant MASTER_KEY.
          Kviečiama kiekvieno upload/download metu.
    grąžina: (bytes) – plaintext vartotojo šifravimo raktas
    iškelia: HTTPException 500 – jei rakto nėra arba nepavyko dešifruoti
    """
    if not user.encryption_key_encrypted:
        logger.error(f"Vartotojas {user.username} neturi šifravimo rakto!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Vartotojo šifravimo raktas nerastas. Kreipkitės į administratorių.",
        )

    try:
        return decrypt_user_key(user.encryption_key_encrypted, settings.master_key)
    except ValueError as exc:
        logger.critical(
            f"Nepavyko dešifruoti rakto vartotojui {user.username}: {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Šifravimo rakto klaida. Kreipkitės į administratorių.",
        ) from exc


# ============================================
# APLANKO RESPONSE SCHEMOS KŪRIMAS
# ============================================

def get_folder_file_count(folder_id: int, db: DBSession) -> int:
    """
    gauna: folder_id (int)      – aplanko ID
           db        (DBSession) – DB sesija
    daro: suskaičiuoja kiek aktyvių (ne ištrintų) failų yra šiame aplanke.
    grąžina: (int) – failų skaičius
    """
    return db.query(func.count(FileModel.id)).filter(
        FileModel.folder_id == folder_id,
        FileModel.is_deleted == False,  # noqa: E712
    ).scalar() or 0


def folder_to_response(folder: Folder, db: DBSession) -> FolderResponse:
    """
    gauna: folder (Folder)    – aplanko ORM objektas
           db     (DBSession) – DB sesija
    daro: konvertuoja Folder ORM į FolderResponse Pydantic schemą,
          papildomai užpildo file_count lauką iš DB.

          Šis helper'is naudojamas folders.py ir search.py, kad
          būtų išvengta kodo dubliavimosi.
    grąžina: (FolderResponse) – atsakymo schema su file_count
    """
    return FolderResponse(
        id=folder.id,
        user_id=folder.user_id,
        parent_id=folder.parent_id,
        name=folder.name,
        color=folder.color,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
        is_deleted=folder.is_deleted,
        deleted_at=folder.deleted_at,
        file_count=get_folder_file_count(folder.id, db),
    )
