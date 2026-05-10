"""
CLI scriptas pirmojo administratoriaus sukūrimui.

Naudojimas (iš backend/ direktorijos):
    python scripts/create_user.py --username konradas --admin
    python scripts/create_user.py --username jonas

Vykdymo eiga:
    1. Paklausiama slaptažodžio (du kartus)
    2. Generuojama TOTP paslaptis
    3. Rodomas QR kodas terminale (ASCII art)
    4. Sukuriamas vartotojas DB
    5. Rodoma patvirtinimo žinutė

REIKALAVIMAI:
    - Turi būti .env failas (su MASTER_KEY)
    - DB turi egzistuoti (paleisti alembic upgrade head arba init_db())
    - Vykdyti iš backend/ direktorijos

PAVYZDYS PALEISTI:
    cd backend
    python scripts/create_user.py --username konradas --admin
"""

# ============================================
# IMPORTAI
# ============================================
import argparse
import getpass
import sys
from pathlib import Path

# Pridedame backend/ į Python kelią (jei skriptas paleidžiamas tiesiogiai)
# scripts/create_user.py → .parent → scripts/ → .parent → backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Šitie importai turi būti PO sys.path pakeitimo
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.security import hash_password
from app.core.totp import generate_qr_code_base64, generate_totp_secret, generate_totp_uri
from app.database import SessionLocal, init_db
from app.models.user import User


# ============================================
# SPALVOTAS TERMINALAS
# ============================================

# ANSI spalvų kodai gražesniam terminalui
# Kai kuriuose Windows terminaluose neveikia – naudojame atsargiai
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def print_success(msg: str) -> None:
    """
    gauna: msg (str) – sėkmės pranešimas
    daro: išspausdina žalią pranešimą
    grąžina: None
    """
    print(f"{GREEN}✓ {msg}{RESET}")


def print_error(msg: str) -> None:
    """
    gauna: msg (str) – klaidos pranešimas
    daro: išspausdina raudoną pranešimą ir išeina
    grąžina: None (baigia programą su klaidos kodu)
    """
    print(f"{RED}✗ KLAIDA: {msg}{RESET}", file=sys.stderr)
    sys.exit(1)


def print_info(msg: str) -> None:
    """
    gauna: msg (str) – informacinis pranešimas
    daro: išspausdina geltoną pranešimą
    grąžina: None
    """
    print(f"{YELLOW}→ {msg}{RESET}")


# ============================================
# SLAPTAŽODŽIO ĮVEDIMAS
# ============================================

def prompt_password(username: str) -> str:
    """
    gauna: username (str) – vartotojo vardas (rodomas prompto metu)
    daro: interaktyviai paklausia slaptažodžio du kartus
          (apsauga nuo rašybos klaidų), tikrina minimalų ilgį
    grąžina: (str) – patvirtintas slaptažodis
    """
    print()
    print(f"{CYAN}Kuriamas vartotojas: {BOLD}{username}{RESET}")
    print()

    while True:
        # getpass slaptažodį rodo kaip **** (neatskleidžia ekrane)
        password = getpass.getpass(f"  Slaptažodis (min. 8 simboliai): ")

        if len(password) < 8:
            print(f"  {RED}Slaptažodis per trumpas (min. 8 simboliai). Bandykite dar kartą.{RESET}")
            continue

        password_confirm = getpass.getpass(f"  Pakartokite slaptažodį: ")

        if password != password_confirm:
            print(f"  {RED}Slaptažodžiai nesutampa. Bandykite dar kartą.{RESET}")
            continue

        # Slaptažodžiai sutampa ir pakankamai ilgi
        break

    return password


# ============================================
# QR KODO RODYMAS TERMINALE
# ============================================

def print_qr_to_terminal(totp_uri: str) -> None:
    """
    gauna: totp_uri (str) – otpauth:// URI
    daro: sugeneruoja QR kodą ir atspausdina jį terminale ASCII simboliais.
          Vartotojas nuskenuoja jį su Google Authenticator programėle.
    grąžina: None
    """
    try:
        import qrcode

        # Sukuriame QR kodą
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,     # Mažas dydis terminalui
            border=2,
        )
        qr.add_data(totp_uri)
        qr.make(fit=True)

        print()
        print(f"{CYAN}{'=' * 60}{RESET}")
        print(f"{BOLD}  Nuskenuokite QR kodą su Google Authenticator:{RESET}")
        print(f"{CYAN}{'=' * 60}{RESET}")
        print()

        # print_tty=True – spausdina ASCII QR kodą terminale
        qr.print_tty()

        print()
        print(f"{CYAN}{'=' * 60}{RESET}")

    except ImportError:
        # Jei qrcode nėra – rodome tik URI
        print()
        print(f"{YELLOW}  (qrcode biblioteka nerasta – QR kodas nerodomas){RESET}")
        print(f"  TOTP URI: {totp_uri}")
        print()


# ============================================
# VARTOTOJO KŪRIMAS DB
# ============================================

def create_user_in_db(
    db: DBSession,
    username: str,
    password: str,
    is_admin: bool,
) -> User:
    """
    gauna: db       (DBSession) – aktyvi DB sesija
           username (str)       – vartotojo vardas
           password (str)       – plaintext slaptažodis
           is_admin (bool)      – ar suteikti admin teises
    daro: patikrina ar username laisvas,
          sukuria User objektą su:
            - Argon2 password hash
            - nauja TOTP paslaptimi
            - nauju encryption key (sugeneruojamas encryption.py)
          Prideda ir išsaugo į DB.
    grąžina: (User) – sukurtas vartotojas
    """
    # ----------------------------------------
    # Tikriname ar username laisvas
    # ----------------------------------------
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        print_error(f"Vartotojas '{username}' jau egzistuoja DB!")

    # ----------------------------------------
    # Generuojame saugumo komponentus
    # ----------------------------------------

    print_info("Generuojamas Argon2 slaptažodžio hash...")
    password_hash = hash_password(password)

    print_info("Generuojama TOTP paslaptis (Google Authenticator)...")
    totp_secret = generate_totp_secret()

    # Generuojame vartotojo šifravimo raktą
    # Importuojame čia – apsauga nuo circular import
    print_info("Generuojamas vartotojo šifravimo raktas...")
    try:
        from app.core.encryption import generate_user_key, encrypt_user_key
        raw_user_key = generate_user_key()
        encrypted_user_key = encrypt_user_key(raw_user_key, settings.master_key)
    except ImportError:
        # encryption.py dar neimplementuotas – sukuriame vartotoją be rakto
        print(f"  {YELLOW}(encryption.py neparuoštas – raktas bus nustatytas vėliau){RESET}")
        encrypted_user_key = None

    # ----------------------------------------
    # Sukuriame User objektą
    # ----------------------------------------
    new_user = User(
        username=username,
        password_hash=password_hash,
        totp_secret=totp_secret,
        encryption_key_encrypted=encrypted_user_key,
        is_admin=is_admin,
        is_active=True,
        storage_used_bytes=0,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


# ============================================
# PAGRINDINIS SRAUTAS
# ============================================

def main() -> None:
    """
    gauna: nieko (argumentai iš sys.argv per argparse)
    daro: vykdo visą vartotojo kūrimo procesą:
          1. Parso argumentus
          2. Klausia slaptažodžio
          3. Inicializuoja DB
          4. Sukuria vartotoją
          5. Rodo QR kodą
          6. Patvirtina sėkmę
    grąžina: None
    """

    # ----------------------------------------
    # 1. Argumentų parsavimas
    # ----------------------------------------
    parser = argparse.ArgumentParser(
        description="KonradVault – naujo vartotojo kūrimas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Pavyzdžiai:\n"
            "  python scripts/create_user.py --username konradas --admin\n"
            "  python scripts/create_user.py --username jonas\n"
        ),
    )

    parser.add_argument(
        "--username",
        required=True,
        help="Vartotojo prisijungimo vardas (3-50 simbolių)",
    )

    parser.add_argument(
        "--admin",
        action="store_true",        # Jei nurodytas → True, kitaip False
        default=False,
        help="Suteikti administratoriaus teises",
    )

    args = parser.parse_args()

    # Bazinis username validavimas
    username = args.username.strip().lower()
    if len(username) < 3 or len(username) > 50:
        print_error("Vartotojo vardas turi būti 3–50 simbolių.")

    # ----------------------------------------
    # 2. Antraštė
    # ----------------------------------------
    print()
    print(f"{BOLD}{CYAN}╔══════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║       KonradVault – Vartotojo kūrimas    ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════╝{RESET}")

    role_label = f"{RED}ADMINISTRATORIUS{RESET}" if args.admin else "eilinis vartotojas"
    print(f"  Vartotojas: {BOLD}{username}{RESET}")
    print(f"  Rolė:       {role_label}")

    # ----------------------------------------
    # 3. Slaptažodžio įvedimas
    # ----------------------------------------
    password = prompt_password(username)

    # ----------------------------------------
    # 4. DB inicializavimas
    # ----------------------------------------
    print()
    print_info("Tikrinama duomenų bazė...")
    try:
        init_db()
    except Exception as exc:
        print_error(f"DB inicializacija nepavyko: {exc}")

    # ----------------------------------------
    # 5. Vartotojo kūrimas
    # ----------------------------------------
    print_info("Kuriamas vartotojas DB...")

    db = SessionLocal()
    try:
        user = create_user_in_db(
            db=db,
            username=username,
            password=password,
            is_admin=args.admin,
        )
    except SystemExit:
        # print_error() jau išspausdino žinutę ir kviečia sys.exit()
        db.close()
        raise
    except Exception as exc:
        db.close()
        print_error(f"Vartotojo kūrimas nepavyko: {exc}")
    finally:
        db.close()

    # ----------------------------------------
    # 6. TOTP QR kodo rodymas
    # ----------------------------------------
    totp_uri = generate_totp_uri(user.totp_secret, username)
    print_qr_to_terminal(totp_uri)

    # ----------------------------------------
    # 7. Patvirtinimas
    # ----------------------------------------
    print()
    print(f"{GREEN}{'=' * 60}{RESET}")
    print(f"{BOLD}{GREEN}  Vartotojas sukurtas sėkmingai!{RESET}")
    print(f"{GREEN}{'=' * 60}{RESET}")
    print()
    print(f"  Vartotojas:  {BOLD}{user.username}{RESET}")
    print(f"  Rolė:        {'Admin' if user.is_admin else 'Vartotojas'}")
    print(f"  ID:          {user.id}")
    print()
    print(f"{YELLOW}  SVARBŪS VEIKSMAI:{RESET}")
    print(f"  1. Nuskenuokite QR kodą aukščiau su Google Authenticator")
    print(f"  2. Prisijunkite per: http://<oracle-ip>/konradvault.html")
    print(f"  3. Įveskite username + password → tada 6 skaitmenų kodą")
    print()

    if args.admin:
        print(f"  {YELLOW}Admin panel pasiekiamas per: http://<oracle-ip>/admin{RESET}")
        print()


# ============================================
# PALEIDIMAS
# ============================================

if __name__ == "__main__":
    main()
