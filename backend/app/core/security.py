"""
Saugumo pagalbinių funkcijų modulis.

Apima:
    - Slaptažodžių maišymą ir tikrinimą (Argon2id)
    - Session token'ų generavimą
    - Laikinų token'ų (temp_token) generavimą 2FA žingsniui
    - Laiko saugų eilučių lyginimą (timing-safe compare)

ARGON2 PASIRINKIMO PRIEŽASTIS:
    - Memory-hard algoritmas → GPU brute-force neefektyvus
    - Moderniausia alternatyva bcrypt ir scrypt
    - OWASP rekomenduojamas nuo 2023
    - argon2-cffi biblioteka: Pythono wraperis C implementacijai (greita)
"""

# ============================================
# IMPORTAI
# ============================================
import hashlib
import hmac
import logging
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings


# ============================================
# LOGGER
# ============================================

logger = logging.getLogger(__name__)


# ============================================
# ARGON2 KONFIGŪRACIJA
# ============================================

# PasswordHasher objektas – sukuriamas vieną kartą (ne kiekvienam hashavimui)
# Parametrai iš konfigūracijos – galima reguliuoti pagal serverio galią
_password_hasher = PasswordHasher(
    # Kiek iteracijų atliekama (laiko kaštai)
    time_cost=settings.argon2_time_cost,

    # Kiek atminties naudojama (KB) – didesnė reikšmė → sunkiau GPU
    memory_cost=settings.argon2_memory_cost,

    # Kiek lygiagrečių gijų naudojama
    parallelism=settings.argon2_parallelism,

    # Hash ilgis baitais
    hash_len=32,

    # Salt ilgis baitais (argon2-cffi generuoja automatiškai)
    salt_len=16,
)


# ============================================
# SLAPTAŽODŽIŲ FUNKCIJOS
# ============================================

def hash_password(plain_password: str) -> str:
    """
    gauna: plain_password (str) – plaintext slaptažodis iš formos
    daro: sukuria Argon2id hash su atsitiktiniu salt'u
    grąžina: (str) – Argon2 hash eilutė (saugoma DB)

    PASTABA: Kiekvienas kvietimas grąžina KITĄ hash (skirtingas salt),
    net jei slaptažodis tas pats – tai normalu ir pageidautina.
    """
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """
    gauna: plain_password (str) – vartotojo įvestas slaptažodis
           password_hash (str)  – Argon2 hash iš DB
    daro: lygina plaintext su hash, naudodamas constant-time algoritmą
          (apsauga nuo timing atakų)
    grąžina: (bool) – True jei slaptažodis teisingas, False jei ne
    """
    try:
        # verify() meta exception jei nesutampa – ne False!
        _password_hasher.verify(password_hash, plain_password)
        return True

    except VerifyMismatchError:
        # Neteisingas slaptažodis – normalus atvejis
        return False

    except (VerificationError, InvalidHashError) as exc:
        # Hash sugadintas arba formato klaida – rimta problema
        logger.error(f"Slaptažodžio tikrinimo klaida (hash problema): {exc}")
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """
    gauna: password_hash (str) – Argon2 hash iš DB
    daro: tikrina ar hash buvo sukurtas su senesniais parametrais.
          Jei taip – reikia rehash'uoti po sėkmingo prisijungimo.
          (argon2-cffi tai daro automatiškai, kai keičiasi parametrai)
    grąžina: (bool) – True jei reikia rehash'uoti
    """
    return _password_hasher.check_needs_rehash(password_hash)


# ============================================
# SESSION TOKEN FUNKCIJOS
# ============================================

def generate_session_token() -> str:
    """
    gauna: nieko
    daro: generuoja kriptografiškai saugų session token'ą
          naudojant OS atsitiktinių skaičių generatorių (secrets modulis)
    grąžina: (str) – URL-safe base64 token'as (~43 simboliai)

    SAUGUMAS: secrets.token_urlsafe naudoja os.urandom() – kriptografiškai saugus
    """
    # 32 baitai → ~43 URL-safe base64 simboliai
    return secrets.token_urlsafe(32)


def generate_temp_token() -> str:
    """
    gauna: nieko
    daro: generuoja laikinąjį token'ą 2FA žingsniui
          (tarpinis tarp password patikrinimo ir TOTP verifikacijos)
    grąžina: (str) – URL-safe base64 token'as (~27 simboliai, trumpesnis nei session)

    GALIOJIMAS: Tik settings.temp_token_expire_minutes minučių
    """
    # 20 baitų – pakanka saugumui, bet trumpesnis nei session token
    return secrets.token_urlsafe(20)


# ============================================
# LAIKO SAUGUS LYGINIMAS
# ============================================

def constant_time_compare(value_a: str, value_b: str) -> bool:
    """
    gauna: value_a (str) – pirmoji eilutė
           value_b (str) – antroji eilutė
    daro: lygina dvi eilutes CONSTANT TIME – net jei nesutampa,
          užtrunka tiek pat laiko kaip ir sutampant.
          Apsauga nuo timing atakų (timing oracle).
    grąžina: (bool) – True jei eilutės identiškos

    KADA NAUDOTI:
        - Lyginant session token'us
        - Lyginant bet kokius slaptus kodus
        - NEreikia slaptažodžiams (Argon2 jau daro tai)
    """
    return hmac.compare_digest(
        value_a.encode("utf-8"),
        value_b.encode("utf-8"),
    )


# ============================================
# HASH FUNKCIJOS FAILAMS
# ============================================

def compute_file_hash(data: bytes) -> str:
    """
    gauna: data (bytes) – failo turinys (arba chunk'as)
    daro: apskaičiuoja SHA-256 hash failo integralumui patikrinti
    grąžina: (str) – SHA-256 hash hex formatu (64 simboliai)

    NAUDOJIMAS upload'o metu:
        hash_value = compute_file_hash(file_bytes)
        # Saugoma DB → tikrinama po download ir decrypt
    """
    return hashlib.sha256(data).hexdigest()


def compute_streaming_hash(chunks: list[bytes]) -> str:
    """
    gauna: chunks (list[bytes]) – failo duomenų dalys (iš streaming upload)
    daro: apskaičiuoja SHA-256 hash per kelis chunks (nekrauna viso failo į RAM)
    grąžina: (str) – SHA-256 hash hex formatu

    NAUDOJIMAS su dideliais failais (streaming):
        hasher = hashlib.sha256()
        async for chunk in upload_stream:
            hasher.update(chunk)
        hash_value = hasher.hexdigest()
    """
    hasher = hashlib.sha256()
    for chunk in chunks:
        hasher.update(chunk)
    return hasher.hexdigest()
