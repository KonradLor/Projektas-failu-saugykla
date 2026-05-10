"""
Failų API – pagrindinis failų valdymo modulis.

ENDPOINT'AI:
    POST   /api/files/upload          – įkelti failą (su šifravimu)
    GET    /api/files                  – gauti vartotojo failų sąrašą
    GET    /api/files/{id}             – gauti vieno failo metaduomenis
    GET    /api/files/{id}/download    – atsisiųsti failą (dešifruojant)
    GET    /api/files/{id}/preview     – peržiūrėti failą naršyklėje
    PATCH  /api/files/{id}             – pervadinti / perkelti į kitą aplanką
    DELETE /api/files/{id}             – perkelti į šiukšlinę (soft delete)
    POST   /api/files/{id}/restore     – atkurti iš šiukšlinės

ŠIFRAVIMO SRAUTAS (UPLOAD):
    1. Gauti failo baitai per FastAPI UploadFile
    2. Iš DB gauti vartotojo šifruotą raktą
    3. Dešifruoti raktą su MASTER_KEY
    4. Šifruoti failą su vartotojo raktu → diske
    5. Išsaugoti metaduomenis DB

DEŠIFRAVIMO SRAUTAS (DOWNLOAD):
    1. Rasti failą DB, patikrinti nuosavybę
    2. Dešifruoti vartotojo raktą su MASTER_KEY
    3. Grąžinti StreamingResponse su decrypt_file_streaming()
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.core.encryption import (
    decrypt_file_streaming,
    encrypt_file_to_path,
)
from app.database import get_db
from app.models.file import File as FileModel
from app.models.folder import Folder
from app.models.user import User
from app.schemas.file import FileListResponse, FileResponse, FileUpdate
from app.utils.api_helpers import get_user_decryption_key
from app.utils.file_handler import (
    delete_encrypted_file,
    delete_temp_file,
    encrypted_file_exists,
    ensure_storage_directory,
    generate_file_uuid,
    get_encrypted_file_path,
    stream_upload_to_temp,
)
from app.utils.thumbnails import (
    generate_and_encrypt_thumbnail,
    get_thumbnail_path,
    is_thumbnailable,
    thumbnail_exists,
)


# ============================================
# ROUTER IR LOGGER
# ============================================

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================

# get_user_decryption_key importuotas iš app/utils/api_helpers.py –
# bendras helper'is naudojamas ir share.py modulyje.
# Lokalus alias palaikomas atgalinio suderinamumo dėliai (kodas naudoja `_get_user_decryption_key`).
_get_user_decryption_key = get_user_decryption_key


def _get_file_or_404(
    file_id: int,
    user: User,
    db: DBSession,
    *,
    allow_deleted: bool = False,
) -> FileModel:
    """
    gauna: file_id       (int)      – failo ID iš URL
           user          (User)     – prisijungęs vartotojas
           db            (DBSession) – DB sesija
           allow_deleted (bool)     – leisti grąžinti ištryntus failus (trash)
    daro: ieško failo DB pagal ID ir vartotoją.
          Patikrina nuosavybę – vartotojas gali matyti TIK savo failus.
    grąžina: (FileModel) – rastas failo ORM objektas
    iškelia: HTTPException 404 – jei failas nerastas arba ne vartotojo
    """
    query = db.query(FileModel).filter(
        FileModel.id == file_id,
        FileModel.user_id == user.id,
    )

    if not allow_deleted:
        query = query.filter(FileModel.is_deleted == False)  # noqa: E712

    file_obj = query.first()

    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failas #{file_id} nerastas.",
        )

    return file_obj


# ============================================
# POST /api/files/upload
# ============================================

@router.post(
    "/upload",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Įkelti failą",
    description=(
        "Įkelia failą į sistemą. Failas šifruojamas vartotojo raktu prieš saugojant diske. "
        "Maksimalus failo dydis: 500MB. Maksimali saugykla: 2GB."
    ),
)
async def upload_file(
    file: Annotated[UploadFile, File(description="Įkeliamas failas")],
    folder_id: Annotated[Optional[int], Form()] = None,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FileResponse:
    """
    gauna: file       (UploadFile) – įkeliamas failas (multipart/form-data)
           folder_id  (int|None)   – aplankas kuriame saugoti (None = šakninis)
           current_user            – prisijungęs vartotojas (iš cookie)
           db                      – DB sesija
    daro: 1. Tikrina failo dydį (max 500MB)
          2. Tikrina saugyklos limitą (max 2GB)
          3. Tikrina aplanką (jei nurodytas)
          4. Dešifruoja vartotojo raktą
          5. Šifruoja failą ir saugo diske
          6. Išsaugo metaduomenis DB
          7. Atnaujina storage_used_bytes
    grąžina: (FileResponse) – sukurto failo metaduomenys
    iškelia: 400 – failas be vardo arba tuščias pavadinimas
             413 – failas per didelis
             507 – saugykla pilna
             404 – aplankas nerastas
    """

    # ----------------------------------------
    # 1. Failo vardo tikrinimas
    # ----------------------------------------
    if not file.filename or not file.filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failo vardas negali būti tuščias.",
        )

    original_filename = file.filename.strip()

    # ----------------------------------------
    # 2. Aplanko tikrinimas (jei nurodytas) – darome PRIEŠ upload'ą
    # ----------------------------------------
    if folder_id is not None:
        folder = db.query(Folder).filter(
            Folder.id == folder_id,
            Folder.user_id == current_user.id,
            Folder.is_deleted == False,  # noqa: E712
        ).first()

        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Aplankas #{folder_id} nerastas.",
            )

    # ----------------------------------------
    # 3. Saugyklos limito preliminarus tikrinimas
    # (tikslesnis tikrinimas po stream'inimo, kai žinome tikrą dydį)
    # ----------------------------------------
    available_bytes = settings.max_storage_per_user_bytes - current_user.storage_used_bytes
    if available_bytes <= 0:
        used_mb = round(current_user.storage_used_bytes / (1024 * 1024), 1)
        max_mb = settings.max_storage_per_user_mb
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=(
                f"Saugykla pilna. Naudojama: {used_mb} MB / {max_mb} MB. "
                f"Ištrinkite senus failus."
            ),
        )

    # Maksimalus šio upload'o dydis = min(failo limitas, likusi vieta vartotojui)
    upload_limit = min(settings.max_file_size_bytes, available_bytes)

    # ----------------------------------------
    # 4. Streaming upload į laikiną failą (RAM-safe)
    # ----------------------------------------
    # Failas chunk'ais (1MB) rašomas į diską – NEKRAUNA viso į RAM.
    # Jei viršyja limitą, pati funkcija išmes ValueError ir išvalys temp failą.
    try:
        temp_path, file_size = await stream_upload_to_temp(
            upload_file=file,
            max_bytes=upload_limit,
        )
    except ValueError:
        # Failas viršija leistinus dydį – grąžiname tikslią klaidą
        if file_size := getattr(file, "_total", 0):
            pass
        # Atskiriame: failas didesnis už absoliutų limitą vs už likusią vietą
        if upload_limit == settings.max_file_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Failas per didelis. Maksimalus dydis: {settings.max_file_size_mb} MB.",
            )
        else:
            used_mb = round(current_user.storage_used_bytes / (1024 * 1024), 1)
            max_mb = settings.max_storage_per_user_mb
            raise HTTPException(
                status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                detail=(
                    f"Saugykla pilna. Naudojama: {used_mb} MB / {max_mb} MB. "
                    f"Ištrinkite senus failus."
                ),
            )
    except Exception as exc:
        logger.error(f"Upload streaming klaida ({original_filename}): {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failo įkėlimas nepavyko.",
        ) from exc

    if file_size == 0:
        delete_temp_file(temp_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failas tuščias.",
        )

    # ----------------------------------------
    # 5. Vartotojo šifravimo rakto gavimas
    # ----------------------------------------
    try:
        user_key = _get_user_decryption_key(current_user)
    except HTTPException:
        delete_temp_file(temp_path)
        raise

    # ----------------------------------------
    # 6. Failo šifravimas streaming būdu (chunk po chunk iš disko)
    # ----------------------------------------
    ensure_storage_directory()

    stored_filename = generate_file_uuid()
    dest_path = get_encrypted_file_path(stored_filename)

    # MIME tipas – jau čia reikalingas thumbnail logikai
    mime_type = file.content_type or _guess_mime_type(original_filename)

    try:
        encrypted_size, file_hash = encrypt_file_to_path(
            source_path=temp_path,
            dest_path=dest_path,
            user_key=user_key,
        )

        # ----------------------------------------
        # 6b. Thumbnail generavimas paveikslėliams
        # (dabar dar turime temp_path su plaintext duomenimis)
        # ----------------------------------------
        if is_thumbnailable(mime_type) and file_size <= 50 * 1024 * 1024:  # max 50MB src
            try:
                with open(temp_path, "rb") as src:
                    image_bytes = src.read()
                generate_and_encrypt_thumbnail(
                    image_bytes=image_bytes,
                    encrypted_dest_dir=settings.encrypted_files_dir,
                    stored_filename=stored_filename,
                    user_key=user_key,
                )
            except Exception as thumb_exc:
                # Thumbnail klaida – nekritiška, sistema veikia toliau
                logger.warning(
                    f"Thumbnail generavimas nepavyko ({original_filename}): {thumb_exc}"
                )

    except Exception as exc:
        # Šifravimas nepavyko – išvalome dest + temp
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        delete_temp_file(temp_path)
        logger.error(f"Šifravimo klaida ({original_filename}): {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failo šifravimas nepavyko.",
        ) from exc
    finally:
        # Bet kuriuo atveju – išvalome temp
        delete_temp_file(temp_path)

    # ----------------------------------------
    # 7. Metaduomenų saugojimas DB
    # ----------------------------------------

    new_file = FileModel(
        user_id=current_user.id,
        folder_id=folder_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        mime_type=mime_type,
        size_bytes=file_size,          # Originalus (nešifruotas) dydis
        file_hash=file_hash,
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_file)

    # Atnaujiname vartotojo saugyklos naudojimą
    current_user.storage_used_bytes += file_size

    db.commit()
    db.refresh(new_file)

    logger.info(
        f"Failas įkeltas: {original_filename} | "
        f"UUID: {stored_filename} | "
        f"Dydis: {file_size} B (šifruotas: {encrypted_size} B) | "
        f"Vartotojas: {current_user.username}"
    )

    return FileResponse.model_validate(new_file)


# ============================================
# GET /api/files
# ============================================

@router.get(
    "",
    response_model=FileListResponse,
    summary="Failų sąrašas",
)
def list_files(
    folder_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FileListResponse:
    """
    gauna: folder_id (int|None) – filtruoti pagal aplanką (None = šakninis)
           current_user         – prisijungęs vartotojas
           db                   – DB sesija
    daro: grąžina vartotojo failų sąrašą nurodytame aplanke.
          Neįtraukia ištryntų failų (soft deleted).
    grąžina: (FileListResponse) – failų sąrašas ir kiekis
    """
    query = db.query(FileModel).filter(
        FileModel.user_id == current_user.id,
        FileModel.is_deleted == False,  # noqa: E712
        FileModel.folder_id == folder_id,  # None = šakninis aplankas
    ).order_by(FileModel.created_at.desc())

    files = query.all()

    return FileListResponse(
        files=[FileResponse.model_validate(f) for f in files],
        total=len(files),
    )


# ============================================
# GET /api/files/{id}
# ============================================

@router.get(
    "/{file_id}",
    response_model=FileResponse,
    summary="Failo metaduomenys",
)
def get_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FileResponse:
    """
    gauna: file_id     (int)  – failo ID iš URL
           current_user       – prisijungęs vartotojas
           db                 – DB sesija
    daro: grąžina vieno failo metaduomenis.
          Patikrina nuosavybę.
    grąžina: (FileResponse) – failo metaduomenys
    iškelia: 404 – failas nerastas
    """
    file_obj = _get_file_or_404(file_id, current_user, db)
    return FileResponse.model_validate(file_obj)


# ============================================
# GET /api/files/{id}/download
# ============================================

@router.get(
    "/{file_id}/download",
    summary="Atsisiųsti failą",
    response_class=StreamingResponse,
)
async def download_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> StreamingResponse:
    """
    gauna: file_id     (int)  – failo ID iš URL
           current_user       – prisijungęs vartotojas
           db                 – DB sesija
    daro: dešifruoja failą srautiškai ir grąžina kaip atsisiuntimą.
          Failas NEKRAUNAMAS į RAM visas – dešifruojama po chunk'us (64KB).
          Content-Disposition: attachment → naršyklė siūlo išsaugoti.
    grąžina: StreamingResponse su dešifruotu failu
    iškelia: 404 – failas nerastas
             500 – dešifravimo klaida arba failas diske nerastas
    """
    file_obj = _get_file_or_404(file_id, current_user, db)

    # Tikriname ar failas egzistuoja diske
    if not encrypted_file_exists(file_obj.stored_filename):
        logger.error(
            f"Failas diske nerastas! DB ID={file_id}, UUID={file_obj.stored_filename}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failas nerastas diske. Kreipkitės į administratorių.",
        )

    # Gauname vartotojo šifravimo raktą
    user_key = _get_user_decryption_key(current_user)

    # Sukuriame dešifravimo generatorių (streaming – nekrauna į RAM)
    encrypted_path = get_encrypted_file_path(file_obj.stored_filename)
    stream = decrypt_file_streaming(encrypted_path, user_key)

    # Content-Disposition: attachment → naršyklė siūlo išsaugoti failą
    # filename* RFC 5987 – palaikomas Unicode failų vardo kodavimas
    safe_filename = file_obj.original_filename.replace('"', "'")

    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
        "Content-Length": str(file_obj.size_bytes),
        "X-File-Hash": file_obj.file_hash or "",
    }

    logger.info(
        f"Atsisiuntimas pradėtas: {file_obj.original_filename} | "
        f"Vartotojas: {current_user.username}"
    )

    return StreamingResponse(
        content=stream,
        media_type=file_obj.mime_type or "application/octet-stream",
        headers=headers,
    )


# ============================================
# GET /api/files/{id}/thumbnail
# ============================================

@router.get(
    "/{file_id}/thumbnail",
    summary="Paveikslėlio thumbnail (mažesnis nei preview)",
    response_class=StreamingResponse,
)
async def get_file_thumbnail(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> StreamingResponse:
    """
    gauna: file_id     (int)  – failo ID iš URL
           current_user       – prisijungęs vartotojas
           db                 – DB sesija
    daro: grąžina dešifruotą thumbnail'ą (mažą JPEG, ~10-50KB).
          Jei thumbnail'as neegzistuoja (senas failas arba nepavyko sugeneruoti) –
          404 (frontend turi fallback į emoji ikoną).
          Apsauga: tikrinama failo nuosavybė.
    grąžina: StreamingResponse su JPEG thumbnail'u (inline)
    iškelia: 404 – failas arba thumbnail nerastas
    """
    file_obj = _get_file_or_404(file_id, current_user, db)

    if not thumbnail_exists(settings.encrypted_files_dir, file_obj.stored_filename):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thumbnail nerastas.",
        )

    user_key = get_user_decryption_key(current_user)
    thumb_path = get_thumbnail_path(settings.encrypted_files_dir, file_obj.stored_filename)

    stream = decrypt_file_streaming(thumb_path, user_key)

    return StreamingResponse(
        content=stream,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": 'inline',
            "Cache-Control":       "private, max-age=86400",  # 1 diena
        },
    )


# ============================================
# GET /api/files/{id}/preview
# ============================================

@router.get(
    "/{file_id}/preview",
    summary="Peržiūrėti failą naršyklėje",
    response_class=StreamingResponse,
)
async def preview_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> StreamingResponse:
    """
    gauna: file_id     (int)  – failo ID iš URL
           current_user       – prisijungęs vartotojas
           db                 – DB sesija
    daro: dešifruoja failą srautiškai ir grąžina tiesiai naršyklei peržiūrai.
          Content-Disposition: inline → naršyklė rodo failą, ne siūlo atsisiųsti.
          Tinka: nuotraukoms (JPEG, PNG, GIF, WebP) ir PDF.
    grąžina: StreamingResponse su dešifruotu failu (inline)
    iškelia: 400 – failas nėra peržiūrimas tipo
             404 – failas nerastas
    """
    file_obj = _get_file_or_404(file_id, current_user, db)

    # Tikriname ar failas yra peržiūrimo tipo
    # Naudojame schema metodą konvertavę modelį į schema objektą
    file_schema = FileResponse.model_validate(file_obj)
    if not file_schema.is_previewable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Failas '{file_obj.original_filename}' negali būti peržiūrimas. "
                f"Peržiūra palaikoma: nuotraukoms ir PDF."
            ),
        )

    if not encrypted_file_exists(file_obj.stored_filename):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failas nerastas diske.",
        )

    user_key = _get_user_decryption_key(current_user)
    encrypted_path = get_encrypted_file_path(file_obj.stored_filename)
    stream = decrypt_file_streaming(encrypted_path, user_key)

    safe_filename = file_obj.original_filename.replace('"', "'")

    headers = {
        # inline → naršyklė rodo, ne siūlo atsisiųsti
        "Content-Disposition": f'inline; filename="{safe_filename}"',
        "Content-Length": str(file_obj.size_bytes),
        # Leidžiame naršyklei cache'inti preview (1 valanda)
        "Cache-Control": "private, max-age=3600",
    }

    return StreamingResponse(
        content=stream,
        media_type=file_obj.mime_type or "application/octet-stream",
        headers=headers,
    )


# ============================================
# PATCH /api/files/{id}
# ============================================

@router.patch(
    "/{file_id}",
    response_model=FileResponse,
    summary="Pervadinti arba perkelti failą",
)
def update_file(
    file_id: int,
    payload: FileUpdate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FileResponse:
    """
    gauna: file_id     (int)       – failo ID iš URL
           payload     (FileUpdate) – { filename?, folder_id? }
           current_user             – prisijungęs vartotojas
           db                       – DB sesija
    daro: pervadina failą ir/arba perkelia į kitą aplanką.
          Patikrina path traversal atakas (FileUpdate schema).
          Patikrina tikslinį aplanką (jei keičiamas).
    grąžina: (FileResponse) – atnaujinti failo metaduomenys
    iškelia: 404 – failas arba tikslinis aplankas nerastas
    """
    file_obj = _get_file_or_404(file_id, current_user, db)

    # Pervadinimas
    if payload.filename is not None:
        old_name = file_obj.original_filename
        file_obj.original_filename = payload.filename
        logger.info(
            f"Failas pervadintas: '{old_name}' → '{payload.filename}' | "
            f"Vartotojas: {current_user.username}"
        )

    # Perkėlimas į kitą aplanką
    if payload.folder_id is not None:
        # folder_id = 0 arba -1 reiškia šakninį aplanką
        # Naudojame None kaip "šakninis aplankas" DB
        if payload.folder_id <= 0:
            file_obj.folder_id = None
        else:
            folder = db.query(Folder).filter(
                Folder.id == payload.folder_id,
                Folder.user_id == current_user.id,
                Folder.is_deleted == False,  # noqa: E712
            ).first()

            if not folder:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Tikslinis aplankas #{payload.folder_id} nerastas.",
                )

            file_obj.folder_id = payload.folder_id

    db.commit()
    db.refresh(file_obj)

    return FileResponse.model_validate(file_obj)


# ============================================
# DELETE /api/files/{id}  (soft delete → šiukšlinė)
# ============================================

@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Perkelti į šiukšlinę",
)
def delete_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: file_id     (int) – failo ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: perkelia failą į šiukšlinę (soft delete).
          Failas LIEKA diske – tik pažymimas is_deleted=True.
          Galutinis trynimas atliekamas per /api/trash endpoint'ą.
    grąžina: 204 No Content
    iškelia: 404 – failas nerastas
    """
    file_obj = _get_file_or_404(file_id, current_user, db)

    # Soft delete – tik pažymime DB, failas diske lieka
    file_obj.soft_delete()

    db.commit()

    logger.info(
        f"Failas perkeltas į šiukšlinę: {file_obj.original_filename} | "
        f"Vartotojas: {current_user.username}"
    )


# ============================================
# POST /api/files/{id}/restore  (atkūrimas iš šiukšlinės)
# ============================================

@router.post(
    "/{file_id}/restore",
    response_model=FileResponse,
    summary="Atkurti iš šiukšlinės",
)
def restore_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FileResponse:
    """
    gauna: file_id     (int) – failo ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: atkuria failą iš šiukšlinės (atšaukia soft delete).
          Jei originalus aplankas jau ištrintas – failas grąžinamas į šakninį.
    grąžina: (FileResponse) – atkurto failo metaduomenys
    iškelia: 404 – failas nerastas šiukšlinėje
             400 – failas ne šiukšlinėje
    """
    file_obj = _get_file_or_404(file_id, current_user, db, allow_deleted=True)

    if not file_obj.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failas nėra šiukšlinėje.",
        )

    # Jei originalus aplankas ištrintas – grąžiname į šakninį (folder_id = None)
    if file_obj.folder_id is not None:
        folder = db.query(Folder).filter(
            Folder.id == file_obj.folder_id,
            Folder.is_deleted == False,  # noqa: E712
        ).first()

        if not folder:
            file_obj.folder_id = None
            logger.info(
                f"Failo '{file_obj.original_filename}' aplankas ištrintas – "
                f"grąžinamas į šakninį."
            )

    file_obj.restore()
    db.commit()
    db.refresh(file_obj)

    logger.info(
        f"Failas atkurtas iš šiukšlinės: {file_obj.original_filename} | "
        f"Vartotojas: {current_user.username}"
    )

    return FileResponse.model_validate(file_obj)


# ============================================
# PAGALBINĖ: MIME TIPO SPĖJIMAS
# ============================================

def _guess_mime_type(filename: str) -> str:
    """
    gauna: filename (str) – failo vardas su plėtiniu
    daro: spėja MIME tipą pagal failo plėtinį.
          Naudojama kai naršyklė nenurodė content_type.
    grąžina: (str) – MIME tipo string arba "application/octet-stream"
    """
    import mimetypes

    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"
