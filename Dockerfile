# ============================================================================
# KonradVault - Docker image
# ============================================================================
# Bazė:    python:3.11-slim (palaiko arm64/aarch64)
# Tikslas: vienas konteineris, kuriame veikia FastAPI backend + serveruoja
#          frontend statinius failus (nereikia atskiro nginx konteinerio,
#          nes Caddy reverse proxy mums duoda TLS sluoksnį).
# ============================================================================

FROM python:3.11-slim

# Sistemos paketai - failų apdorojimui:
#   poppler-utils         - PDF -> image (PDF preview pirmajam puslapiui)
#   libimage-exiftool-perl - failų metaduomenys
#   imagemagick           - paveikslėlių thumbnail'ai (kartu su Pillow)
# Statinė package list -> nustatomas paketų sąrašas, jei juos atnaujins -
# rankiniu būdu reikės apt update + rebuild.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        libimage-exiftool-perl \
        imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Non-root vartotojas - UID 1001 atitinka host'o "ubuntu" vartotoją.
# Tai svarbu volume mount'ams (/var/server-data/konradvault -> /data),
# kad permissions tarp host'o ir konteinerio sutaptų.
RUN useradd -m -u 1001 -s /bin/bash konradvault

WORKDIR /app

# Atskiras layer dependency diegimui - leidžia Docker cache'inti
# (kodas keičiasi dažniau nei requirements.txt).
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend kodas
COPY backend/app          ./app
COPY backend/migrations   ./migrations
COPY backend/scripts      ./scripts
COPY backend/alembic.ini  .

# Frontend statiniai failai (FastAPI juos serveruos per StaticFiles mount)
COPY frontend ./frontend

# Entrypoint - paleidžia alembic + uvicorn
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Sukuriam /data katalogą - volume bus mount'intas čia.
# (Permissions svarbus, kad konradvault vartotojas galėtų rašyti)
RUN mkdir -p /data /data/encrypted /data/logs \
    && chown -R konradvault:konradvault /data /app

USER konradvault

# Uvicorn portas (Caddy proxy'ns iš išorės)
EXPOSE 8000

# Sveikatos patikrinimas - Docker stebės health endpoint'ą
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health').read()" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
