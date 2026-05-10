"""
CLI scriptas vartotojo 2FA (TOTP) atstatymui.

Naudojamas kai:
  - Vartotojas prarado telefoną ar Google Authenticator prieigą
  - QR kodas nuskaitytas neteisingai ir niekada neveikė
  - Kitos aplinkybės dėl kurių vartotojas negali prisijungti

Naudojimas (iš backend/ direktorijos):
    python scripts/reset_2fa.py --username konradas
    python scripts/reset_2fa.py --username jonas --force
    python scripts/reset_2fa.py --list

Vykdymo eiga:
    1. Randamas vartotojas DB pagal username
    2. Prašoma patvirtinimo (nebent --force)
    3. Generuojama nauja TOTP paslaptis
    4. Atnaujinama DB
    5. Visos aktyvios sesijos anuliuojamos (saugumas)
    6. Rodomas naujas QR kodas terminale

REIKALAVIMAI:
    - Turi būti .env failas (su MASTER_KEY)
    - DB turi egzistuoti ir turėti vartotojus
    - Vykdyti iš backend/ direktorijos

PAVYZDŽIAI:
    cd backend
    python scripts/reset_2fa.py --list
    python scripts/reset_2fa.py --username konradas
    python scripts/reset_2fa.py --username jonas --force
"""

# ============================================
# IMPORTAI
# ============================================
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Pridedame backend/ į Python kelią (skriptas paleidžiamas tiesiogiai)
# scripts/reset_2fa.py → .parent → scripts/ → .parent → backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Šitie importai turi būti PO sys.path pakeitimo
from app.core.totp import generate_totp_secret, generate_totp_uri
from app.database import SessionLocal, init_db
from app.models.session import Session as SessionModel
from app.models.user import User


# ============================================
# SPALVOTAS TERMINALAS (ANSI)
# ============================================

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def ok(msg: str) -> None:
    """Žalias sėkmės pranešimas."""
    print(f"{GREEN}✓ {msg}{RESET}")


def err(msg: str) -> None:
    """Raudonas klaidos pranešimas, baigia programą."""
    print(f"{RED}✗ KLAIDA: {msg}{RESET}", file=sys.stderr)
    sys.exit(1)


def info(msg: str) -> None:
    """Geltonas informacinis pranešimas."""
    print(f"{YELLOW}→ {msg}{RESET}")


def dim(msg: str) -> None:
    """Blankus pagalbinis tekstas."""
    print(f"{DIM}  {msg}{RESET}")


def sep(char: str = "─", width: int = 58) -> None:
    """Horizontali linija."""
    print(f"{DIM}{char * width}{RESET}")


# ============================================
# QR KODO RODYMAS TERMINALE
# ============================================

def print_qr_to_terminal(totp_uri: str, username: str) -> None:
    """
    gauna: totp_uri (str)  – otpauth:// URI su nauju TOTP sekretanu
           username (str)  – vartotojo vardas (rodomas antraštėje)
    daro: generuoja ASCII QR kodą ir atspausdina jį terminale.
          Jei qrcode biblioteka nerasta – rodo tik URI.
    grąžina: None
    """
    print()
    print(f"{CYAN}{'═' * 58}{RESET}")
    print(f"{BOLD}  📱 Naujas QR kodas vartotojui: {username}{RESET}")
    print(f"{CYAN}{'═' * 58}{RESET}")

    try:
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=2,
        )
        qr.add_data(totp_uri)
        qr.make(fit=True)
        print()
        qr.print_tty()
        print()

    except ImportError:
        print()
        print(f"{YELLOW}  (qrcode biblioteka nerasta – QR kodas nerodomas){RESET}")
        print(f"  TOTP URI:")
        print(f"  {DIM}{totp_uri}{RESET}")
        print()

    print(f"{CYAN}{'═' * 58}{RESET}")


# ============================================
# VARTOTOJŲ SĄRAŠAS
# ============================================

def cmd_list() -> None:
    """
    gauna: nieko
    daro: atspausdina visų vartotojų sąrašą iš DB su pagrindiniais duomenimis.
          Naudingas norint pamatyti kokie vartotojai egzistuoja prieš reset'ą.
    grąžina: None
    """
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.id).all()
    finally:
        db.close()

    if not users:
        print()
        print(f"{YELLOW}  DB neturi nė vieno vartotojo.{RESET}")
        print()
        return

    print()
    print(f"{BOLD}  {'ID':<5} {'Vartotojas':<20} {'Rolė':<15} {'Aktyvus':<10} {'Sukurtas'}{RESET}")
    sep()

    for u in users:
        role   = f"{RED}Admin{RESET}"   if u.is_admin  else "Vartotojas"
        active = f"{GREEN}Taip{RESET}"  if u.is_active else f"{RED}Ne{RESET}"
        date   = u.created_at.strftime("%Y-%m-%d") if u.created_at else "—"
        print(f"  {u.id:<5} {u.username:<20} {role:<24} {active:<19} {date}")

    sep()
    print(f"  Iš viso: {len(users)} vartotojas(-ų)")
    print()


# ============================================
# 2FA ATSTATYMAS
# ============================================

def cmd_reset(username: str, force: bool) -> None:
    """
    gauna: username (str) – vartotojo vardas, kuriam atstatyti 2FA
           force (bool)   – True = neklausia patvirtinimo
    daro:
      1. Randa vartotoją DB pagal username
      2. Prašo patvirtinimo (nebent force=True)
      3. Sugeneruoja naują TOTP paslaptį
      4. Atnaujina totp_secret DB
      5. Anuliuoja visas vartotojo aktyvias sesijas (saugumas)
      6. Rodo naują QR kodą terminale
    grąžina: None
    """

    # ----------------------------------------
    # 1. Rasti vartotoją
    # ----------------------------------------
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()

        if not user:
            err(
                f"Vartotojas '{username}' nerastas DB.\n"
                f"  Naudokite --list norėdami pamatyti visus vartotojus."
            )

        # ----------------------------------------
        # 2. Rodyti informaciją apie vartotoją
        # ----------------------------------------
        print()
        print(f"{BOLD}  Vartotojas rastas:{RESET}")
        sep()
        print(f"  ID:       {user.id}")
        print(f"  Vardas:   {BOLD}{user.username}{RESET}")
        print(f"  Rolė:     {'Administratorius' if user.is_admin else 'Vartotojas'}")
        print(f"  Aktyvus:  {'Taip' if user.is_active else 'Ne'}")
        sep()

        # ----------------------------------------
        # 3. Patvirtinimas (nebent --force)
        # ----------------------------------------
        if not force:
            print()
            print(f"{YELLOW}  ⚠  Tai atstatys 2FA raktą vartotojui '{username}'.{RESET}")
            print(f"{YELLOW}     Visos aktyvios sesijos bus anuliuotos.{RESET}")
            print()
            answer = input(f"  Tęsti? (įveskite '{username}' patvirtinimui): ").strip()

            if answer != username:
                print()
                print(f"{DIM}  Atšaukta.{RESET}")
                print()
                return

        # ----------------------------------------
        # 4. Generuoti naują TOTP paslaptį
        # ----------------------------------------
        print()
        info("Generuojama nauja TOTP paslaptis...")

        new_secret = generate_totp_secret()
        old_secret = user.totp_secret   # saugome žurnalui

        user.totp_secret = new_secret

        # ----------------------------------------
        # 5. Anuliuoti visas sesijas
        # ----------------------------------------
        info("Anuliuojamos visos aktyvios sesijos...")

        now = datetime.now(timezone.utc)
        invalidated = (
            db.query(SessionModel)
            .filter(SessionModel.user_id == user.id)
            .all()
        )
        session_count = len(invalidated)

        for s in invalidated:
            # Nustatome expires_at į praeities laiką – sesija tampa negaliojančia
            s.expires_at = now

        # ----------------------------------------
        # 6. Išsaugoti DB
        # ----------------------------------------
        db.commit()
        db.refresh(user)

        # ----------------------------------------
        # 7. Rodyti QR kodą
        # ----------------------------------------
        totp_uri = generate_totp_uri(new_secret, user.username)
        print_qr_to_terminal(totp_uri, user.username)

        # ----------------------------------------
        # 8. Patvirtinimo santrauka
        # ----------------------------------------
        print()
        print(f"{GREEN}{'═' * 58}{RESET}")
        print(f"{BOLD}{GREEN}  2FA sėkmingai atstatytas!{RESET}")
        print(f"{GREEN}{'═' * 58}{RESET}")
        print()
        print(f"  Vartotojas:         {BOLD}{user.username}{RESET}")
        print(f"  Anuliuotos sesijos: {session_count}")
        print()
        print(f"{YELLOW}  VEIKSMAI VARTOTOJUI:{RESET}")
        print(f"  1. Nuskenuokite QR kodą aukščiau su Google Authenticator")
        print(f"  2. Eikite į: http://<oracle-ip>/konradvault.html")
        print(f"  3. Įveskite username → naują 6 skaitmenų kodą")
        print()
        print(f"{DIM}  Senas TOTP sekretas: {old_secret[:8]}... (pakeistas){RESET}")
        print()

    except Exception as exc:
        db.rollback()
        err(f"Nepavyko atstatyti 2FA: {exc}")
    finally:
        db.close()


# ============================================
# PAGRINDINIS SRAUTAS
# ============================================

def main() -> None:
    """
    gauna: nieko (argumentai iš sys.argv per argparse)
    daro: parso komandų eilutės argumentus ir iškviečia atitinkamą komandą:
            --list     → parodo visus vartotojus
            --username → atlieka 2FA reset'ą
    grąžina: None
    """

    # ----------------------------------------
    # Argumentų parsavimas
    # ----------------------------------------
    parser = argparse.ArgumentParser(
        description="KonradVault – 2FA (TOTP) atstatymas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Pavyzdžiai:\n"
            "  python scripts/reset_2fa.py --list\n"
            "  python scripts/reset_2fa.py --username konradas\n"
            "  python scripts/reset_2fa.py --username jonas --force\n"
        ),
    )

    parser.add_argument(
        "--username",
        metavar="VARDAS",
        help="Vartotojo vardas, kuriam atstatyti 2FA",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="Parodyti visų vartotojų sąrašą",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Neklausti patvirtinimo (naudoti atsargiai!)",
    )

    args = parser.parse_args()

    # ----------------------------------------
    # Antraštė
    # ----------------------------------------
    print()
    print(f"{BOLD}{CYAN}╔══════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║       KonradVault – 2FA atstatymo įrankis        ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════╝{RESET}")

    # ----------------------------------------
    # Validacija
    # ----------------------------------------
    if not args.list and not args.username:
        parser.print_help()
        print()
        err("Nurodykite --username arba --list.")

    # ----------------------------------------
    # DB inicializavimas
    # ----------------------------------------
    try:
        init_db()
    except Exception as exc:
        err(f"DB inicializacija nepavyko: {exc}")

    # ----------------------------------------
    # Komandos vykdymas
    # ----------------------------------------
    if args.list:
        cmd_list()

    if args.username:
        username = args.username.strip().lower()
        if len(username) < 1:
            err("Vartotojo vardas negali būti tuščias.")
        cmd_reset(username=username, force=args.force)


# ============================================
# PALEIDIMAS
# ============================================

if __name__ == "__main__":
    main()
