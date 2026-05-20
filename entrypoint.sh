#!/bin/sh
# ============================================================================
# KonradVault konteinerio paleidimo skriptas
# ============================================================================
# Žingsniai:
#   1. Pritaikom alembic migracijas (sukuria/atnaujina DB schemą)
#   2. Paleidžiam uvicorn ASGI serverį
# ============================================================================

set -e

echo "============================================================"
echo "KonradVault konteinerio paleidimas"
echo "============================================================"

echo "[1/2] Pritaikom Alembic migracijas..."
alembic upgrade head
echo "    Migracijos OK"

echo "[2/2] Paleidžiame uvicorn ASGI serverį..."
echo "    Adresas: 0.0.0.0:8000"
echo "    Workers: 1 (Oracle Free Tier - taupom resursus)"
echo "============================================================"

# exec - kad uvicorn taptų PID 1 ir gautų signalus tiesiogiai
# (svarbu graceful shutdown'ui per docker stop)
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips '*'
