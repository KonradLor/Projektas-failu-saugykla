"""
Per-vartotojo „effective" limitų pagalbinis modulis.

Logika:
    - EILINIS vartotojas: galioja globalūs settings limitai
        (storage, failo dydis, mėnesinis srautas).
    - ADMINISTRATORIUS (is_admin=True): storage ir failo dydis NERIBOJAMI,
        mėnesinis srautas apribotas atsargai (admin_monthly_transfer_limit_gb,
        pagal nutylėjimą 1000 GB).

NAUDOJAMAS:
    - api/files.py            upload (storage + failo dydis + srautas)
    - utils/transfer_quota.py check_quota / get_quota_info (srautas)

Visi callerai limitą gauna PER VARTOTOJĄ, todėl admino išimtis veikia
automatiškai visur, kur šie helper'iai naudojami.
"""

from __future__ import annotations

from app.config import settings
from app.models.user import User

# Praktiškai „neribota" reikšmė (1 EiB). Naudojama admino storage / failo dydžiui.
# Pakankamai didelė, kad realiai niekada nebūtų pasiekta, bet ne begalybė
# (kad aritmetinė min()/atimtis veiktų be problemų).
UNLIMITED_BYTES: int = 1024 ** 6


def storage_limit_bytes(user: User) -> int:
    """Maksimali disko vieta vartotojui baitais (adminui – neribota)."""
    if user.is_admin:
        return UNLIMITED_BYTES
    return settings.max_storage_per_user_bytes


def max_file_size_bytes(user: User) -> int:
    """Maksimalus vieno failo dydis baitais (adminui – neribota)."""
    if user.is_admin:
        return UNLIMITED_BYTES
    return settings.max_file_size_bytes


def transfer_limit_bytes(user: User) -> int:
    """Mėnesinio srauto limitas baitais (adminui – admin_monthly_transfer_limit)."""
    if user.is_admin:
        return settings.admin_monthly_transfer_limit_bytes
    return settings.monthly_transfer_limit_bytes


def transfer_limit_gb(user: User) -> int:
    """Mėnesinio srauto limitas GB (žinutėms / UI)."""
    if user.is_admin:
        return settings.admin_monthly_transfer_limit_gb
    return settings.monthly_transfer_limit_gb
