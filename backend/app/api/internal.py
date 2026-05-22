"""
Internal API – service-to-service endpoint'ai (NE vartotojams).

PRIEIGOS KONTROLĖ:
    NĖRA vartotojo sesijos. Vietoje to – bendras slaptas tokenas antraštėje
    X-Internal-Token, kuris turi sutapti su settings.internal_api_token.
    Pasiekiama TIK per vidinį "web" docker tinklą (Caddy šių kelių neatveria viešai).

ENDPOINT'AI:
    POST /api/internal/set-active – aktyvuoti/deaktyvuoti vartotoją pagal username.
        Naudoja centrinė admin panelė (dashboard), kad išjungimas Authentik'e
        IŠKART pasiektų ir vault (vault tikrina is_active kiekvienoje užklausoje,
        tad deaktyvuotas vartotojas blokuojamas nedelsiant; papildomai nutraukiamos
        jo aktyvios sesijos).
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app.api.admin import _delete_all_user_files_from_disk
from app.config import settings
from app.database import get_db
from app.models.session import Session as SessionModel
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    """Patikrina bendrą service-to-service tokeną. Klaidos atveju – 401."""
    expected = settings.internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="vidiniai endpoint'ai išjungti")
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(status_code=401, detail="neteisingas vidinis tokenas")


class SetActivePayload(BaseModel):
    username: str
    is_active: bool


@router.post("/set-active", dependencies=[Depends(require_internal_token)])
def set_active(payload: SetActivePayload, db: DBSession = Depends(get_db)) -> dict:
    """Nustato vartotojo is_active pagal username. Jei deaktyvuojama – papildomai
    ištrina visas jo sesijas (kad esama prisijungimo sesija nustotų veikti iškart)."""
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None:
        # Vartotojas niekada nesijungė į vault – nieko nedarom, bet grąžinam OK
        # (kad centrinė panelė nelaikytų to klaida).
        return {"ok": True, "found": False, "username": payload.username}

    user.is_active = payload.is_active
    dropped = 0
    if not payload.is_active:
        dropped = db.query(SessionModel).filter(SessionModel.user_id == user.id).delete()
    db.commit()

    logger.info(
        f"[internal] set-active username='{payload.username}' "
        f"is_active={payload.is_active} (sesijų nutraukta: {dropped})"
    )
    return {"ok": True, "found": True, "is_active": user.is_active, "sessions_dropped": dropped}


class DeleteUserPayload(BaseModel):
    username: str


@router.post("/delete-user", dependencies=[Depends(require_internal_token)])
def delete_user(payload: DeleteUserPayload, db: DBSession = Depends(get_db)) -> dict:
    """NEGRĮŽTAMAI ištrina vartotoją pagal username ir VISĄ jo turinį:
    1) visi failai iš disko (šifruoti UUID blobai + thumbnail'ai),
    2) User įrašas iš DB -> CASCADE ištrina sessions/files/folders/share_links.
    Panaudoja tą pačią logiką kaip admin DELETE /api/admin/users/{id}.
    Jei vartotojo vault'e nėra (niekada nesijungė) - grąžina found=False (ne klaida)."""
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None:
        return {"ok": True, "found": False, "username": payload.username, "deleted_files": 0}

    user_id = user.id
    # 1) Failai iš disko PRIEŠ DB trynimą (reikia stored_filename)
    deleted_files = _delete_all_user_files_from_disk(user_id, db)
    # 2) User iš DB (CASCADE ištrina sessions/files/folders/share_links)
    db.delete(user)
    db.commit()

    logger.warning(
        f"[internal] NEGRĮŽTAMAI IŠTRINTAS vartotojas '{payload.username}' "
        f"(id={user_id}) | failai iš disko: {deleted_files}"
    )
    return {"ok": True, "found": True, "deleted_files": deleted_files}
