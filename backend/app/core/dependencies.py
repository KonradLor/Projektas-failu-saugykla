"""
FastAPI priklausomybių (dependencies) modulis.

Čia apibrėžiamos Depends() funkcijos, kurias naudoja visi API endpoint'ai
vartotojo autentifikavimui ir autorizavimui.

NAUDOJIMAS ENDPOINT'UOSE:
    from fastapi import Depends
    from app.core.dependencies import get_current_user, require_admin

    # Bet kuriam prisijungusiam vartotojui:
    @router.get("/files")
    def list_files(current_user: User = Depends(get_current_user)):
        ...

    # Tik administratoriui:
    @router.get("/admin/users")
    def list_users(current_user: User = Depends(require_admin)):
        ...

SESSION TIKRINIMO SRAUTAS:
    1. Iš HTTP-only cookie'aus perskaitomas session token
    2. DB randamas Session įrašas pagal token'ą
    3. Tikrinama ar sesija negaliojusi (expires_at)
    4. Grąžinamas User objektas
"""

# ============================================
# IMPORTAI
# ============================================
import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import Session as UserSession
from app.models.user import User


# ============================================
# KONSTANTOS
# ============================================

logger = logging.getLogger(__name__)

# Cookie pavadinimas – turi sutapti su tuo, kas nustatoma login metu
SESSION_COOKIE_NAME = "konradvault_session"


# ============================================
# SESIJOS TIKRINIMAS
# ============================================

def get_session_token_from_cookie(request: Request) -> str:
    """
    gauna: request (Request) – HTTP užklausa
    daro: bando perskaityti session token'ą iš HTTP-only cookie'aus
    grąžina: (str) – session token eilutė
    iškelia: HTTPException 401 – jei cookie nėra arba tuščias
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)

    # Jei cookie nėra arba tuščias – vartotojas neprisijungęs
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neprisijungta. Prašome prisijungti.",
        )

    return token.strip()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    gauna: request (Request) – HTTP užklausa (reikalinga cookie'ui)
           db (Session)      – DB sesija (per Depends)
    daro: 1. Perskaito session token'ą iš cookie'aus
          2. Suranda sesijos įrašą DB
          3. Tikrina ar sesija negaliojusi
          4. Suranda ir grąžina User objektą
    grąžina: (User) – prisijungęs vartotojas
    iškelia: HTTPException 401 – jei neprisijungta arba sesija nebegalioja

    NAUDOJIMAS:
        @router.get("/files")
        def list_files(user: User = Depends(get_current_user)):
            ...
    """
    # 1. Gauname token'ą iš cookie'aus
    token = get_session_token_from_cookie(request)

    # 2. Ieškome sesijos DB pagal token'ą (Session.token yra PRIMARY KEY – greita)
    session = db.query(UserSession).filter(UserSession.token == token).first()

    # Jei sesijos nėra DB – token'as negaliojantis arba logout'intas
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesija nerasta arba nebegalioja. Prašome prisijungti iš naujo.",
        )

    # 3. Tikriname ar sesija dar galioja (expires_at > dabar)
    if session.is_expired:
        # Ištriname pasibaigusią sesiją iš DB (cleanup)
        db.delete(session)
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesija pasibaigė. Prašome prisijungti iš naujo.",
        )

    # 4. Gauname vartotoją pagal user_id iš sesijos
    user = db.query(User).filter(User.id == session.user_id).first()

    # Teoriškai neturėtų nutikti (CASCADE), bet patikriname
    if user is None:
        logger.error(
            f"Sesija {token[:8]}... turi user_id={session.user_id}, "
            f"bet toks vartotojas neegzistuoja DB"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Vartotojas nerastas. Prašome prisijungti iš naujo.",
        )

    # Tikriname ar vartotojas aktyvus (admin galėjo deaktyvuoti)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Jūsų paskyra deaktyvuota. Susisiekite su administratoriumi.",
        )

    return user


# ============================================
# ADMIN TIKRINIMAS
# ============================================

def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    gauna: current_user (User) – prisijungęs vartotojas (per Depends)
    daro: tikrina ar vartotojas yra administratorius
    grąžina: (User) – administratorius
    iškelia: HTTPException 403 – jei vartotojas nėra adminas

    NAUDOJIMAS (tik admin endpoint'ams):
        @router.get("/admin/users")
        def list_users(admin: User = Depends(require_admin)):
            ...
    """
    if not current_user.is_admin:
        # Loggingame – gali būti bandymas pasiekti admin resursus
        logger.warning(
            f"Vartotojas '{current_user.username}' (id={current_user.id}) "
            f"bandė pasiekti admin resursą be teisių"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Prieiga uždrausta. Reikalingos administratoriaus teisės.",
        )

    return current_user


# ============================================
# OPTIONAL VARTOTOJAS (viešiems endpoint'ams)
# ============================================

def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """
    gauna: request (Request) – HTTP užklausa
           db (Session)      – DB sesija
    daro: bando gauti prisijungusį vartotoją, bet neiškelia klaidos
          jei neprisijungta. Naudojama endpoint'ams, kurie veikia ir be login.
    grąžina: (User | None) – vartotojas jei prisijungęs, None jei ne

    NAUDOJIMAS (pvz. viešas share puslapis gali rodyti papildomą info prisijungusiam):
        @router.get("/share/{token}")
        def view_share(user: User | None = Depends(get_optional_user)):
            if user:
                # Rodyti papildomą informaciją
            ...
    """
    try:
        return get_current_user(request, db)
    except HTTPException:
        # Neprisijungęs – grąžiname None, ne klaidą
        return None
