"""
Autentifikacijos API endpoint'ai.

Realizuoja 2 žingsnių prisijungimą:
    1 žingsnis → POST /api/auth/login       → password tikrinimas → temp_token
    2 žingsnis → POST /api/auth/verify-2fa  → TOTP tikrinimas    → session cookie

Papildomai:
    POST /api/auth/logout  → sesijos panaikinimas
    GET  /api/auth/me      → dabartinio vartotojo informacija

TEMP TOKEN SAUGYKLA:
    Kadangi sistema neturi Redis ar kitos cache saugyklos,
    laikiniai token'ai saugomi serveryje atmintyje (Python dict).
    Tai tinka demo projektui (keli vartotojai, vienas procesas).
    Trūkumas: server'io restart'as panaikina visus nepabaigus loginimosi.

SESSION SAUGUMAS:
    - Session token → HTTP-only cookie (JavaScript negali pasiekti)
    - Secure=True → tik per HTTPS
    - SameSite=Strict → CSRF apsauga
    - Galiojimas: settings.session_expire_hours (24h)
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.dependencies import SESSION_COOKIE_NAME, get_current_user
from app.core.encryption import encrypt_user_key, generate_user_key
from app.core.security import (
    generate_session_token,
    generate_temp_token,
    hash_password,
    verify_password,
)
from app.core.totp import (
    generate_qr_code_base64,
    generate_totp_secret,
    generate_totp_uri,
    verify_totp_code,
)
from app.database import get_db
from app.models.session import Session as UserSession
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutResponse,
    TempTokenResponse,
    TokenResponse,
    TwoFARequest,
)
from app.schemas.user import UserResponse


# ============================================
# ROUTER
# ============================================

# Visi šio failo endpoint'ai turės prefiksą /api/auth (nurodoma main.py)
router = APIRouter()

# Logger šiam moduliui
logger = logging.getLogger(__name__)


# ============================================
# LAIKINŲ TOKEN'Ų SAUGYKLA (IN-MEMORY)
# ============================================

# Laikina saugykla 2FA žingsnio token'ams
# Struktūra: { temp_token: { "user_id": int, "expires_at": datetime, "attempts": int } }
# Šie token'ai gyvena tik tarp 1 ir 2 žingsnio (5 min.)
#
# PASTABA: Tai nėra thread-safe. Jei naudotum kelis Uvicorn workers –
# reikėtų Redis. Demo projektui su vienu procesu – pakanka.
_pending_2fa: dict[str, dict] = {}


# ============================================
# DUMMY HASH (TIMING ATTACK APSAUGAI)
# ============================================
# Jei vartotojas nerastas – mes vis tiek vykdome verify_password() su
# tikru Argon2 hash'u, kad atsakymo laikas būtų vienodas.
# Generuojame jį TIK VIENĄ KARTĄ paleidimo metu.
_DUMMY_PASSWORD_HASH: str = hash_password("dummy-password-for-timing-protection")


def _cleanup_expired_temp_tokens() -> None:
    """
    gauna: nieko
    daro: pašalina pasibaigusių laikinų token'ų įrašus iš atminties.
          Kviečiama login metu – paprastas "lazy cleanup" be atskirų gijų.
    grąžina: None
    """
    now = datetime.now(timezone.utc)

    # Randame visus pasibaigusius token'us
    expired_tokens = [
        token
        for token, data in _pending_2fa.items()
        if data["expires_at"] < now
    ]

    # Pašaliname juos iš saugyklos
    for token in expired_tokens:
        del _pending_2fa[token]

    if expired_tokens:
        logger.debug(f"Išvalyti {len(expired_tokens)} pasibaigę temp token'ai")


# ============================================
# ENDPOINT'AI
# ============================================

@router.post(
    "/login",
    response_model=TempTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="1 žingsnis: slaptažodžio tikrinimas",
    description=(
        "Tikrina vartotojo vardą ir slaptažodį. "
        "Sėkmės atveju grąžina laikinąjį token'ą (galioja 5 min.) "
        "skirtą 2FA verifikacijos žingsniui."
    ),
)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> TempTokenResponse:
    """
    gauna: body (LoginRequest) – { username, password }
           db   (Session)      – DB sesija
    daro: 1. Randa vartotoją pagal username
          2. Tikrina Argon2 slaptažodžio hash
          3. Generuoja laikinąjį token'ą
          4. Įrašo token'ą į atmintį (5 min. TTL)
          5. Atnaujina last_login laiką
    grąžina: TempTokenResponse – { temp_token, expires_in_seconds }
    iškelia: HTTPException 401 – jei credentials neteisingi
             HTTPException 403 – jei paskyra deaktyvuota
    """
    # Išvalome pasibaigusius token'us (lazy cleanup)
    _cleanup_expired_temp_tokens()

    # ----------------------------------------
    # 1. Vartotojo paieška pagal username
    # ----------------------------------------

    user = db.query(User).filter(User.username == body.username).first()

    # SVARBU: Jei vartotojas nerastas, MES VISTIEK VYKDOME slaptažodžio tikrinimą
    # (su tikru Argon2 hash'u). Tai apsaugo nuo "user enumeration" atakų –
    # atsakymo laikas turi būti vienodas nepriklausomai nuo to, ar useris egzistuoja.
    if user is None:
        # Vykdome dummy verify (kad atsakymo laikas vienodas)
        verify_password(body.password, _DUMMY_PASSWORD_HASH)
        logger.warning(
            f"Nepavykęs prisijungimas – vartotojas nerastas: '{body.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neteisingas vartotojo vardas arba slaptažodis.",
        )

    # ----------------------------------------
    # 2. Slaptažodžio tikrinimas
    # ----------------------------------------

    password_correct = verify_password(body.password, user.password_hash)

    if not password_correct:
        logger.warning(
            f"Nepavykęs prisijungimas – blogas slaptažodis: vartotojas '{user.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neteisingas vartotojo vardas arba slaptažodis.",
        )

    # ----------------------------------------
    # 3. Paskyros statuso tikrinimas
    # ----------------------------------------

    if not user.is_active:
        logger.warning(
            f"Nepavykęs prisijungimas – paskyra deaktyvuota: vartotojas '{user.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Jūsų paskyra yra deaktyvuota. Susisiekite su administratoriumi.",
        )

    # ----------------------------------------
    # 4. Laikinojo token'o generavimas
    # ----------------------------------------

    temp_token = generate_temp_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.temp_token_expire_minutes
    )

    # Įrašome į atmintį
    _pending_2fa[temp_token] = {
        "user_id": user.id,
        "expires_at": expires_at,
        "attempts": 0,        # brute-force apsauga TOTP kodui
    }

    logger.info(
        f"Sėkmingas 1 žingsnis – vartotojas '{user.username}' "
        f"(id={user.id}), laukia 2FA"
    )

    return TempTokenResponse(
        temp_token=temp_token,
        expires_in_seconds=settings.temp_token_expire_minutes * 60,
    )


# ============================================
# HIBRIDINIS AUTH MODELIS
# ============================================
# Adminai: username + password + TOTP (per /login → /verify-2fa)
# Reguliarūs: username + TOTP (per /login-totp → /verify-2fa)
#
# Anti-enumeration: nežinomam vartotojui sakome „reikia password" (atrodo kaip admin),
# kad puolėjas negalėtų nustatyti, kuris username yra reguliarus, o kuris admin.

@router.post(
    "/login-method",
    status_code=status.HTTP_200_OK,
    summary="Sužinoti ar reikia slaptažodžio (admin) ar tik TOTP (reguliarus)",
)
def login_method(
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """
    gauna: body (dict) – { username }
           db          – DB sesija
    daro: pagal username nustato ar prisijungimui reikia slaptažodžio.
          Adminams – grąžina True (reikia password).
          Reguliariems – grąžina False (tik TOTP).
          Nežinomam useriui – grąžina True (anti-enumeration apsauga).
    grąžina: (dict) – { requires_password: bool }
    iškelia: 422 – jei username tuščias
    """
    username = str(body.get("username", "")).strip().lower()

    if not username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vartotojo vardas privalomas.",
        )

    user = db.query(User).filter(User.username == username).first()

    # Anti-enumeration: jei vartotojas neegzistuoja → sakome „reikia password"
    # (tarsi admin), kad puolėjas negalėtų atskirti egzistuojančių/neegzistuojančių
    if user is None:
        return {"requires_password": True}

    return {"requires_password": bool(user.is_admin)}


@router.post(
    "/login-totp",
    response_model=TempTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="1 žingsnis reguliariems vartotojams (be slaptažodžio – tik TOTP)",
)
def login_totp(
    body: dict,
    db: Session = Depends(get_db),
) -> TempTokenResponse:
    """
    gauna: body (dict) – { username }
           db          – DB sesija
    daro: pradeda prisijungimo srautą reguliariems vartotojams (be slaptažodžio).
          Adminams – grąžina 403 (jiems privaloma /login su password).
          Sėkmės atveju – generuoja temp_token, kurį naudoja /verify-2fa.
    grąžina: TempTokenResponse – { temp_token, expires_in_seconds }
    iškelia: 401 – username nerastas
             403 – useris yra adminas (turi naudoti /login)
             403 – paskyra deaktyvuota
             422 – username tuščias
    """
    _cleanup_expired_temp_tokens()

    username = str(body.get("username", "")).strip().lower()

    if not username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vartotojo vardas privalomas.",
        )

    user = db.query(User).filter(User.username == username).first()

    if user is None:
        logger.warning(
            f"Nepavykęs TOTP prisijungimas – vartotojas nerastas: '{username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neteisingas vartotojo vardas.",
        )

    # Adminai negali naudoti šio endpoint'o – jiems privaloma password + TOTP
    if user.is_admin:
        logger.warning(
            f"Adminas bandė per /login-totp: '{user.username}' – grąžinta 403"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administratoriai privalo prisijungti su slaptažodžiu.",
        )

    if not user.is_active:
        logger.warning(
            f"Nepavykęs TOTP prisijungimas – paskyra deaktyvuota: '{user.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Jūsų paskyra yra deaktyvuota. Susisiekite su administratoriumi.",
        )

    # Generuojame temp_token ir laukiame TOTP kodo per /verify-2fa
    temp_token = generate_temp_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.temp_token_expire_minutes
    )

    _pending_2fa[temp_token] = {
        "user_id": user.id,
        "expires_at": expires_at,
        "attempts": 0,
    }

    logger.info(
        f"Sėkmingas 1 žingsnis (TOTP-only) – vartotojas '{user.username}' "
        f"(id={user.id}), laukia 2FA"
    )

    return TempTokenResponse(
        temp_token=temp_token,
        expires_in_seconds=settings.temp_token_expire_minutes * 60,
    )


@router.post(
    "/verify-2fa",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="2 žingsnis: TOTP verifikacija",
    description=(
        "Tikrina 6 skaitmenų TOTP kodą iš Google Authenticator. "
        "Sėkmės atveju sukuria sesiją ir nustato HTTP-only cookie."
    ),
)
def verify_2fa(
    body: TwoFARequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    gauna: body     (TwoFARequest) – { temp_token, totp_code }
           response (Response)     – FastAPI response objektas cookie'ui nustatyti
           db       (Session)      – DB sesija
    daro: 1. Tikrina ar temp_token galioja ir nepasibaigęs
          2. Tikrina TOTP kodą (pyotp)
          3. Sukuria Session įrašą DB
          4. Nustato HTTP-only session cookie
          5. Pašalina temp_token iš atminties
    grąžina: TokenResponse – { user_id, username, is_admin, message }
    iškelia: HTTPException 401 – jei temp_token negalioja arba TOTP kodas blogas
    """
    # ----------------------------------------
    # 1. Temp token tikrinimas
    # ----------------------------------------

    pending = _pending_2fa.get(body.temp_token)

    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Laikinasis token'as negalioja arba baigėsi. Prisijunkite iš naujo.",
        )

    # Tikriname ar token'as nepasibaigęs
    if datetime.now(timezone.utc) > pending["expires_at"]:
        # Valome pasibaigusį token'ą
        del _pending_2fa[body.temp_token]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Laikinasis token'as pasibaigė (5 min. limitas). Prisijunkite iš naujo.",
        )

    user_id = pending["user_id"]

    # ----------------------------------------
    # 2. Vartotojo gavimas iš DB
    # ----------------------------------------

    user = db.query(User).filter(User.id == user_id).first()

    if user is None or not user.is_active:
        # Teoriškai neturėtų nutikti, bet patikriname
        del _pending_2fa[body.temp_token]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Vartotojas nerastas arba deaktyvuotas.",
        )

    # ----------------------------------------
    # 3. Brute-force apsauga TOTP kodui (max 5 bandymai)
    # ----------------------------------------
    if pending.get("attempts", 0) >= 5:
        del _pending_2fa[body.temp_token]
        logger.warning(
            f"Per daug nepavykusių TOTP bandymų – vartotojas '{user.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Per daug nepavykusių bandymų. Prisijunkite iš naujo.",
        )

    # ----------------------------------------
    # 4. TOTP kodo tikrinimas
    # ----------------------------------------

    totp_valid = verify_totp_code(user.totp_secret, body.totp_code)

    if not totp_valid:
        pending["attempts"] = pending.get("attempts", 0) + 1
        remaining = 5 - pending["attempts"]
        logger.warning(
            f"Blogas TOTP kodas – vartotojas '{user.username}' (id={user.id})"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Neteisingas autentifikatoriaus kodas. "
                f"Patikrinkite laiką ir bandykite dar kartą. Liko bandymų: {remaining}."
            ),
        )

    # ----------------------------------------
    # 5. Sesijos sukūrimas DB su IP / User-Agent
    # ----------------------------------------

    session_token = generate_session_token()

    # IP adresas – iš X-Forwarded-For (Nginx proxy) arba tiesioginio kliento
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )
    user_agent = request.headers.get("user-agent", "")[:500]  # ribota iki 500 simbolių

    new_session = UserSession(
        token=session_token,
        user_id=user.id,
        ip_address=client_ip,
        user_agent=user_agent or None,
    )
    db.add(new_session)

    # Atnaujiname paskutinio prisijungimo laiką vartotojui
    user.last_login = datetime.now(timezone.utc)

    # SVARBU: commit'as PRIEŠ cookie nustatymą.
    # Jei DB transakcija nepavyks – klientas negaus cookie su negaliojančiu token'u.
    db.commit()
    db.refresh(new_session)

    # ----------------------------------------
    # 6. HTTP-only cookie nustatymas (po sėkmingo commit'o)
    # ----------------------------------------

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,                              # JS negali pasiekti
        secure=not settings.debug,                  # tik per HTTPS production'e
        samesite="strict",                          # CSRF apsauga
        max_age=settings.session_expire_seconds,    # 24h
        path="/",
    )

    # ----------------------------------------
    # 7. Temp token'o pašalinimas (jis panaudotas)
    # ----------------------------------------

    del _pending_2fa[body.temp_token]

    logger.info(
        f"Sėkmingas prisijungimas – vartotojas '{user.username}' "
        f"(id={user.id}, admin={user.is_admin}, ip={client_ip})"
    )

    return TokenResponse(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        message="Prisijungta sėkmingai",
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Atsijungimas",
    description="Panaikina aktyvią sesiją ir išvalo session cookie.",
)
def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LogoutResponse:
    """
    gauna: response      (Response) – FastAPI response objektas cookie'ui išvalyti
           request       (Request)  – HTTP užklausa (cookie'ui perskaityti)
           db            (Session)  – DB sesija
           current_user  (User)     – prisijungęs vartotojas (per Depends)
    daro: 1. Perskaito session token'ą iš cookie'aus
          2. Ištrina Session įrašą iš DB
          3. Išvalo cookie vartotojo naršyklėje
    grąžina: LogoutResponse – { message }
    """
    # Gauname token'ą iš cookie (SESSION_COOKIE_NAME importuotas viršuje)
    token = request.cookies.get(SESSION_COOKIE_NAME)

    if token:
        # Ištriname sesiją iš DB
        session = db.query(UserSession).filter(UserSession.token == token).first()
        if session:
            db.delete(session)
            db.commit()

    # Ištriname cookie vartotojo naršyklėje
    # SVARBU: visi atributai (httponly/secure/samesite/path) turi sutapti su tais,
    # kuriais cookie buvo sukurta – kitaip naršyklė neištrins.
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=not settings.debug,
        samesite="strict",
    )

    logger.info(
        f"Atsijungė vartotojas '{current_user.username}' (id={current_user.id})"
    )

    return LogoutResponse(message="Atsijungta sėkmingai")


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Dabartinis vartotojas",
    description=(
        "Grąžina prisijungusio vartotojo informaciją. "
        "Naudojama frontend'e vartotojo vardui ir admin statusui patikrinti."
    ),
)
def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """
    gauna: current_user (User) – prisijungęs vartotojas (per Depends)
    daro: grąžina vartotojo informaciją JSON formatu
    grąžina: UserResponse – vartotojo duomenys (be slaptažodžio ir TOTP)
    """
    # UserResponse.model_validate() konvertuoja SQLAlchemy objektą į Pydantic schemą
    # from_attributes=True (nustatyta UserResponse) leidžia tai daryti
    return UserResponse.model_validate(current_user)


# ============================================
# GET /api/auth/me-transfer-quota
# ============================================
# Grąžina vartotojo einamojo mėnesio srauto info: used/limit/remaining/percent.
# Naudoja dashboard puslapis kvotos indicator'ui.

@router.get(
    "/me-transfer-quota",
    status_code=status.HTTP_200_OK,
    summary="Einamojo mėnesio srauto naudojimas",
    description=(
        "Grąžina vartotojo perduoto duomenų srauto info: kiek baitų sunaudota, "
        "kiek likę, kada kitas reset'as. Į skaitiklį įeina upload + download + "
        "share atsisiuntimai (savininkui)."
    ),
)
def get_transfer_quota(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    gauna: prisijungęs vartotojas
    daro: grąžina dict su naudojimu šio mėnesio srauto kvotos
    grąžina: dict (žr. transfer_quota.get_quota_info)
    """
    from app.utils.transfer_quota import get_quota_info
    info = get_quota_info(current_user, db)
    # Patikrinam, ar reikia commit'inti (jei reset įvyko per ensure_current_period)
    db.commit()
    return info


# ============================================
# VIEŠOJI REGISTRACIJA (be slaptažodžio)
# ============================================

# Laikina registracijos saugykla – kaip _pending_2fa, bet kūrimui
# Struktūra: { setup_token: { username, totp_secret, expires_at } }
_pending_registrations: dict[str, dict] = {}


@router.get(
    "/register/slots",
    status_code=status.HTTP_200_OK,
    summary="Laisvų vietų skaičius",
    description="Grąžina kiek vietų dar liko (iš max 10). Viešas endpoint'as.",
)
def get_slots(
    db: Session = Depends(get_db),
) -> dict:
    """
    gauna: db (Session) – DB sesija
    daro: suskaičiuoja esamų vartotojų skaičių ir grąžina laisvų vietų kiekį.
          Viešas – nereikalauja autentifikacijos (rodomas landing page'e).
    grąžina: (dict) – { used, total, remaining, is_full }
    """
    used = db.query(User).count()
    total = settings.max_users
    remaining = max(0, total - used)

    return {
        "used": used,
        "total": total,
        "remaining": remaining,
        "is_full": remaining == 0,
    }


@router.post(
    "/register",
    status_code=status.HTTP_200_OK,
    summary="1 registracijos žingsnis: username → QR kodas",
    description=(
        "Pradeda registraciją. Patikrina ar username laisvas ir ar yra vietų. "
        "Grąžina QR kodą Google Authenticator'iui nuskenoti."
    ),
)
def register_step1(
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """
    gauna: body (dict) – { username }
           db          – DB sesija
    daro: 1. Tikrina ar yra laisvų vietų (max 10 vartotojų)
          2. Tikrina ar username laisvas ir galiojantis
          3. Generuoja TOTP secret ir QR kodą
          4. Išsaugo laikinus duomenis atmintyje (5 min. TTL)
    grąžina: (dict) – { setup_token, qr_code_base64, totp_uri, username }
    iškelia: 403 – sistema pilna (10 vartotojų)
             409 – username jau užimtas
             422 – neteisingas username formatas
    """
    import re
    from datetime import timedelta

    username = str(body.get("username", "")).strip().lower()

    # ----------------------------------------
    # Validacija
    # ----------------------------------------
    if not username or len(username) < 3 or len(username) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vartotojo vardas turi būti 3–50 simbolių.",
        )

    if not re.match(r"^[a-z0-9_-]+$", username):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vartotojo vardas gali turėti tik mažąsias raides, skaičius, _ ir -.",
        )

    reserved = {"admin", "root", "system", "konradvault", "api", "share"}
    if username in reserved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Vardas '{username}' yra rezervuotas.",
        )

    # ----------------------------------------
    # Vietų tikrinimas
    # ----------------------------------------
    user_count = db.query(User).count()
    if user_count >= settings.max_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Sistema pilna – {settings.max_users} vietų išnaudota. "
                "Kreipkitės į administratorių."
            ),
        )

    # ----------------------------------------
    # Username užimtumo tikrinimas
    # ----------------------------------------
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vartotojas '{username}' jau egzistuoja.",
        )

    # ----------------------------------------
    # TOTP generavimas
    # ----------------------------------------
    totp_secret = generate_totp_secret()
    totp_uri = generate_totp_uri(totp_secret, username)
    qr_base64 = generate_qr_code_base64(totp_uri)

    # ----------------------------------------
    # Laikinas token'as registracijos patvirtinimui
    # ----------------------------------------
    setup_token = generate_temp_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    _pending_registrations[setup_token] = {
        "username": username,
        "totp_secret": totp_secret,
        "expires_at": expires_at,
        "attempts": 0,        # brute-force apsauga TOTP kodui (max 5 bandymai)
    }

    logger.info(f"Registracijos pradžia: '{username}' – laukia QR patvirtinimo")

    return {
        "setup_token": setup_token,
        "qr_code_base64": qr_base64,
        "totp_uri": totp_uri,
        "username": username,
    }


@router.post(
    "/register/confirm",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="2 registracijos žingsnis: TOTP patvirtinimas → paskyra sukurta",
    description=(
        "Patikrina 6 skaitmenų kodą iš Google Authenticator. "
        "Sėkmės atveju sukuria paskyrą ir iš karto prisijungia (HTTP-only cookie)."
    ),
)
def register_confirm(
    body: dict,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    gauna: body     (dict)     – { setup_token, totp_code }
           response (Response) – FastAPI response objektas cookie'ui
           db                  – DB sesija
    daro: 1. Tikrina setup_token galiojimą
          2. Tikrina 6 skaitmenų TOTP kodą
          3. Sukuria vartotoją DB (be slaptažodžio – tik TOTP autentifikacija)
          4. Sukuria sesiją ir nustato HTTP-only cookie
          5. Iš karto „prisijungia" – vartotojas nukreipiamas į dashboard
    grąžina: TokenResponse – { user_id, username, is_admin, message }
    iškelia: 401 – setup_token nebegalioja arba blogas TOTP kodas
             403 – sistema jau pilna (race condition apsauga)
             409 – username tuo tarpu užimtas (race condition)
    """
    setup_token = str(body.get("setup_token", ""))
    totp_code = str(body.get("totp_code", "")).strip()

    # ----------------------------------------
    # Setup token tikrinimas
    # ----------------------------------------
    pending = _pending_registrations.get(setup_token)

    if pending is None or datetime.now(timezone.utc) > pending["expires_at"]:
        if setup_token in _pending_registrations:
            del _pending_registrations[setup_token]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registracijos sesija pasibaigė. Pradėkite iš naujo.",
        )

    username = pending["username"]
    totp_secret = pending["totp_secret"]

    # ----------------------------------------
    # Brute-force apsauga – max 5 bandymai
    # ----------------------------------------
    if pending.get("attempts", 0) >= 5:
        del _pending_registrations[setup_token]
        logger.warning(
            f"Per daug nepavykusių TOTP bandymų registracijoje: username={username}"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Per daug nepavykusių bandymų. Pradėkite registraciją iš naujo.",
        )

    # ----------------------------------------
    # TOTP kodo tikrinimas
    # ----------------------------------------
    if not verify_totp_code(totp_secret, totp_code):
        pending["attempts"] = pending.get("attempts", 0) + 1
        remaining = 5 - pending["attempts"]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Neteisingas kodas. Patikrinkite Google Authenticator ir bandykite "
                f"dar kartą. Liko bandymų: {remaining}."
            ),
        )

    # ----------------------------------------
    # Race condition apsauga – dar kartą tikriname
    # ----------------------------------------
    user_count = db.query(User).count()
    if user_count >= settings.max_users:
        del _pending_registrations[setup_token]
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema pilna – kitas vartotojas spėjo užimti paskutinę vietą.",
        )

    if db.query(User).filter(User.username == username).first():
        del _pending_registrations[setup_token]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vartotojas '{username}' jau egzistuoja.",
        )

    # ----------------------------------------
    # Vartotojo kūrimas (be slaptažodžio)
    # Slaptažodis nenaudojamas – autentifikacija tik per TOTP
    # ----------------------------------------
    raw_user_key = generate_user_key()
    encrypted_user_key = encrypt_user_key(raw_user_key, settings.master_key)

    # Generuojame atsitiktinį vidinį slaptažodį (niekada nerodomas vartotojui)
    # Reikalingas tik nes DB stulpelis NOT NULL
    import secrets as _secrets
    internal_password = hash_password(_secrets.token_hex(32))

    new_user = User(
        username=username,
        password_hash=internal_password,
        totp_secret=totp_secret,
        encryption_key_encrypted=encrypted_user_key,
        is_admin=False,
        is_active=True,
        storage_used_bytes=0,
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_user)
    db.flush()  # Gauname new_user.id be commit'o

    # ----------------------------------------
    # Sesijos sukūrimas – iš karto prisijungiame (su IP/User-Agent)
    # ----------------------------------------
    session_token = generate_session_token()

    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )
    user_agent = request.headers.get("user-agent", "")[:500]

    new_session = UserSession(
        token=session_token,
        user_id=new_user.id,
        ip_address=client_ip,
        user_agent=user_agent or None,
    )
    db.add(new_session)

    new_user.last_login = datetime.now(timezone.utc)

    # SVARBU: commit prieš cookie nustatymą
    db.commit()

    # HTTP-only session cookie (tik po sėkmingo commit'o)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=not settings.debug,
        samesite="strict",
        max_age=settings.session_expire_seconds,
        path="/",
    )

    # Valome setup token
    del _pending_registrations[setup_token]

    logger.info(
        f"Naujas vartotojas užsiregistravo: '{username}' (id={new_user.id})"
    )

    return TokenResponse(
        user_id=new_user.id,
        username=new_user.username,
        is_admin=new_user.is_admin,
        message=f"Sveiki, {username}! Paskyra sukurta sėkmingai.",
    )
