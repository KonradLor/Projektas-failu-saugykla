"""
Šiukšlinės API – ištryntų failų ir aplankų valdymas.

ENDPOINT'AI:
    GET    /api/trash                  – šiukšlinės turinys (failai + aplankalai)
    DELETE /api/trash/files/{id}       – galutinai ištrinti failą
    DELETE /api/trash/folders/{id}     – galutinai ištrinti aplanką
    DELETE /api/trash/empty            – išvalyti visą šiukšlinę

SOFT DELETE vs GALUTINIS TRYNIMAS:
    Soft delete (api/files.py DELETE, api/folders.py DELETE):
        → is_deleted=True, failas LIEKA diske ir DB
        → vartotojas mato jį šiukšlinėje

    Galutinis trynimas (šis failas):
        → failai IŠTRINAMI iš disko (delete_encrypted_file)
        → DB įrašai IŠTRINAMI
        → storage_used_bytes sumažinamas

VAIKINIAI APLANKALAI:
    Galutinai trinant aplanką → rekursyviai ištrinami visi vaikiniai aplankalai
    ir VISI failai juose (iš disko ir DB). storage_used_bytes atnaujinamas.
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.file import File as FileModel
from app.models.folder import Folder
from app.models.user import User
from app.schemas.file import TrashItemResponse
from app.utils.file_handler import delete_encrypted_file
from app.utils.thumbnails import delete_thumbnail


# ============================================
# ROUTER IR LOGGER
# ============================================

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================

def _permanently_delete_file(file_obj: FileModel, user: User, db: DBSession) -> int:
    """
    gauna: file_obj (FileModel) – failo ORM objektas (turi būti is_deleted=True)
           user     (User)      – savininkas (storage atnaujinimui)
           db       (DBSession) – DB sesija
    daro: galutinai ištrina failą:
          1. Ištrina šifruotą failą iš disko
          2. Ištrina DB įrašą
          3. Grąžina originalų failo dydį (storage atnaujinimui)
          NEPAVDO db.commit() – kviečiančioji funkcija tai daro.
    grąžina: (int) – ištrinamo failo dydis baitais (storage atnaujinimui)
    """
    # Ištriname fizinį failą iš disko + thumbnail (jei buvo)
    delete_encrypted_file(file_obj.stored_filename)
    delete_thumbnail(settings.encrypted_files_dir, file_obj.stored_filename)

    freed_bytes = file_obj.size_bytes

    # Ištriname DB įrašą
    db.delete(file_obj)

    return freed_bytes


def _permanently_delete_folder_recursive(
    folder: Folder,
    user: User,
    db: DBSession,
) -> int:
    """
    gauna: folder (Folder)      – aplanko ORM objektas
           user   (User)        – savininkas (storage atnaujinimui)
           db     (DBSession)   – DB sesija
    daro: rekursyviai galutinai ištrina aplanką:
          1. Ištrina visus failus šiame aplanke (iš disko ir DB)
          2. Rekursyviai ištrina vaikiniai aplankalus (bet kurio is_deleted statuso)
          3. Ištrina patį aplanką iš DB
          NEPAVDO db.commit() – kviečiančioji funkcija tai daro.
    grąžina: (int) – iš viso išlaisvintų baitų
    """
    freed_bytes = 0

    # 1. Ištriname visus failus šiame aplanke
    files_in_folder = db.query(FileModel).filter(
        FileModel.folder_id == folder.id,
        FileModel.user_id == user.id,
    ).all()

    for file_obj in files_in_folder:
        freed_bytes += _permanently_delete_file(file_obj, user, db)

    # 2. Rekursyviai ištriname vaikiniai aplankalus
    child_folders = db.query(Folder).filter(
        Folder.parent_id == folder.id,
        Folder.user_id == user.id,
    ).all()

    for child in child_folders:
        freed_bytes += _permanently_delete_folder_recursive(child, user, db)

    # 3. Ištriname patį aplanką
    db.delete(folder)

    return freed_bytes


# ============================================
# GET /api/trash
# ============================================

@router.get(
    "",
    response_model=list[TrashItemResponse],
    summary="Šiukšlinės turinys",
)
def list_trash(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> list[TrashItemResponse]:
    """
    gauna: current_user – prisijungęs vartotojas
           db           – DB sesija
    daro: grąžina vartotojo šiukšlinės turinį –
          visus soft-deleted failus ir aplankus.
          Rikiuojama pagal deleted_at (naujausias pirmas).
    grąžina: (list[TrashItemResponse]) – mišrus failų ir aplankų sąrašas
    """
    # Ištrintų failų sąrašas
    deleted_files = db.query(FileModel).filter(
        FileModel.user_id == current_user.id,
        FileModel.is_deleted == True,  # noqa: E712
    ).all()

    # Ištrintų aplankų sąrašas
    deleted_folders = db.query(Folder).filter(
        Folder.user_id == current_user.id,
        Folder.is_deleted == True,  # noqa: E712
    ).all()

    result: list[TrashItemResponse] = []

    for f in deleted_files:
        result.append(TrashItemResponse(
            id=f.id,
            item_type="file",
            name=f.original_filename,
            deleted_at=f.deleted_at,
            size_bytes=f.size_bytes,
            color=None,
        ))

    for folder in deleted_folders:
        result.append(TrashItemResponse(
            id=folder.id,
            item_type="folder",
            name=folder.name,
            deleted_at=folder.deleted_at,
            size_bytes=None,
            color=folder.color,
        ))

    # Rikiuojame: naujausias ištrintas pirmas
    result.sort(key=lambda x: x.deleted_at or datetime.min, reverse=True)

    return result


# ============================================
# DELETE /api/trash/files/{id}
# ============================================

@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Galutinai ištrinti failą iš šiukšlinės",
)
def permanently_delete_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: file_id     (int) – failo ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: NEGRĮŽTAMAI ištrina failą:
          - Šifruotas failas ištrinamas iš disko
          - DB įrašas ištrinamas
          - storage_used_bytes sumažinamas
    grąžina: 204 No Content
    iškelia: 404 – failas nerastas arba ne šiukšlinėje
    """
    file_obj = db.query(FileModel).filter(
        FileModel.id == file_id,
        FileModel.user_id == current_user.id,
        FileModel.is_deleted == True,  # noqa: E712
    ).first()

    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failas #{file_id} nerastas šiukšlinėje.",
        )

    freed_bytes = _permanently_delete_file(file_obj, current_user, db)

    # Atnaujiname storage
    current_user.storage_used_bytes = max(
        0, current_user.storage_used_bytes - freed_bytes
    )

    db.commit()

    logger.info(
        f"Failas galutinai ištrintas: ID={file_id} | "
        f"Išlaisvinta: {freed_bytes} B | "
        f"Vartotojas: {current_user.username}"
    )


# ============================================
# DELETE /api/trash/folders/{id}
# ============================================

@router.delete(
    "/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Galutinai ištrinti aplanką iš šiukšlinės",
)
def permanently_delete_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: folder_id   (int) – aplanko ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: NEGRĮŽTAMAI ištrina aplanką ir visą jo turinį rekursyviai:
          - Visi failai aplankuose ištrinami iš disko ir DB
          - Visi vaikiniai aplankalai ištrinami iš DB
          - storage_used_bytes sumažinamas (visi failai)
    grąžina: 204 No Content
    iškelia: 404 – aplankas nerastas arba ne šiukšlinėje
    """
    folder = db.query(Folder).filter(
        Folder.id == folder_id,
        Folder.user_id == current_user.id,
        Folder.is_deleted == True,  # noqa: E712
    ).first()

    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aplankas #{folder_id} nerastas šiukšlinėje.",
        )

    freed_bytes = _permanently_delete_folder_recursive(folder, current_user, db)

    # Atnaujiname storage
    current_user.storage_used_bytes = max(
        0, current_user.storage_used_bytes - freed_bytes
    )

    db.commit()

    logger.info(
        f"Aplankas galutinai ištrintas: ID={folder_id} | "
        f"Išlaisvinta: {freed_bytes} B | "
        f"Vartotojas: {current_user.username}"
    )


# ============================================
# DELETE /api/trash/empty
# ============================================

@router.delete(
    "/empty",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Išvalyti visą šiukšlinę",
)
def empty_trash(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: current_user – prisijungęs vartotojas
           db           – DB sesija
    daro: NEGRĮŽTAMAI ištrina VISĄ šiukšlinės turinį vartotojo:
          - Visi is_deleted=True failai ištrinami iš disko ir DB
          - Visi is_deleted=True aplankalai (ir jų turinys) ištrinami
          - storage_used_bytes atnaujinamas
    grąžina: 204 No Content
    """
    freed_bytes = 0
    deleted_file_count = 0
    deleted_folder_count = 0

    # ----------------------------------------
    # 1. Galutinai ištriname visus ištrintus failus
    # ----------------------------------------
    deleted_files = db.query(FileModel).filter(
        FileModel.user_id == current_user.id,
        FileModel.is_deleted == True,  # noqa: E712
    ).all()

    for file_obj in deleted_files:
        freed_bytes += _permanently_delete_file(file_obj, current_user, db)
        deleted_file_count += 1

    # ----------------------------------------
    # 2. Galutinai ištriname visus ištrintus aplankus (rekursyviai)
    # ----------------------------------------
    deleted_folders = db.query(Folder).filter(
        Folder.user_id == current_user.id,
        Folder.is_deleted == True,  # noqa: E712
    ).all()

    for folder in deleted_folders:
        freed_bytes += _permanently_delete_folder_recursive(folder, current_user, db)
        deleted_folder_count += 1

    # ----------------------------------------
    # 3. Atnaujiname storage
    # ----------------------------------------
    current_user.storage_used_bytes = max(
        0, current_user.storage_used_bytes - freed_bytes
    )

    db.commit()

    logger.info(
        f"Šiukšlinė išvalyta | "
        f"Failai: {deleted_file_count} | "
        f"Aplankalai: {deleted_folder_count} | "
        f"Išlaisvinta: {freed_bytes} B | "
        f"Vartotojas: {current_user.username}"
    )

