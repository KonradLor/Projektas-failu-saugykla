#!/usr/bin/env bash
# ==============================================================================
#  KonradVault – Deploy scriptas
#  Oracle Cloud Always Free (ARM Ubuntu 22.04)
#
#  NAUDOJIMAS:
#    chmod +x deployment/deploy.sh
#    sudo bash deployment/deploy.sh          # pirmas diegimas
#    sudo bash deployment/deploy.sh update   # atnaujinimas (git pull)
#    sudo bash deployment/deploy.sh status   # sistemos būsena
#    sudo bash deployment/deploy.sh logs     # live logai
#
#  REIKALAVIMAI:
#    - Ubuntu 22.04 ARM64 (Oracle Cloud)
#    - Python 3.11+ įdiegtas
#    - Nginx įdiegtas
#    - git repozitorija: /opt/konradvault/repo
#    - .env failas: /opt/konradvault/backend/.env
# ==============================================================================

set -euo pipefail   # Sustoti prie bet kokios klaidos, neleisti neapibrėžtų kintamųjų

# ── Spalvos ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'  # No Color (reset)

ok()   { echo -e "${GREEN}✓ ${*}${NC}"; }
err()  { echo -e "${RED}✗ KLAIDA: ${*}${NC}" >&2; exit 1; }
info() { echo -e "${YELLOW}→ ${*}${NC}"; }
head() { echo -e "\n${BOLD}${CYAN}── ${*}${NC}"; }

# ── Konstantos ─────────────────────────────────────────────────────────────────
APP_USER="konradvault"
APP_GROUP="konradvault"

REPO_DIR="/opt/konradvault/repo"
APP_DIR="/opt/konradvault"
BACKEND_DIR="${APP_DIR}/backend"
FRONTEND_DIR="${APP_DIR}/frontend"
VENV_DIR="${APP_DIR}/venv"
ENV_FILE="${BACKEND_DIR}/.env"

DATA_DIR="/var/konradvault"
ENCRYPTED_DIR="${DATA_DIR}/encrypted"
LOG_DIR="/var/log/konradvault"

NGINX_CONF="/etc/nginx/sites-available/konradvault"
NGINX_ENABLED="/etc/nginx/sites-enabled/konradvault"
SERVICE_FILE="/etc/systemd/system/konradvault.service"

# Python binary aptinkamas automatiškai: bandome 3.12 → 3.11 → 3.10 (min)
# Galima nustatyti rankiniu būdu: PYTHON_BIN=python3.11 sudo bash deploy.sh
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
    for v in python3.12 python3.11 python3.10 python3; do
        if command -v "$v" &>/dev/null; then
            PYV=$("$v" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            # Tikrinam, kad bent 3.10
            if "$v" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
                PYTHON_BIN="$v"
                break
            fi
        fi
    done
fi

SERVICE_NAME="konradvault"

# ── Root tikrinimas ────────────────────────────────────────────────────────────
check_root() {
    if [[ $EUID -ne 0 ]]; then
        err "Šis scriptas turi būti paleidžiamas su sudo arba root teisėmis.\n  sudo bash deployment/deploy.sh"
    fi
}

# ── Komandų egzistavimo tikrinimas ─────────────────────────────────────────────
require_cmd() {
    command -v "$1" &>/dev/null || err "Komanda '$1' nerasta. Įdiekite: apt install $2"
}

# ==============================================================================
#  PIRMAS DIEGIMAS (install)
# ==============================================================================
cmd_install() {
    head "KonradVault – Pirmas diegimas"

    check_root
    require_cmd nginx      nginx
    require_cmd git        git

    # Python tikrinimas atskirai – PYTHON_BIN pasirinktas automatiškai
    if [[ -z "${PYTHON_BIN}" ]]; then
        err "Python 3.10+ nerastas. Įdiekite: sudo apt install python3.11 python3.11-venv"
    fi
    PYV=$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    info "Naudojama Python versija: ${PYV} (${PYTHON_BIN})"

    # Patikrinam ar python venv modulis prieinamas
    if ! "${PYTHON_BIN}" -m venv --help &>/dev/null; then
        err "Python venv modulis nerastas. Įdiekite: sudo apt install ${PYTHON_BIN}-venv"
    fi

    # ── 1. Sistemos vartotojas ─────────────────────────────────────────────────
    head "1/9  Sistemos vartotojas"
    if id "${APP_USER}" &>/dev/null; then
        ok "Vartotojas '${APP_USER}' jau egzistuoja"
    else
        useradd \
            --system \
            --no-create-home \
            --shell /bin/false \
            --comment "KonradVault servisas" \
            "${APP_USER}"
        ok "Vartotojas '${APP_USER}' sukurtas"
    fi

    # ── 2. Direktorijos ────────────────────────────────────────────────────────
    head "2/9  Direktorijų struktūra"
    mkdir -p "${APP_DIR}" "${BACKEND_DIR}" "${FRONTEND_DIR}"
    mkdir -p "${ENCRYPTED_DIR}" "${LOG_DIR}"

    # Teisės duomenų direktorijoms
    chown -R "${APP_USER}:${APP_GROUP}" "${DATA_DIR}" "${LOG_DIR}"
    chmod 750 "${ENCRYPTED_DIR}"
    chmod 755 "${LOG_DIR}"

    # App dir – rašo tik root diegiant, skaito konradvault servisas
    chown -R root:${APP_GROUP} "${APP_DIR}"
    chmod 750 "${APP_DIR}"

    ok "Direktorijos sukurtos"

    # ── 3. Kodo kopijavimas ────────────────────────────────────────────────────
    head "3/9  Kodo kopijavimas"
    _copy_code
    ok "Kodas nukopijuotas"

    # ── 4. Python venv ─────────────────────────────────────────────────────────
    head "4/9  Python virtualios aplinkos kūrimas"
    _setup_venv
    ok "Python venv sukurtas ir dependencies įdiegtos"

    # ── 5. .env failo tikrinimas ───────────────────────────────────────────────
    head "5/9  .env konfigūracija"
    _check_env
    ok ".env failas rastas"

    # ── 6. DB migracija ────────────────────────────────────────────────────────
    head "6/9  Duomenų bazės inicializavimas"
    _run_migrations
    ok "DB sukurta / migruota"

    # ── 7. Nginx konfigūracija ─────────────────────────────────────────────────
    head "7/9  Nginx konfigūracija"
    _setup_nginx
    ok "Nginx sukonfigūruotas"

    # ── 8. systemd servisas ────────────────────────────────────────────────────
    head "8/9  systemd servisas"
    _setup_service
    ok "Servisas įdiegtas ir paleistas"

    # ── 9. Santrauka ───────────────────────────────────────────────────────────
    head "9/9  Diegimas baigtas"
    _print_summary
}

# ==============================================================================
#  ATNAUJINIMAS (update)
# ==============================================================================
cmd_update() {
    head "KonradVault – Atnaujinimas"

    check_root

    info "Stabdomas servisas..."
    systemctl stop "${SERVICE_NAME}" || true

    info "Kopijuojamas naujas kodas..."
    _copy_code

    info "Atnaujinamos dependencies..."
    _setup_venv

    info "Vykdomos DB migracijos..."
    _run_migrations

    info "Paleidžiamas servisas..."
    systemctl start "${SERVICE_NAME}"

    info "Perkraunamas Nginx..."
    nginx -t && systemctl reload nginx

    ok "Atnaujinimas baigtas!"
    echo ""
    systemctl status "${SERVICE_NAME}" --no-pager -l || true
}

# ==============================================================================
#  BŪSENA (status)
# ==============================================================================
cmd_status() {
    echo ""
    echo -e "${BOLD}${CYAN}── KonradVault būsena ──────────────────────────────${NC}"

    echo ""
    echo -e "${BOLD}Servisas:${NC}"
    systemctl status "${SERVICE_NAME}" --no-pager -l 2>/dev/null || \
        echo -e "  ${RED}Servisas nerastas${NC}"

    echo ""
    echo -e "${BOLD}Nginx:${NC}"
    systemctl status nginx --no-pager | head -5 2>/dev/null || true

    echo ""
    echo -e "${BOLD}Disko vieta:${NC}"
    df -h "${DATA_DIR}" 2>/dev/null || echo "  ${DATA_DIR} neegzistuoja"

    echo ""
    echo -e "${BOLD}Šifruoti failai:${NC}"
    if [[ -d "${ENCRYPTED_DIR}" ]]; then
        FILE_COUNT=$(find "${ENCRYPTED_DIR}" -type f 2>/dev/null | wc -l)
        DISK_USED=$(du -sh "${ENCRYPTED_DIR}" 2>/dev/null | cut -f1)
        echo "  Failų: ${FILE_COUNT}  |  Disko vieta: ${DISK_USED}"
    else
        echo "  Direktorija nerasta"
    fi

    echo ""
}

# ==============================================================================
#  LIVE LOGAI (logs)
# ==============================================================================
cmd_logs() {
    echo -e "${CYAN}Rodomi live logai (Ctrl+C norint sustoti)...${NC}"
    journalctl -u "${SERVICE_NAME}" -f --no-pager
}

# ==============================================================================
#  PAGALBINĖS FUNKCIJOS
# ==============================================================================

# Kodo kopijavimas iš repo arba dabartinės direktorijos
_copy_code() {
    # Nustatome kur yra šis scriptas → reiškia esame repo šaknyje
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

    # Backend
    rsync -a --delete \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude 'konradvault.db' \
        "${REPO_ROOT}/backend/" \
        "${BACKEND_DIR}/"

    # Frontend
    rsync -a --delete \
        "${REPO_ROOT}/frontend/" \
        "${FRONTEND_DIR}/"

    # Teisės
    chown -R root:${APP_GROUP} "${BACKEND_DIR}" "${FRONTEND_DIR}"
    chmod -R 640 "${BACKEND_DIR}"
    find "${BACKEND_DIR}" -type d -exec chmod 750 {} \;
    chmod -R 644 "${FRONTEND_DIR}"
    find "${FRONTEND_DIR}" -type d -exec chmod 755 {} \;

    # .env – jei egzistuoja saugo
    if [[ -f "${ENV_FILE}" ]]; then
        chown "${APP_USER}:${APP_GROUP}" "${ENV_FILE}"
        chmod 600 "${ENV_FILE}"
    fi
}

# Python venv sukūrimas arba atnaujinimas
_setup_venv() {
    if [[ ! -d "${VENV_DIR}" ]]; then
        info "Kuriamas Python venv..."
        "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    fi

    # Atnaujiname pip ir įdiegiame dependencies
    "${VENV_DIR}/bin/pip" install --upgrade pip --quiet
    "${VENV_DIR}/bin/pip" install \
        --requirement "${BACKEND_DIR}/requirements.txt" \
        --quiet

    # uvloop – greičiausias async event loop (Oracle ARM palaiko)
    "${VENV_DIR}/bin/pip" install uvloop --quiet 2>/dev/null || \
        info "uvloop neįdiegtas – naudosimas standartinis asyncio"

    chown -R root:${APP_GROUP} "${VENV_DIR}"
    chmod -R 750 "${VENV_DIR}"
}

# .env failo tikrinimas
_check_env() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        echo ""
        echo -e "${RED}╔══════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║  .env FAILAS NERASTAS!                               ║${NC}"
        echo -e "${RED}╚══════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "  Sukurkite .env failą pagal šabloną:"
        echo -e "  ${CYAN}cp ${BACKEND_DIR}/.env.example ${ENV_FILE}${NC}"
        echo ""
        echo -e "  Sugeneruokite raktus:"
        echo -e "  ${CYAN}${VENV_DIR}/bin/python -c \\"
        echo -e "    \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"${NC}"
        echo ""
        echo -e "  Tada užpildykite MASTER_KEY ir SECRET_KEY reikšmes."
        echo ""

        # Paklausiam – gal vartotojas nori sukurti dabar
        read -rp "Ar norite sukurti .env dabar? (t/n): " CREATE_ENV
        if [[ "${CREATE_ENV,,}" == "t" ]]; then
            _interactive_env_setup
        else
            err ".env failas privalomas. Diegimas sustabdytas."
        fi
    fi

    # Tikriname ar raktai nėra default reikšmės
    if grep -q "PAKEISK_MANE" "${ENV_FILE}" 2>/dev/null; then
        err ".env faile yra PAKEISK_MANE reikšmės – užpildykite raktus prieš diegiant."
    fi

    chown "${APP_USER}:${APP_GROUP}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
}

# Interaktyvus .env sukūrimas (jei nėra)
_interactive_env_setup() {
    info "Kopijuojamas .env.example..."
    cp "${BACKEND_DIR}/.env.example" "${ENV_FILE}"

    info "Generuojami raktai..."
    MASTER_KEY=$("${VENV_DIR}/bin/python" -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    SECRET_KEY=$("${VENV_DIR}/bin/python" -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

    # Pakeičiame placeholder'ius
    sed -i "s|PAKEISK_MANE_SUGENERUOTU_RAKTU|${MASTER_KEY}|g" "${ENV_FILE}"
    sed -i "s|PAKEISK_MANE_KITU_SUGENERUOTU_RAKTU|${SECRET_KEY}|g" "${ENV_FILE}"

    # Production keliai
    sed -i "s|DATABASE_URL=.*|DATABASE_URL=sqlite:////var/konradvault/konradvault.db|" "${ENV_FILE}"
    sed -i "s|ENCRYPTED_FILES_DIR=.*|ENCRYPTED_FILES_DIR=/var/konradvault/encrypted|" "${ENV_FILE}"
    sed -i "s|LOG_DIR=.*|LOG_DIR=/var/log/konradvault|" "${ENV_FILE}"
    sed -i "s|BASE_URL=.*|BASE_URL=https://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_SERVER_IP')|" "${ENV_FILE}"
    sed -i "s|DEBUG=.*|DEBUG=False|" "${ENV_FILE}"

    chown "${APP_USER}:${APP_GROUP}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"

    ok ".env sukurtas su sugeneruotais raktais"

    echo ""
    echo -e "${YELLOW}  ⚠  LABAI SVARBU – IŠSAUGOKITE ŠIUOS RAKTUS:${NC}"
    echo ""
    echo -e "  MASTER_KEY=${BOLD}${MASTER_KEY}${NC}"
    echo -e "  SECRET_KEY=${BOLD}${SECRET_KEY}${NC}"
    echo ""
    echo -e "${RED}  Praradus MASTER_KEY – VISI FAILAI PRARASTI AMŽINAI!${NC}"
    echo -e "${YELLOW}  Sukurkite backup'ą dabar.${NC}"
    echo ""
    read -rp "  Patvirtinkite, kad išsaugojote raktus (t/n): " CONFIRM_KEYS
    if [[ "${CONFIRM_KEYS,,}" != "t" ]]; then
        err "Raktai nepatvirtinti. Diegimas sustabdytas – išsaugokite raktus ir paleiskite iš naujo."
    fi
}

# DB migracija
_run_migrations() {
    cd "${BACKEND_DIR}"

    # Sukuriame DB direktoriją jei reikia
    DB_DIR="/var/konradvault"
    mkdir -p "${DB_DIR}"
    chown "${APP_USER}:${APP_GROUP}" "${DB_DIR}"
    chmod 750 "${DB_DIR}"

    # Paleidžiame Alembic migraciją
    sudo -u "${APP_USER}" \
        "${VENV_DIR}/bin/python" -m alembic upgrade head \
        || {
            # Jei Alembic nepavyksta (pvz. pirmą kartą be alembic_version) –
            # tiesiogiai inicializuojame DB
            info "Alembic nepavyko – inicializuojama DB tiesiogiai..."
            sudo -u "${APP_USER}" \
                PYTHONPATH="${BACKEND_DIR}" \
                "${VENV_DIR}/bin/python" -c "from app.database import init_db; init_db()"
        }

    # DB failo teisės
    DB_FILE="/var/konradvault/konradvault.db"
    if [[ -f "${DB_FILE}" ]]; then
        chown "${APP_USER}:${APP_GROUP}" "${DB_FILE}"
        chmod 640 "${DB_FILE}"
    fi
}

# Nginx konfigūracija
_setup_nginx() {
    # Kopijuojame konfigūraciją
    cp "$(dirname "${BASH_SOURCE[0]}")/nginx.conf" "${NGINX_CONF}"

    # Įjungiame (symlink)
    ln -sf "${NGINX_CONF}" "${NGINX_ENABLED}"

    # Ištriname default konfigūraciją jei egzistuoja
    rm -f /etc/nginx/sites-enabled/default

    # Tikriname konfigūraciją
    nginx -t || err "Nginx konfigūracija klaidinga. Patikrinkite ${NGINX_CONF}"

    # SSL sertifikato tikrinimas
    SSL_CERT="/etc/ssl/konradvault/konradvault.crt"
    SSL_KEY="/etc/ssl/konradvault/konradvault.key"

    if [[ ! -f "${SSL_CERT}" || ! -f "${SSL_KEY}" ]]; then
        info "SSL sertifikatas nerastas – generuojamas self-signed..."
        mkdir -p /etc/ssl/konradvault
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "${SSL_KEY}" \
            -out "${SSL_CERT}" \
            -subj "/C=LT/ST=Vilnius/L=Vilnius/O=KonradVault/CN=$(hostname -I | awk '{print $1}')" \
            -quiet
        chmod 600 "${SSL_KEY}"
        chmod 644 "${SSL_CERT}"
        ok "Self-signed SSL sertifikatas sugeneruotas (galioja 365 dienų)"
    else
        ok "SSL sertifikatas rastas"
    fi

    # Perkrauname Nginx
    systemctl reload nginx || systemctl start nginx
}

# systemd servisas
_setup_service() {
    cp "$(dirname "${BASH_SOURCE[0]}")/konradvault.service" "${SERVICE_FILE}"
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"

    # Palaukiame 3s ir tikriname ar paleistas
    sleep 3
    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
        echo ""
        echo -e "${RED}Servisas nepasileido! Logai:${NC}"
        journalctl -u "${SERVICE_NAME}" -n 30 --no-pager
        err "Servisas nepasileido. Patikrinkite logus aukščiau."
    fi
}

# Pabaigos santrauka
_print_summary() {
    SERVER_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║       KonradVault sėkmingai įdiegtas!                ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  🌐  Adresas:     ${BOLD}https://${SERVER_IP}${NC}"
    echo -e "  🔐  Prisijungimas: https://${SERVER_IP}/konradvault.html"
    echo -e "  📊  Admin panel:  https://${SERVER_IP}/admin.html"
    echo ""
    echo -e "${YELLOW}  KITI VEIKSMAI:${NC}"
    echo -e "  1. Sukurkite pirmą admin vartotoją:"
    echo -e "     ${CYAN}cd ${BACKEND_DIR} && sudo -u konradvault \\"
    echo -e "     ${VENV_DIR}/bin/python scripts/create_user.py --username admin --admin${NC}"
    echo ""
    echo -e "  2. Patikrinkite sistemos būseną:"
    echo -e "     ${CYAN}sudo bash deployment/deploy.sh status${NC}"
    echo ""
    echo -e "  3. Stebėkite logus:"
    echo -e "     ${CYAN}sudo bash deployment/deploy.sh logs${NC}"
    echo ""
    echo -e "${YELLOW}  ⚠  Naršyklė rodys SSL įspėjimą (self-signed sertifikatas).${NC}"
    echo -e "     Spustelėkite 'Advanced' → 'Proceed' – tai normalu be domeno vardo.${NC}"
    echo ""
}

# ==============================================================================
#  KOMANDŲ DISPATCHER
# ==============================================================================
COMMAND="${1:-install}"

case "${COMMAND}" in
    install)
        cmd_install
        ;;
    update)
        cmd_update
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs
        ;;
    *)
        echo ""
        echo -e "${BOLD}Naudojimas:${NC}"
        echo "  sudo bash deployment/deploy.sh [komanda]"
        echo ""
        echo -e "${BOLD}Komandos:${NC}"
        echo "  install   Pirmas diegimas (default)"
        echo "  update    Atnaujinti kodą ir perkrauti"
        echo "  status    Rodyti sistemos būseną"
        echo "  logs      Rodyti live logus"
        echo ""
        exit 1
        ;;
esac
