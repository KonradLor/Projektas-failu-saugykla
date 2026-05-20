"""
Mėnesinio srauto (transfer quota) pagalbinis modulis.

Logika:
    1. Kiekvienas vartotojas turi mėnesinį baitų limitą (settings.monthly_transfer_limit_gb).
    2. transfer_used_bytes - kiek šio mėnesio srauto sunaudota.
    3. transfer_period_start - kurio mėnesio 1-osios diena (UTC).
    4. Mėnesio pasikeitimo metu skaitiklis nulinamas (rolling reset).

ŠITAS MODULIS:
    - ensure_current_period(user, db)   - patikrina ir nulina, jei naujas mėnuo
    - check_quota(user, bytes, db)      - HTTPException 429, jei viršija
    - add_usage(user, bytes, db)        - prideda baitus prie skaitiklio
    - get_quota_info(user, db)          - grąžina dict su naudojimu/limitu

NAUDOJAMAS:
    - api/files.py    upload_file()     prieš ir po įkėlimo
    - api/files.py    download_file()   prieš atsisiuntimą (mes žinome dydį)
    - api/share.py    download_public_share()  charge'inam FAILO SAVININKĄ
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================

def _current_period_start() -> date:
    """Grąžina einamojo mėnesio 1-ąją (UTC)."""
    today = datetime.now(timezone.utc).date()
    return today.replace(day=1)


def _next_period_start(current: date) -> date:
    """Grąžina kito mėnesio 1-ąją (po current periodo)."""
    if current.month == 12:
        return date(current.year + 1, 1, 1)
    return date(current.year, current.month + 1, 1)


def _format_bytes(n: int) -> str:
    """Skaičius -> 'X.X GB' arba 'X.X MB' žmoniško skaitymo formate."""
    gb = 1024 ** 3
    mb = 1024 ** 2
    if n >= gb:
        return f"{n / gb:.2f} GB"
    if n >= mb:
        return f"{n / mb:.1f} MB"
    return f"{n / 1024:.0f} KB"


# ============================================
# VIEŠOSIOS FUNKCIJOS
# ============================================

def ensure_current_period(user: User, db: Session) -> None:
    """
    Patikrina, ar user.transfer_period_start yra einamojo mėnesio 1-oji.
    Jei ne (mėnuo pasikeitė) - nulina skaitiklį ir atnaujina period_start.

    Užtikrina, kad visi tolesni quota patikrinimai veiktų su tiksliais
    duomenimis šiame mėnesyje.

    PASTABA: NEKVIEČIA db.commit() - kviečiančioji funkcija atsakinga už
    transakciją. Tai leidžia kombinuoti su kitais DB pakeitimais.
    """
    current = _current_period_start()
    if user.transfer_period_start != current:
        logger.info(
            f"Quota reset for user_id={user.id}: "
            f"{user.transfer_period_start} -> {current} "
            f"(was used: {_format_bytes(user.transfer_used_bytes)})"
        )
        user.transfer_period_start = current
        user.transfer_used_bytes = 0


def check_quota(user: User, additional_bytes: int, db: Session) -> None:
    """
    Patikrina, ar vartotojas gali perduoti dar `additional_bytes` baitų.
    Jei viršija mėnesinį limitą - meta 429 HTTPException.

    Naudoja: prieš upload (su file size), prieš download (su file size),
            prieš share download (charge'inant savininką).
    """
    ensure_current_period(user, db)

    limit = settings.monthly_transfer_limit_bytes
    used = user.transfer_used_bytes
    after = used + max(0, additional_bytes)

    if after > limit:
        remaining = max(0, limit - used)
        next_reset = _next_period_start(user.transfer_period_start)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Viršijote mėnesinį {settings.monthly_transfer_limit_gb} GB srauto limitą. "
                f"Sunaudota: {_format_bytes(used)}, "
                f"liko: {_format_bytes(remaining)}. "
                f"Skaitiklis bus nulinamas: {next_reset.isoformat()}."
            ),
        )


def add_usage(user: User, bytes_count: int, db: Session) -> None:
    """
    Įrašo `bytes_count` baitus į vartotojo srauto skaitiklį.

    Naudoja: PO sėkmingo upload (su tikrais baitų skaičiumi),
            PO download (su file size).

    PASTABA: NEKVIEČIA db.commit() - kviečiančioji funkcija atsakinga
    už transakciją (kad būtų galima sujungti su kitais pakeitimais,
    pvz., storage_used_bytes atnaujinimu).
    """
    if bytes_count <= 0:
        return
    ensure_current_period(user, db)
    user.transfer_used_bytes = user.transfer_used_bytes + bytes_count


def get_quota_info(user: User, db: Session) -> dict[str, Any]:
    """
    Grąžina vartotojo srauto info dict'e:
        {
            "used_bytes": int,
            "limit_bytes": int,
            "remaining_bytes": int,
            "used_gb": float,
            "limit_gb": int,
            "remaining_gb": float,
            "period_start": "YYYY-MM-DD",
            "next_reset": "YYYY-MM-DD",
            "percent_used": float    # 0.0-100.0
        }

    Naudoja: GET /api/auth/me-quota endpoint'as (dashboard/profilis).
    """
    ensure_current_period(user, db)
    used = user.transfer_used_bytes
    limit = settings.monthly_transfer_limit_bytes
    remaining = max(0, limit - used)
    next_reset = _next_period_start(user.transfer_period_start)
    percent = (used / limit * 100) if limit > 0 else 0.0

    return {
        "used_bytes": used,
        "limit_bytes": limit,
        "remaining_bytes": remaining,
        "used_gb": round(used / (1024 ** 3), 3),
        "limit_gb": settings.monthly_transfer_limit_gb,
        "remaining_gb": round(remaining / (1024 ** 3), 3),
        "period_start": user.transfer_period_start.isoformat(),
        "next_reset": next_reset.isoformat(),
        "percent_used": round(percent, 1),
    }
