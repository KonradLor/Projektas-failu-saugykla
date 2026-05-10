"""
Dalinimosi nuorodų API.

ENDPOINT'AI (su autentifikacija):
    POST   /api/share                  – sukurti share link'ą failui
    GET    /api/share                  – gauti savo share linkų sąrašą
    DELETE /api/share/{id}             – išjungti / ištrinti share link'ą

ENDPOINT'AI (be autentifikacijos – viešas priėjimas):
    GET    /api/share/public/{token}           – viešo failo informacija
    GET    /api/share/public/{token}/download  – atsisiųsti per share link'ą

SAUGUMO LOGIKA:
    - Token'as: 32 baitų URL-safe base64 (secrets.token_urlsafe)
    - max_downloads limitas: 1–1000
    - Kiekvieną atsisiuntimą skaičiuojame download_count++
    - Kai download_count >= max_downloads → is_disabled=True (auto)
    - Išjungtas arba išnaudotas link'as → 410 Gone

DALINIMOSI URL SCHEMA:
    https://<ip>/share/<token>    ← frontend puslapis
    GET /api/share/public/<token>/download ← backend atsisiuntimas
"""

# ============================================
# IMPORTAI
# ============================================
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import update
from sqlalchemy.orm import Session as DBSession, joinedload

from app.config import settings
from app.core.dependencies import get_current_user, get_optional_user
from app.core.encryption import decrypt_file_streaming
from app.database import get_db
from app.models.file import File as FileModel
from app.models.share_link import ShareLink
from app.models.user import User
from app.schemas.file import ShareLinkCreate, ShareLinkResponse
from app.utils.api_helpers import get_user_decryption_key
from app.utils.file_handler import encrypted_file_exists, get_encrypted_file_path


# ============================================
# ROUTER IR LOGGER
# ============================================

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================

def _build_share_url(token: str) -> str:
    """
    gauna: token (str) – share link'o token'as
    daro: sudaro pilną viešo dalinimosi URL.
          Naudojama ShareLinkResponse.share_url laukui.
    grąžina: (str) – pilnas URL, pvz. https://1.2.3.4/share/abc123
    """
    base = settings.base_url.rstrip("/")
    return f"{base}/share/{token}"


def _build_share_response(link: ShareLink) -> ShareLinkResponse:
    """
    gauna: link (ShareLink) – share link'o ORM objektas
    daro: konvertuoja į ShareLinkResponse pridėdamas share_url ir filename.
    grąžina: (ShareLinkResponse) – atsakymo schema
    """
    return ShareLinkResponse(
        id=link.id,
        file_id=link.file_id,
        filename=link.file.original_filename if link.file else "?",
        share_url=_build_share_url(link.token),
        download_count=link.download_count,
        max_downloads=link.max_downloads,
        downloads_remaining=link.downloads_remaining,
        is_disabled=link.is_disabled,
        is_active=link.is_active,
        created_at=link.created_at,
    )


# ============================================
# POST /api/share
# ============================================

@router.post(
    "",
    response_model=ShareLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sukurti share link'ą",
)
def create_share_link(
    payload: ShareLinkCreate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> ShareLinkResponse:
    """
    gauna: payload      (ShareLinkCreate) – { file_id, max_downloads }
           current_user                   – prisijungęs vartotojas
           db                             – DB sesija
    daro: sukuria naują share link'ą nurodytam failui.
          Patikrina ar failas priklauso vartotojui.
          Generuoja unikalų token'ą.
    grąžina: (ShareLinkResponse) – sukurto link'o duomenys su share_url
    iškelia: 404 – failas nerastas
    """
    # Patikriname ar failas egzistuoja ir priklauso vartotojui
    file_obj = db.query(FileModel).filter(
        FileModel.id == payload.file_id,
        FileModel.user_id == current_user.id,
        FileModel.is_deleted == False,  # noqa: E712
    ).first()

    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failas #{payload.file_id} nerastas.",
        )

    # Generuojame unikalų token'ą (32 baitai → 43 simboliai URL-safe base64)
    # Ciklas apsauga nuo kolizijų (tikimybė astronominė, bet geriau patikrinti)
    for _ in range(3):
        token = secrets.token_urlsafe(32)
        existing = db.query(ShareLink).filter(ShareLink.token == token).first()
        if not existing:
            break
    else:
        # Trijų bandymų nepakako – labai mažai tikėtina situacija
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Nepavyko sugeneruoti unikalaus token'o. Bandykite dar kartą.",
        )

    new_link = ShareLink(
        file_id=payload.file_id,
        token=token,
        max_downloads=payload.max_downloads,
        download_count=0,
        is_disabled=False,
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_link)
    db.commit()
    db.refresh(new_link)

    logger.info(
        f"Share link'as sukurtas: failas={file_obj.original_filename} | "
        f"Max atsisiuntimų: {payload.max_downloads} | "
        f"Vartotojas: {current_user.username}"
    )

    return _build_share_response(new_link)


# ============================================
# GET /api/share
# ============================================

@router.get(
    "",
    response_model=list[ShareLinkResponse],
    summary="Savo share linkų sąrašas",
)
def list_share_links(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> list[ShareLinkResponse]:
    """
    gauna: current_user – prisijungęs vartotojas
           db           – DB sesija
    daro: grąžina visus vartotojo sukurtų share linkų sąrašą
          (tiek aktyvių, tiek išjungtų / išnaudotų).
          Rikiuojama: naujausias sukurtas pirmas.

          Naudojama joinedload() – visi failai atsiunčiami vienu SQL query,
          taip išvengiant N+1 problemos kai _build_share_response()
          pasiekia link.file.original_filename.

          Filtruoja IŠTRINTUS failus (is_deleted=False) – kad nerodytų
          share linkų į trash'e esančius failus.
    grąžina: (list[ShareLinkResponse]) – share linkų sąrašas
    """
    # JOIN + joinedload – vienu query gauname ir Share, ir File metaduomenis
    links = (
        db.query(ShareLink)
        .join(FileModel, ShareLink.file_id == FileModel.id)
        .options(joinedload(ShareLink.file))
        .filter(
            FileModel.user_id == current_user.id,
            FileModel.is_deleted == False,  # noqa: E712
        )
        .order_by(ShareLink.created_at.desc())
        .all()
    )

    return [_build_share_response(link) for link in links]


# ============================================
# DELETE /api/share/{id}
# ============================================

@router.delete(
    "/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Išjungti / ištrinti share link'ą",
)
def delete_share_link(
    link_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: link_id     (int) – share link'o ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: galutinai ištrina share link'ą iš DB.
          Patikrina ar link'as priklauso vartotojui (per failo nuosavybę).
    grąžina: 204 No Content
    iškelia: 404 – link'as nerastas arba ne vartotojo
    """
    link = (
        db.query(ShareLink)
        .join(FileModel, ShareLink.file_id == FileModel.id)
        .filter(
            ShareLink.id == link_id,
            FileModel.user_id == current_user.id,
        )
        .first()
    )

    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Share link'as #{link_id} nerastas.",
        )

    db.delete(link)
    db.commit()

    logger.info(
        f"Share link'as ištrintas: ID={link_id} | "
        f"Vartotojas: {current_user.username}"
    )


# ============================================
# GET /api/share/public/{token}  (BEZ AUTH)
# ============================================

@router.get(
    "/public/{token}",
    summary="Viešo failo informacija (be prisijungimo)",
)
def get_public_share_info(
    token: str,
    db: DBSession = Depends(get_db),
) -> dict:
    """
    gauna: token (str) – share link'o token'as iš URL
           db          – DB sesija (autentifikacija NEPRIVALOMA)
    daro: grąžina viešą failo informaciją pagal token'ą.
          Nenaudoja autentifikacijos – prieinamas visiems.
          Naudojama frontend share puslapiui rodyti failo pavadinimą ir dydį
          PRIEŠ atsisiuntimą.
    grąžina: (dict) – { filename, size_bytes, mime_type, downloads_remaining }
    iškelia: 404 – token'as nerastas
             410 – link'as išjungtas arba išnaudotas
    """
    link = db.query(ShareLink).filter(ShareLink.token == token).first()

    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share link'as nerastas arba negaliojantis.",
        )

    if not link.is_active:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "Šis share link'as nebegalioja. "
                "Pasiektas maksimalus atsisiuntimų skaičius arba link'as išjungtas."
            ),
        )

    file_obj = link.file
    if not file_obj or file_obj.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Failas nebepasiekiamas.",
        )

    # Failo savininko username – naudojama UI „Bendrino: ..." rodiniui
    # Atskira užklausa, bet dėl tiesioginio user_id matomumo greita
    owner = db.query(User).filter(User.id == file_obj.user_id).first()

    return {
        "filename":            file_obj.original_filename,
        "size_bytes":          file_obj.size_bytes,
        "mime_type":           file_obj.mime_type,
        "downloads_remaining": link.downloads_remaining,
        "download_count":      link.download_count,
        "max_downloads":       link.max_downloads,
        "is_active":           link.is_active,
        "is_disabled":         link.is_disabled,
        "expires_at":          None,                 # nepalaikoma šioje versijoje
        "created_at":          link.created_at,
        "owner_username":      owner.username if owner else None,
    }


# ============================================
# GET /api/share/public/{token}/download  (BEZ AUTH)
# ============================================

@router.get(
    "/public/{token}/download",
    summary="Atsisiųsti failą per share link'ą (be prisijungimo)",
    response_class=StreamingResponse,
)
async def download_public_share(
    token: str,
    db: DBSession = Depends(get_db),
) -> StreamingResponse:
    """
    gauna: token (str) – share link'o token'as iš URL
           db          – DB sesija (autentifikacija NEPRIVALOMA)
    daro: dešifruoja failą srautiškai ir grąžina atsisiuntimui.
          download_count padidinamas ATOMIŠKAI (UPDATE su WHERE),
          kad du paraleliški atsisiuntimai negalėtų abu praeiti
          virš max_downloads ribos.
          SVARBU: Failas šifruotas vartotojo raktu – reikia jį gauti iš DB.
    grąžina: StreamingResponse su dešifruotu failu
    iškelia: 404 – token'as nerastas
             410 – link'as nebegalioja arba viršytas limitas (atominis)
             500 – šifravimo klaida
    """
    link = db.query(ShareLink).filter(ShareLink.token == token).first()

    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share link'as nerastas.",
        )

    if not link.is_active:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Šis share link'as nebegalioja.",
        )

    file_obj = link.file
    if not file_obj or file_obj.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Failas nebepasiekiamas.",
        )

    # Patikrinimas ar fizinis failas egzistuoja diske
    if not encrypted_file_exists(file_obj.stored_filename):
        logger.error(
            f"Share atsisiuntimas: failas diske nerastas! "
            f"UUID={file_obj.stored_filename}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failas nerastas serveryje.",
        )

    # Gauname failo savininką (reikia šifravimo rakto)
    owner = db.query(User).filter(User.id == file_obj.user_id).first()

    if not owner or not owner.encryption_key_encrypted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failo savininko šifravimo raktas nerastas.",
        )

    # Patikriname ar savininkas aktyvus (deaktyvuoto vartotojo failai nedalinami)
    if not owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Failas nebepasiekiamas (savininko paskyra deaktyvuota).",
        )

    # ----------------------------------------
    # ATOMINIS download_count padidinimas
    # ----------------------------------------
    # UPDATE share_links
    #   SET download_count = download_count + 1
    # WHERE id = ?  AND  is_disabled = 0
    #              AND  download_count < max_downloads
    # rowcount == 1 → mes laimėjome šio slot'o vietą
    # rowcount == 0 → kažkas kitas spėjo arba limitas pasiektas
    result = db.execute(
        update(ShareLink)
        .where(
            ShareLink.id == link.id,
            ShareLink.is_disabled == False,  # noqa: E712
            ShareLink.download_count < ShareLink.max_downloads,
        )
        .values(download_count=ShareLink.download_count + 1)
    )

    if result.rowcount == 0:
        # Kitas paralelinis atsisiuntimas pasiekė limitą prieš mus
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Šis share link'as ką tik pasiekė atsisiuntimų limitą.",
        )

    # Po atominio increment'o – patikriname ar reikia auto-disable
    db.refresh(link)
    if link.download_count >= link.max_downloads:
        link.is_disabled = True

    db.commit()

    # Dešifruojame vartotojo raktą su MASTER_KEY (po atominio rezervavimo)
    # Bendras helper'is – jei raktas sugadintas, mes 500
    user_key = get_user_decryption_key(owner)

    logger.info(
        f"Viešas atsisiuntimas: {file_obj.original_filename} | "
        f"Token: {token[:8]}... | "
        f"Atsisiuntimų: {link.download_count}/{link.max_downloads}"
    )

    # Srautinis dešifravimas
    encrypted_path = get_encrypted_file_path(file_obj.stored_filename)
    stream = decrypt_file_streaming(encrypted_path, user_key)

    safe_filename = file_obj.original_filename.replace('"', "'")

    return StreamingResponse(
        content=stream,
        media_type=file_obj.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Content-Length": str(file_obj.size_bytes),
        },
    )
