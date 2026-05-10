"""
API endpoint'ų paketas.

Kiekvienas modulis šiame pakete yra atskiras FastAPI router'is.
Visi jie registruojami main.py faile per app.include_router().

REALIZUOTI:
    auth.py    → /api/auth/*    (login, 2FA, logout, me)

LAUKIA REALIZACIJOS:
    folders.py → /api/folders/*
    files.py   → /api/files/*
    share.py   → /api/share/*
    search.py  → /api/search
    trash.py   → /api/trash/*
    admin.py   → /api/admin/*
"""
