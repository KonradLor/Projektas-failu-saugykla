"""
Admin API – vartotojų valdymo ir sistemos statistikos modulis.

PRIEIGOS KONTROLĖ:
    Visi šio modulio endpoint'ai reikalauja:
        1. Galiojančios sesijos (HTTP-only cookie)
        2. is_admin=True

    Naudojama: Depends(require_admin) – iš core/dependencies.py

ENDPOINT'AI:
    GET    /api/admin/users                – visų vartotojų sąrašas
    POST   /api/admin/users                – pradėti vartotojo kūrimą (1 žingsnis)
    POST   /api/admin/users/confirm        – patvirtinti TOTP ir užbaigti kūrimą (2 žingsnis)
    GET    /api/admin/users/{id}           – vieno vartotojo duomenys
    PATCH  /api/admin/users/{id}           – aktyvuoti / deaktyvuoti / keisti rolę
    DELETE /api/admin/users/{id}           – ištrinti vartotoją (su visais failais!)
    POST   /api/admin/users/{id}/reset-2fa – resetinti TOTP secret
    GET    /api/admin/stats                – sistemos statistika

VARTOTOJO KŪRIMAS (2 ŽINGSNIAI):
    1. POST /users {username, is_admin}     → grąžina QR kodą + setup_token
    2. POST /users/confirm {setup_token, totp_code} → sukuria vartotoją
    Skirtingai nuo viešo /api/auth/register/confirm – admin sesija NEPAKEIČIAMA.

VARTOTOJO TRYNIMAS:
    Ištrinamas vartotojas → kaskadinis trynimas:
        - Visos sesijos (DB CASCADE)
        - Visi share_links (DB CASCADE)
        - Visi failai iš DISKO (šifruoti UUID failai)
        - Visos DB lentelių eilutės (CASCADE DELETE)
    NEGRĮŽTAMA operacija!
"""

# ============================================
# IMPORTAI
# ============================================
import logging
import re
import secrets as _secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.dependencies import get_current_user, require_admin
from app.core.encryption import encrypt_user_key, generate_user_key
from app.core.security import generate_temp_token, hash_password
from app.core.totp import generate_qr_code_base64, generate_totp_secret, generate_totp_uri, verify_totp_code
from app.database import get_db
from app.models.file import File as FileModel
from app.models.folder import Folder
from app.models.session import Session as SessionModel
from app.models.share_link import ShareLink
from app.models.user import User
from app.schemas.user import (
    AdminUserCreateResponse,
    UserResponse,
    UserStatsResponse,
    UserUpdate,
)
from app.utils.file_handler import delete_encrypted_file, get_storage_stats
from app.utils.thumbnails import delete_thumbnail


# ============================================
# ROUTER IR LOGGER
# ============================================

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================

def _get_user_or_404(user_id: int, db: DBSession) -> User:
    """
    gauna: user_id (int)      – vartotojo ID iš URL
           db      (DBSession) – DB sesija
    daro: ieško vartotojo DB pagal ID.
    grąžina: (User) – rastas vartotojo ORM objektas
    iškelia: HTTPException 404 – jei vartotojas nerastas
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vartotojas #{user_id} nerastas.",
        )

    return user


def _delete_all_user_files_from_disk(user_id: int, db: DBSession) -> int:
    """
    gauna: user_id (int)      – vartotojo ID
           db      (DBSession) – DB sesija
    daro: ištrina VISUS vartotojo failus iš disko (šifruotus UUID failus).
          DB įrašai ištrinami per CASCADE DELETE kai trinamas User.
          Naudojama prieš db.delete(user).
    grąžina: (int) – ištrintų failų skaičius
    """
    all_files = db.query(FileModel).filter(FileModel.user_id == user_id).all()

    deleted_count = 0
    for file_obj in all_files:
        try:
            delete_encrypted_file(file_obj.stored_filename)
            # Bandome ištrinti ir thumbnail'ą (jei buvo)
            delete_thumbnail(settings.encrypted_files_dir, file_obj.stored_filename)
            deleted_count += 1
        except Exception as exc:
            # Disko klaida – loggyjame bet nesustojame
            logger.error(
                f"Nepavyko ištrinti failo diske: {file_obj.stored_filename}: {exc}"
            )

    return deleted_count


# ============================================
# GET /api/admin/users
# ============================================

@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="Visų vartotojų sąrašas",
    dependencies=[Depends(require_admin)],
)
def list_users(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> list[UserResponse]:
    """
    gauna: current_user – prisijungęs admin vartotojas
           db           – DB sesija
    daro: grąžina visų sistemos vartotojų sąrašą su file_count laukais.
          Rikiuojama pagal sukūrimo datą (seniausias pirmas).
          Failų skaičius gaunamas vienu agreguotu query (ne N+1).
    grąžina: (list[UserResponse]) – vartotojų sąrašas (be slaptų laukų)
    """
    users = db.query(User).order_by(User.created_at.asc()).all()

    # Vienu query – {user_id: file_count} žodynas (apsauga nuo N+1)
    count_rows = db.query(
        FileModel.user_id,
        func.count(FileModel.id).label("cnt"),
    ).filter(
        FileModel.is_deleted == False,  # noqa: E712
    ).group_by(FileModel.user_id).all()

    file_counts: dict[int, int] = {row.user_id: row.cnt for row in count_rows}

    # Konvertuojame kiekvieną į schemą su file_count
    result = []
    for u in users:
        data = UserResponse.model_validate(u).model_dump()
        data["file_count"] = file_counts.get(u.id, 0)
        result.append(UserResponse(**data))
    return result


# ============================================
# ADMIN USER KŪRIMO TARPINIS BUFERIS (2 žingsnių 2FA flow)
# ============================================
# Skirtingai nuo viešo /api/auth/register/confirm, admin flow NEKEIČIA admin sesijos
# ir leidžia administratoriui kontroliuoti rolę bei matyti QR kodą prieš
# pateikiant jį naujam vartotojui.
#
# Struktūra:
#   { setup_token: { username, is_admin, totp_secret, attempts, expires_at } }
_pending_admin_creates: dict[str, dict] = {}


def _cleanup_expired_admin_pending() -> None:
    """Pašalina pasibaigusius admin user kūrimo įrašus iš atminties."""
    now = datetime.now(timezone.utc)
    expired = [tok for tok, d in _pending_admin_creates.items() if d["expires_at"] < now]
    for tok in expired:
        del _pending_admin_creates[tok]


# ============================================
# REQUEST/RESPONSE SCHEMOS (admin kūrimo flow)
# ============================================

class AdminUserCreateRequest(BaseModel):
    """1 žingsnio kūnas: tik username + is_admin (slaptažodis NEREIKIA)."""

    username: str = Field(
        ..., min_length=3, max_length=50, pattern=r"^[a-z0-9_-]+$",
        description="Vartotojo vardas (3–50 simbolių)",
    )
    is_admin: bool = Field(
        default=False,
        description="True = administratorius, False = eilinis vartotojas",
    )


class AdminUserSetupResponse(BaseModel):
    """1 žingsnio atsakymas: setup_token + QR kodas TOTP įvedimui."""

    setup_token: str = Field(description="Laikinas token'as 2 žingsniui (10 min.)")
    qr_code_base64: str = Field(description="QR kodas Google Authenticator'iui")
    totp_uri: str = Field(description="TOTP URI rankiniam įvedimui")
    username: str = Field(description="Numatytas username (informaciniam rodiniui)")
    expires_in_seconds: int = Field(description="Po kiek sekundžių setup_token nebegalios")


class AdminUserConfirmRequest(BaseModel):
    """2 žingsnio kūnas: TOTP kodas patvirtina, kad QR sėkmingai pridėtas."""

    setup_token: str = Field(..., description="Iš 1 žingsnio gautas token'as")
    totp_code: str = Field(
        ..., min_length=6, max_length=6, pattern=r"^\d{6}$",
        description="6 skaitmenų TOTP kodas",
    )


# ============================================
# POST /api/admin/users  (1 žingsnis – username → QR)
# ============================================

@router.post(
    "/users",
    response_model=AdminUserSetupResponse,
    status_code=status.HTTP_200_OK,
    summary="1 žingsnis: pradėti naujo vartotojo kūrimą",
    dependencies=[Depends(require_admin)],
)
def admin_create_start(
    payload: AdminUserCreateRequest,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> AdminUserSetupResponse:
    """
    gauna: payload      (AdminUserCreateRequest) – { username, is_admin }
           current_user                          – prisijungęs admin
           db                                    – DB sesija
    daro: 1. Tikrina ar username laisvas ir nerezervuotas
          2. Generuoja TOTP secret + QR kodą
          3. Išsaugo laikinus duomenis atmintyje (10 min. TTL)
          4. Grąžina setup_token administratoriui (admin sesija NEPAKEIČIAMA)
    grąžina: AdminUserSetupResponse – setup_token + QR kodas
    iškelia: 409 – username jau užimtas / rezervuotas
             403 – sistema pilna (max_users)
    """
    _cleanup_expired_admin_pending()

    username = payload.username.strip().lower()

    # Rezervuoti vardai
    reserved = {"admin", "root", "system", "konradvault", "api", "share"}
    if username in reserved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vardas '{username}' yra rezervuotas sistemos reikmėms.",
        )

    # Užimtumo tikrinimas
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vartotojas '{username}' jau egzistuoja.",
        )

    # Vietų limito tikrinimas
    user_count = db.query(User).count()
    if user_count >= settings.max_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Sistema pilna – {settings.max_users} vietų išnaudota.",
        )

    # Generuojame TOTP komponentus
    totp_secret = generate_totp_secret()
    totp_uri = generate_totp_uri(totp_secret, username)
    qr_base64 = generate_qr_code_base64(totp_uri)

    # Laikinas token'as
    setup_token = generate_temp_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    _pending_admin_creates[setup_token] = {
        "username": username,
        "is_admin": payload.is_admin,
        "totp_secret": totp_secret,
        "attempts": 0,                # brute-force apsauga (max 5)
        "expires_at": expires_at,
        "created_by_admin_id": current_user.id,
    }

    logger.info(
        f"Admin '{current_user.username}' pradėjo kurti vartotoją '{username}' "
        f"(is_admin={payload.is_admin}) – laukia QR patvirtinimo"
    )

    return AdminUserSetupResponse(
        setup_token=setup_token,
        qr_code_base64=qr_base64,
        totp_uri=totp_uri,
        username=username,
        expires_in_seconds=600,
    )


# ============================================
# POST /api/admin/users/confirm  (2 žingsnis – TOTP → vartotojas sukurtas)
# ============================================

@router.post(
    "/users/confirm",
    response_model=AdminUserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="2 žingsnis: TOTP patvirtinimas → vartotojas sukurtas",
    dependencies=[Depends(require_admin)],
)
def admin_create_confirm(
    payload: AdminUserConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> AdminUserCreateResponse:
    """
    gauna: payload      (AdminUserConfirmRequest) – { setup_token, totp_code }
           current_user                           – prisijungęs admin
           db                                     – DB sesija
    daro: 1. Tikrina setup_token galiojimą
          2. Tikrina TOTP kodą (max 5 bandymai)
          3. Generuoja šifravimo komponentus
          4. Sukuria User įrašą DB
          5. Admin sesija NEKEIČIAMA – tik grąžinamas naujo vartotojo info
    grąžina: AdminUserCreateResponse – sukurtas vartotojas + QR + initial_password=""
    iškelia: 401 – token negalioja arba neteisingas TOTP
             403 – sistema pilna (race apsauga)
             409 – username pasiimtas (race apsauga)
             429 – per daug nepavykusių TOTP bandymų
    """
    _cleanup_expired_admin_pending()

    pending = _pending_admin_creates.get(payload.setup_token)

    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Setup token negalioja arba pasibaigė. Pradėkite iš naujo.",
        )

    if datetime.now(timezone.utc) > pending["expires_at"]:
        del _pending_admin_creates[payload.setup_token]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Setup token pasibaigė (10 min. limitas). Pradėkite iš naujo.",
        )

    # Brute-force apsauga – max 5 bandymai
    if pending["attempts"] >= 5:
        del _pending_admin_creates[payload.setup_token]
        logger.warning(
            f"Per daug nepavykusių TOTP bandymų admin user kūrime: "
            f"username={pending['username']}, admin={current_user.username}"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Per daug nepavykusių bandymų. Pradėkite iš naujo.",
        )

    # TOTP kodo tikrinimas
    if not verify_totp_code(pending["totp_secret"], payload.totp_code):
        pending["attempts"] += 1
        remaining = 5 - pending["attempts"]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Neteisingas TOTP kodas. Liko bandymų: {remaining}.",
        )

    username = pending["username"]
    is_admin = pending["is_admin"]
    totp_secret = pending["totp_secret"]

    # Race apsauga – dar kartą tikriname užimtumą / vietų skaičių
    if db.query(User).filter(User.username == username).first():
        del _pending_admin_creates[payload.setup_token]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vartotojas '{username}' jau egzistuoja.",
        )

    if db.query(User).count() >= settings.max_users:
        del _pending_admin_creates[payload.setup_token]
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema pilna – kitas vartotojas užėmė paskutinę vietą.",
        )

    # Generuojame saugumo komponentus
    raw_user_key = generate_user_key()
    encrypted_user_key = encrypt_user_key(raw_user_key, settings.master_key)

    # Vidinis slaptažodis (niekada nerodomas) – DB stulpelis NOT NULL
    internal_password = hash_password(_secrets.token_hex(32))

    new_user = User(
        username=username,
        password_hash=internal_password,
        totp_secret=totp_secret,
        encryption_key_encrypted=encrypted_user_key,
        is_admin=is_admin,
        is_active=True,
        storage_used_bytes=0,
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Generuojame QR kodą atsakymui (taip pat saugomas atminty buvęs)
    totp_uri = generate_totp_uri(totp_secret, username)
    qr_base64 = generate_qr_code_base64(totp_uri)

    # Valome setup_token
    del _pending_admin_creates[payload.setup_token]

    logger.info(
        f"Admin '{current_user.username}' sukūrė vartotoją: "
        f"'{username}' (is_admin={is_admin})"
    )

    return AdminUserCreateResponse(
        user=UserResponse.model_validate(new_user),
        qr_code_base64=qr_base64,
        totp_uri=totp_uri,
        initial_password="",  # Slaptažodžio nėra – tik TOTP autentifikacija
    )


# ============================================
# GET /api/admin/users/{id}
# ============================================

@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Vieno vartotojo duomenys",
    dependencies=[Depends(require_admin)],
)
def get_user(
    user_id: int,
    db: DBSession = Depends(get_db),
) -> UserResponse:
    """
    gauna: user_id (int) – vartotojo ID iš URL
           db            – DB sesija
    daro: grąžina vieno vartotojo duomenis.
    grąžina: (UserResponse) – vartotojo duomenys (be slaptų laukų)
    iškelia: 404 – vartotojas nerastas
    """
    user = _get_user_or_404(user_id, db)
    return UserResponse.model_validate(user)


# ============================================
# PATCH /api/admin/users/{id}
# ============================================

@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Aktyvuoti / deaktyvuoti vartotoją arba keisti rolę",
    dependencies=[Depends(require_admin)],
)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> UserResponse:
    """
    gauna: user_id     (int)        – vartotojo ID iš URL
           payload     (UserUpdate) – { is_active?, is_admin? }
           current_user             – prisijungęs admin
           db                       – DB sesija
    daro: keičia vartotojo aktyvumo statusą ir/arba admin rolę.
          Admin negali deaktyvuoti paties savęs.
          Admin negali atimti pačiam sau admin teises.
    grąžina: (UserResponse) – atnaujinti vartotojo duomenys
    iškelia: 404 – vartotojas nerastas
             400 – bandoma keisti savo statusą
    """
    user = _get_user_or_404(user_id, db)

    # Apsauga: admin negali pats save "sugadinti"
    if user.id == current_user.id:
        if payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Negalite deaktyvuoti savo paskyros.",
            )
        if payload.is_admin is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Negalite atimti pačiam sau admin teises.",
            )

    if payload.is_active is not None:
        user.is_active = payload.is_active

    if payload.is_admin is not None:
        user.is_admin = payload.is_admin

    db.commit()
    db.refresh(user)

    logger.info(
        f"Admin '{current_user.username}' atnaujino vartotoją "
        f"#{user_id} '{user.username}': "
        f"is_active={user.is_active}, is_admin={user.is_admin}"
    )

    return UserResponse.model_validate(user)


# ============================================
# DELETE /api/admin/users/{id}
# ============================================

@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Ištrinti vartotoją (su visais failais!)",
    dependencies=[Depends(require_admin)],
)
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: user_id     (int) – vartotojo ID iš URL
           current_user      – prisijungęs admin
           db                – DB sesija
    daro: NEGRĮŽTAMAI ištrina vartotoją ir VISĄ jo turinį:
          1. Visi vartotojo failai ištrinami iš disko
          2. User įrašas ištrinamas iš DB
             → CASCADE ištrina: sessions, files, folders, share_links
          Admin negali ištrinti savęs.
    grąžina: 204 No Content
    iškelia: 404 – vartotojas nerastas
             400 – bandoma ištrinti save
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Negalite ištrinti savo paskyros.",
        )

    user = _get_user_or_404(user_id, db)

    username_for_log = user.username

    # 1. Ištriname failus iš disko (PRIEŠ DB trynimą – reikia stored_filename)
    deleted_files_count = _delete_all_user_files_from_disk(user_id, db)

    # 2. Ištriname vartotoją iš DB (CASCADE ištrina visas susijusias eilutes)
    db.delete(user)
    db.commit()

    logger.warning(
        f"Admin '{current_user.username}' IŠTRINTAS vartotojas: "
        f"'{username_for_log}' (ID={user_id}) | "
        f"Failai iš disko: {deleted_files_count}"
    )


# ============================================
# POST /api/admin/users/{id}/reset-2fa
# ============================================

@router.post(
    "/users/{user_id}/reset-2fa",
    response_model=AdminUserCreateResponse,
    summary="Resetinti vartotojo 2FA (sugeneruoti naują TOTP secret)",
    dependencies=[Depends(require_admin)],
)
def reset_2fa(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> AdminUserCreateResponse:
    """
    gauna: user_id     (int) – vartotojo ID iš URL
           current_user      – prisijungęs admin
           db                – DB sesija
    daro: sugeneruoja naują TOTP secret vartotojui.
          Naudojama kai vartotojas prarado Google Authenticator prieigą.
          Po reset'o vartotojas TURI nuskenoti naują QR kodą.
          Visos aktyvios sesijos išjungiamos (priverstinis re-login).
    grąžina: (AdminUserCreateResponse) – naujas QR kodas (initial_password = "")
    iškelia: 404 – vartotojas nerastas
    """
    user = _get_user_or_404(user_id, db)

    # Generuojame naują TOTP secret
    new_totp_secret = generate_totp_secret()
    user.totp_secret = new_totp_secret

    # Išjungiame visas aktyvias sesijas (priverstinis re-login su nauju QR)
    db.query(SessionModel).filter(SessionModel.user_id == user_id).delete()

    db.commit()
    db.refresh(user)

    # Generuojame naują QR kodą
    totp_uri = generate_totp_uri(new_totp_secret, user.username)
    qr_base64 = generate_qr_code_base64(totp_uri)

    logger.warning(
        f"Admin '{current_user.username}' resetino 2FA vartotojui: "
        f"'{user.username}' (ID={user_id}) | Visos sesijos išjungtos."
    )

    return AdminUserCreateResponse(
        user=UserResponse.model_validate(user),
        qr_code_base64=qr_base64,
        totp_uri=totp_uri,
        initial_password="",  # Slaptažodis nekeičiamas – tik 2FA reset
    )


# ============================================
# GET /api/admin/stats
# ============================================

@router.get(
    "/stats",
    response_model=UserStatsResponse,
    summary="Sistemos statistika",
    dependencies=[Depends(require_admin)],
)
def get_stats(
    db: DBSession = Depends(get_db),
) -> UserStatsResponse:
    """
    gauna: db – DB sesija
    daro: apskaičiuoja sistemos statistiką:
          - Vartotojų skaičius (visi / aktyvūs / max)
          - Failų skaičius (aktyvūs / visų laikų / aplankai)
          - Disko vietos naudojimas (suma iš users.storage_used_bytes)
          - Share linkų skaičius (visi / aktyvūs)
          - Vartotojų vietos suvestinė (overview UI)
    grąžina: (UserStatsResponse) – statistikos duomenys
    """
    from app.schemas.user import UserStorageInfo
    from app.utils.limits import (
        storage_limit_bytes as _storage_limit,
        transfer_limit_bytes as _transfer_limit,
    )

    # ── Vartotojai ─────────────────────────────────────────────────────────
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(
        User.is_active == True  # noqa: E712
    ).scalar() or 0

    # ── Failai / aplankai ──────────────────────────────────────────────────
    total_files = db.query(func.count(FileModel.id)).filter(
        FileModel.is_deleted == False  # noqa: E712
    ).scalar() or 0

    total_folders = db.query(func.count(Folder.id)).filter(
        Folder.is_deleted == False  # noqa: E712
    ).scalar() or 0

    total_uploads = db.query(func.count(FileModel.id)).scalar() or 0

    # ── Disko vieta ────────────────────────────────────────────────────────
    total_storage_bytes = db.query(func.sum(User.storage_used_bytes)).scalar() or 0
    max_storage_bytes = settings.max_storage_per_user_bytes * max(total_users, 1)

    # ── Share linkai ───────────────────────────────────────────────────────
    total_share_links = db.query(func.count(ShareLink.id)).scalar() or 0

    # Aktyvūs share linkai = ne išjungti IR neviršijo limito
    active_share_links = db.query(func.count(ShareLink.id)).filter(
        ShareLink.is_disabled == False,  # noqa: E712
        ShareLink.download_count < ShareLink.max_downloads,
    ).scalar() or 0

    # ── Vartotojų vietos suvestinė ─────────────────────────────────────────
    # Failų kiekiai per vartotoją – vienu agreguotu query
    user_file_counts = db.query(
        FileModel.user_id,
        func.count(FileModel.id).label("cnt"),
    ).filter(
        FileModel.is_deleted == False,  # noqa: E712
    ).group_by(FileModel.user_id).all()

    file_count_map: dict[int, int] = {row.user_id: row.cnt for row in user_file_counts}

    users = db.query(User).order_by(User.username.asc()).all()
    users_storage = [
        UserStorageInfo(
            id=u.id,
            username=u.username,
            storage_used_bytes=u.storage_used_bytes,
            file_count=file_count_map.get(u.id, 0),
            is_admin=u.is_admin,
            storage_limit_bytes=_storage_limit(u),
            transfer_used_bytes=u.transfer_used_bytes,
            transfer_limit_bytes=_transfer_limit(u),
        )
        for u in users
    ]

    return UserStatsResponse(
        total_users=total_users,
        active_users=active_users,
        max_users=settings.max_users,
        total_files=total_files,
        total_folders=total_folders,
        total_uploads=total_uploads,
        total_storage_bytes=total_storage_bytes,
        max_storage_bytes=max_storage_bytes,
        total_share_links=total_share_links,
        active_share_links=active_share_links,
        users_storage=users_storage,
    )
