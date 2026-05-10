<div align="center">

# 🔐 KonradVault

**Privati, end-to-end šifruota failų saugykla**

Universitetinis baigiamasis projektas — savarankiškai sukonfigūruota,
pilno stack'o, demonstracinė aplikacija, paleista ant Oracle Cloud Always Free.

[![License](https://img.shields.io/badge/license-Proprietary%20(view%20only)-red.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57.svg)](https://www.sqlite.org/)
[![Status](https://img.shields.io/badge/status-MVP%20baigtas-success.svg)]()

**🌐 Kalba:** [English](README.md) · **Lietuvių**

</div>

---

## 📖 Apie projektą

**KonradVault** — tai šifruota debesijos saugykla, kurioje vartotojai gali
saugoti, peržiūrėti ir dalintis savo failais. Visi failai šifruojami **vartotojui
unikaliu raktu** prieš įrašant į diską — net jei DB ir disko turinys būtų
pavogtas, be `MASTER_KEY` failai lieka neperskaitomi.

Projekto tikslas — pademonstruoti pilno stack'o sprendimą su realiais
saugumo principais, deployinimą Linux serveryje ir modernų UX.

---

## ✨ Pagrindinės funkcijos

- 🔒 **End-to-end šifravimas** (Fernet / AES-128-CBC + HMAC-SHA256)
- 🗝 **Per-user key hierarchy** — `MASTER_KEY → USER_KEY (DB) → FILES (disk)`
- 🔐 **2FA su Google Authenticator** (TOTP / RFC 6238)
- 👥 **Vartotojų valdymas** — admin panelė, registracija (be slaptažodžio,
  tik TOTP), 2FA atstatymas
- 📁 **Aplankų hierarchija** su drag & drop, spalvų rinkikliu
- 🗑 **Šiukšlinė** (soft delete + restore)
- 🔗 **Dalinimosi nuorodos** su atsisiuntimų limitais (atominis counter)
- 🖼 **Paveikslėlių thumbnail'ai** — automatiškai generuojami, šifruoti
- 🔍 **Failų ir aplankų paieška** per visą saugyklą
- 📤 **Streaming upload/download** — 500MB failai be RAM perkrovos
- 🎨 **Modernus UI** — vanilla JS + Tailwind CSS, glassmorphism, animacijos

---

## 🛠 Technologijų stack'as

| Sluoksnis | Įrankis |
|---|---|
| Backend framework | FastAPI 0.115 + Uvicorn |
| ORM | SQLAlchemy 2.0 + Alembic |
| Duomenų bazė | SQLite (WAL mode) |
| Autentifikacija | Argon2id + Pydantic v2 + pyotp |
| Šifravimas | cryptography (Fernet, streaming chunks) |
| Frontend | Vanilla JS + Tailwind CSS (CDN) |
| Reverse proxy | Nginx (TLS 1.2/1.3, rate limiting) |
| Procesų valdymas | systemd (su saugumo izoliacija) |
| Hosting | Oracle Cloud Always Free (ARM Ubuntu 22.04) |

---

## 🏗 Architektūra

```
                    ┌──────────────────┐
                    │   Klientai       │
                    │ (naršyklė + JS)  │
                    └────────┬─────────┘
                             │ HTTPS (TLS 1.2/1.3)
                    ┌────────▼─────────┐
                    │   Nginx :443     │
                    │ (rate limit, SSL)│
                    └────────┬─────────┘
                             │ HTTP loopback
                    ┌────────▼─────────┐
                    │  FastAPI :8000   │
                    │   (Uvicorn)      │
                    └──┬──────────┬────┘
                       │          │
              ┌────────▼──┐  ┌────▼────────────┐
              │  SQLite   │  │  Šifruoti       │
              │  (WAL)    │  │  failai diske   │
              │  metadata │  │  /var/.../*.enc │
              └───────────┘  └─────────────────┘
```

**Šifravimo grandinė:**
```
MASTER_KEY (.env)  ──šifruoja──►  USER_KEY (DB BLOB)  ──šifruoja──►  FILES (disk)
```

Praradus `MASTER_KEY` — visi failai prarasti negrįžtamai (jokio backdoor).

---

## 📂 Projekto struktūra

```
main/
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── api/               # Endpoint'ai (auth, files, folders, share, admin, …)
│   │   ├── core/              # Šifravimas, security, TOTP, dependencies
│   │   ├── models/            # SQLAlchemy ORM (User, File, Folder, ShareLink, Session)
│   │   ├── schemas/           # Pydantic schemos (request/response)
│   │   ├── utils/             # Helper'iai (file I/O, thumbnails, common)
│   │   ├── config.py          # Pydantic Settings
│   │   ├── database.py        # SQLAlchemy engine
│   │   └── main.py            # FastAPI entry point
│   ├── migrations/            # Alembic migracijos
│   ├── scripts/               # CLI įrankiai (create_user, reset_2fa)
│   └── requirements.txt
│
├── frontend/                  # Vanilla HTML/CSS/JS (be build step'o)
│   ├── konradvault.html       # Landing + login + registracija
│   ├── dashboard.html         # Failų valdytojas (pagrindinis UI)
│   ├── share.html             # Viešas dalinimosi puslapis
│   └── admin.html             # Admin panelė
│
├── deployment/
│   ├── nginx.conf             # Nginx reverse proxy + SSL + rate limiting
│   ├── konradvault.service    # systemd unit (saugumo izoliacija)
│   └── deploy.sh              # Vienos komandos diegimas/atnaujinimas
│
├── docs/                      # Techninė dokumentacija (laukia)
├── LICENSE                    # Proprietary – mokymosi tikslams
├── README.md                  # English (default)
└── README.lt.md               # Lietuviškas vertimas
```

---

## 🚀 Greitas paleidimas

### Lokali aplinka (kūrimui)

```bash
# 1. Klonas
git clone https://github.com/KonradLor/Projektas-failu-saugykla.git
cd Projektas-failu-saugykla/backend

# 2. Python 3.10+ aplinka
python3 -m venv venv
source venv/bin/activate            # Linux/Mac
# venv\Scripts\activate              # Windows

# 3. Priklausomybės
pip install -r requirements.txt

# 4. .env failas
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
python -c "from cryptography.fernet import Fernet; print('SECRET_KEY=' + Fernet.generate_key().decode())"
# → įklijuok abu į .env

# 5. DB inicializacija
alembic upgrade head

# 6. Pirmas vartotojas (admin)
python scripts/create_user.py --username konradas --admin

# 7. Paleidimas
uvicorn app.main:app --reload
# → http://localhost:8000/konradvault.html
```

### Production deployment (Linux serveris)

```bash
# Vienos komandos diegimas (kuria sistemos vartotoją, venv, Nginx, systemd)
sudo bash deployment/deploy.sh
```

Detalios instrukcijos — [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (laukia).

---

## 🔒 Saugumo principai

| Sritis | Sprendimas |
|---|---|
| Slaptažodžių saugojimas | Argon2id (memory_cost=64MB, time_cost=3) |
| 2FA | TOTP RFC 6238, valid_window=1, max 5 brute-force bandymai |
| Sesijos | HTTP-only + Secure + SameSite=Strict cookie, 24h TTL |
| Failų šifravimas | Fernet (AES-128-CBC + HMAC), per-user key |
| Streaming | 64KB chunks (jokio RAM perkrovimo) |
| Rate limiting | Nginx limit_req — 5r/s auth, 30r/s API |
| Header'iai | HSTS, X-Frame-Options DENY, CSP, nosniff |
| systemd izoliacija | ProtectSystem=strict, NoNewPrivileges, syscall filter |
| User enumeration | Vienodas atsakymo laikas su tikru dummy hash |
| Path traversal | UUID failai diske, validated filename schema |

---

## 📊 Limitai ir kvotos

| Parametras | Reikšmė |
|---|---|
| Maksimalus failo dydis | 500 MB |
| Maksimali vieta vartotojui | 2 GB |
| Maksimalus vartotojų skaičius | 10 (rezervuota Oracle Free riba) |
| Sesijos galiojimas | 24 h |
| TOTP setup token | 5 min (login) / 10 min (register) |
| Share download limit | 1–1000 |

---

## 🖥 Demo (gyvas projektas)

> Demo URL pateikiamas pagal poreikį projekto pristatymo metu.
> Dėl Oracle Free 10 vartotojų limito — naudojama tik kvietimu.

---

## 📜 Licencija

Šis projektas yra **publikai matomas tik mokymosi tikslams**.

✅ Galite skaityti, studijuoti ir nuorodos į projektą.
❌ Negalima naudoti komerciškai, deployinti į produkciją ar
   redistribuoti modifikuotų versijų.

Pilnos sąlygos — [LICENSE](LICENSE) faile.

Komerciniam naudojimui — susisiekite per repo issue tracker'į.

---

## 👤 Autorius

**Konrad Lorenz** — universitetinio baigiamojo darbo autorius

Projektas sukurtas savarankiškai per ~50 valandų, dokumentuojamas pagal
griežtą sesijų protokolą (`03_PROGRESO_PROTOKOLAS.txt`).

---

## 🙏 Dėkoju

- [FastAPI](https://fastapi.tiangolo.com/) — moderniausias Python web framework'as
- [SQLAlchemy](https://www.sqlalchemy.org/) — Python ORM standartas
- [cryptography](https://cryptography.io/) — Python kriptografijos biblioteka
- [Tailwind CSS](https://tailwindcss.com/) — utility-first CSS framework'as
- [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) — nemokamas ARM serveris

---

<div align="center">

⭐ Jei projektas patinka — duokite žvaigždutę ant GitHub!

📖 **English version → [README.md](README.md)**

</div>
