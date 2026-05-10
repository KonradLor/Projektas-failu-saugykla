<div align="center">

# 🔐 KonradVault

**Private, end-to-end encrypted file vault**

University final project — a fully self-configured, full-stack
demonstration application deployed on Oracle Cloud Always Free.

[![License](https://img.shields.io/badge/license-Proprietary%20(view%20only)-red.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57.svg)](https://www.sqlite.org/)
[![Status](https://img.shields.io/badge/status-MVP%20complete-success.svg)]()

**🌐 Language:** **English** · [Lietuvių](README.lt.md)

</div>

---

## 📖 About the project

**KonradVault** is an encrypted cloud vault where users can store, preview
and share their files. Every file is encrypted with a **per-user unique key**
before being written to disk — even if the database and disk contents were
stolen, the files remain unreadable without the `MASTER_KEY`.

The project's goal is to demonstrate a full-stack solution with real-world
security principles, Linux server deployment and a modern UX.

---

## ✨ Key features

- 🔒 **End-to-end encryption** (Fernet / AES-128-CBC + HMAC-SHA256)
- 🗝 **Per-user key hierarchy** — `MASTER_KEY → USER_KEY (DB) → FILES (disk)`
- 🔐 **2FA via Google Authenticator** (TOTP / RFC 6238)
- 👥 **User management** — admin panel, password-less registration
  (TOTP only), 2FA reset
- 📁 **Folder hierarchy** with drag & drop, color picker
- 🗑 **Trash bin** (soft delete + restore)
- 🔗 **Share links** with download limits (atomic counter)
- 🖼 **Image thumbnails** — auto-generated, encrypted
- 🔍 **File and folder search** across the entire vault
- 📤 **Streaming upload/download** — 500MB files without RAM overload
- 🎨 **Modern UI** — vanilla JS + Tailwind CSS, glassmorphism, animations

---

## 🛠 Tech stack

| Layer | Tool |
|---|---|
| Backend framework | FastAPI 0.115 + Uvicorn |
| ORM | SQLAlchemy 2.0 + Alembic |
| Database | SQLite (WAL mode) |
| Authentication | Argon2id + Pydantic v2 + pyotp |
| Encryption | cryptography (Fernet, streaming chunks) |
| Frontend | Vanilla JS + Tailwind CSS (CDN) |
| Reverse proxy | Nginx (TLS 1.2/1.3, rate limiting) |
| Process management | systemd (with security hardening) |
| Hosting | Oracle Cloud Always Free (ARM Ubuntu 22.04) |

---

## 🏗 Architecture

```
                    ┌──────────────────┐
                    │     Clients      │
                    │ (browser + JS)   │
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
              │  SQLite   │  │  Encrypted      │
              │  (WAL)    │  │  files on disk  │
              │  metadata │  │  /var/.../*.enc │
              └───────────┘  └─────────────────┘
```

**Encryption chain:**
```
MASTER_KEY (.env)  ──encrypts──►  USER_KEY (DB BLOB)  ──encrypts──►  FILES (disk)
```

If `MASTER_KEY` is lost — all files are gone forever (no backdoor).

---

## 📂 Project structure

```
main/
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── api/               # Endpoints (auth, files, folders, share, admin, …)
│   │   ├── core/              # Encryption, security, TOTP, dependencies
│   │   ├── models/            # SQLAlchemy ORM (User, File, Folder, ShareLink, Session)
│   │   ├── schemas/           # Pydantic schemas (request/response)
│   │   ├── utils/             # Helpers (file I/O, thumbnails, common)
│   │   ├── config.py          # Pydantic Settings
│   │   ├── database.py        # SQLAlchemy engine
│   │   └── main.py            # FastAPI entry point
│   ├── migrations/            # Alembic migrations
│   ├── scripts/               # CLI tools (create_user, reset_2fa)
│   └── requirements.txt
│
├── frontend/                  # Vanilla HTML/CSS/JS (no build step)
│   ├── konradvault.html       # Landing + login + registration
│   ├── dashboard.html         # File manager (main UI)
│   ├── share.html             # Public share page
│   └── admin.html             # Admin panel
│
├── deployment/
│   ├── nginx.conf             # Nginx reverse proxy + SSL + rate limiting
│   ├── konradvault.service    # systemd unit (security hardening)
│   └── deploy.sh              # One-command install / update
│
├── docs/                      # Technical documentation (pending)
├── LICENSE                    # Proprietary – educational use only
├── README.md                  # English (default)
└── README.lt.md               # Lithuanian translation
```

---

## 🚀 Quick start

### Local environment (development)

```bash
# 1. Clone
git clone https://github.com/KonradLor/Projektas-failu-saugykla.git
cd Projektas-failu-saugykla/backend

# 2. Python 3.10+ environment
python3 -m venv venv
source venv/bin/activate            # Linux/Mac
# venv\Scripts\activate              # Windows

# 3. Dependencies
pip install -r requirements.txt

# 4. .env file
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
python -c "from cryptography.fernet import Fernet; print('SECRET_KEY=' + Fernet.generate_key().decode())"
# → paste both into .env

# 5. DB initialization
alembic upgrade head

# 6. First user (admin)
python scripts/create_user.py --username konradas --admin

# 7. Run
uvicorn app.main:app --reload
# → http://localhost:8000/konradvault.html
```

### Production deployment (Linux server)

```bash
# Single-command install (creates system user, venv, Nginx, systemd)
sudo bash deployment/deploy.sh
```

Detailed instructions — [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (pending).

---

## 🔒 Security principles

| Area | Solution |
|---|---|
| Password storage | Argon2id (memory_cost=64MB, time_cost=3) |
| 2FA | TOTP RFC 6238, valid_window=1, max 5 brute-force attempts |
| Sessions | HTTP-only + Secure + SameSite=Strict cookie, 24h TTL |
| File encryption | Fernet (AES-128-CBC + HMAC), per-user key |
| Streaming | 64KB chunks (no RAM overload) |
| Rate limiting | Nginx limit_req — 5r/s auth, 30r/s API |
| HTTP headers | HSTS, X-Frame-Options DENY, CSP, nosniff |
| systemd hardening | ProtectSystem=strict, NoNewPrivileges, syscall filter |
| User enumeration | Constant-time response with real dummy hash |
| Path traversal | UUID files on disk, validated filename schema |

---

## 📊 Limits and quotas

| Parameter | Value |
|---|---|
| Maximum file size | 500 MB |
| Maximum storage per user | 2 GB |
| Maximum users | 10 (Oracle Free reserve limit) |
| Session lifetime | 24 h |
| TOTP setup token | 5 min (login) / 10 min (register) |
| Share download limit | 1–1000 |

---

## 🖥 Demo (live project)

> Demo URL is provided on request during the project's defense.
> Due to the Oracle Free 10-user cap — invitation-only.

---

## 📜 License

This project is **publicly visible for educational purposes only**.

✅ You can read, study and reference the project.
❌ You may not use it commercially, deploy it to production or
   redistribute modified versions.

Full terms — see the [LICENSE](LICENSE) file.

For commercial use — contact via the repo issue tracker.

---

## 👤 Author

**Konrad Lorenz** — university final project author

The project was built independently in ~50 hours, fully documented
according to a strict session protocol (`03_PROGRESO_PROTOKOLAS.txt`).

---

## 🙏 Acknowledgments

- [FastAPI](https://fastapi.tiangolo.com/) — modern Python web framework
- [SQLAlchemy](https://www.sqlalchemy.org/) — Python ORM standard
- [cryptography](https://cryptography.io/) — Python cryptography library
- [Tailwind CSS](https://tailwindcss.com/) — utility-first CSS framework
- [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) — free ARM server

---

<div align="center">

⭐ If you like the project — give it a star on GitHub!

📖 **Lietuvišką versiją galite rasti čia → [README.lt.md](README.lt.md)**

</div>
