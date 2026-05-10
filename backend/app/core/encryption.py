"""
Failų šifravimo ir dešifravimo modulis.

Realizuoja "per-user encryption keys" strategiją:
    MASTER KEY (.env) → šifruoja → USER KEY (DB) → šifruoja → FAILAI (diskas)

ALGORITMAS: Fernet (AES-128-CBC + HMAC-SHA256)
    - Fernet yra aukšto lygio wrapper ant AES-128-CBC
    - Automatiškai prideda HMAC → duomenų integralumas
    - Lengvas naudoti, saugus, palaiko ARM
    - Fernet token = IV + ciphertext + HMAC (viskas viename objekte)

STREAMING (SVARBU 500MB failams!):
    - Failo NEGALIMA krauti į RAM visą – 500MB netelpa
    - Šifruojame/dešifruojame po CHUNK_SIZE baitų (64KB)
    - Kiekvienas chunk yra atskiras Fernet token'as
    - Tai reiškia: failo pradžioje saugomas chunk'ų skaičius (header)

FAILŲ SAUGOJIMO SCHEMA:
    /var/konradvault/encrypted/{uuid}
    Failo struktūra:
        [4 baitai: chunk'ų skaičius (big-endian uint32)]
        [4 baitai: chunk 1 ilgis]  [chunk 1 Fernet token baitai]
        [4 baitai: chunk 2 ilgis]  [chunk 2 Fernet token baitai]
        ...
"""

# ============================================
# IMPORTAI
# ============================================
import logging
import os
import struct
from pathlib import Path
from typing import Generator

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


# ============================================
# LOGGER
# ============================================

logger = logging.getLogger(__name__)


# ============================================
# KONSTANTA: CHUNK DYDIS
# ============================================

# Kiek plaintext baitų šifruojame vienu metu
# 64KB – balansas tarp RAM naudojimo ir I/O operacijų skaičiaus
# (512KB failas = 8 chunk'ai, 500MB failas = ~7813 chunk'ai)
CHUNK_SIZE = settings.encryption_chunk_size  # Default: 65536 baitų (64KB)


# ============================================
# USER KEY VALDYMAS
# ============================================

def generate_user_key() -> bytes:
    """
    gauna: nieko
    daro: sugeneruoja naują, atsitiktinį vartotojo šifravimo raktą
          (256-bit, Fernet suderinamas formatas)
    grąžina: (bytes) – 32 baitų atsitiktinis raktas

    NAUDOJIMAS:
        - Kuriant naują vartotoją (create_user.py)
        - Reset'inant vartotojo raktą (jei sugadintas)
    """
    # Fernet.generate_key() grąžina URL-safe base64 koduotą 32 baitų raktą
    # os.urandom(32) → Fernet.generate_key() yra ekvivalentas, bet oficialius API
    return Fernet.generate_key()


def encrypt_user_key(user_key: bytes, master_key: str) -> bytes:
    """
    gauna: user_key  (bytes) – vartotojo šifravimo raktas (plaintext)
           master_key (str)  – sistemos master key iš .env (string formatas)
    daro: šifruoja vartotojo raktą su sistemos master key.
          Tai "key wrapping" – raktas apsaugomas antru raktu.
    grąžina: (bytes) – užšifruotas vartotojo raktas (saugomas DB)

    SAUGUMAS:
        - Net jei kas pavogtų DB – negalės dešifruoti failų
          be master_key (kuris yra tik .env faile)
    """
    # Master key gali būti string formatu (iš .env) – konvertuojame į bytes
    master_key_bytes = master_key.encode("utf-8") if isinstance(master_key, str) else master_key

    # Sukuriame Fernet objektą su master key
    fernet = Fernet(master_key_bytes)

    # Šifruojame vartotojo raktą
    return fernet.encrypt(user_key)


def decrypt_user_key(encrypted_user_key: bytes, master_key: str) -> bytes:
    """
    gauna: encrypted_user_key (bytes) – užšifruotas vartotojo raktas iš DB
           master_key          (str)  – sistemos master key iš .env
    daro: dešifruoja vartotojo raktą naudojant master key.
          Kviečiama kiekvieno upload/download metu.
    grąžina: (bytes) – vartotojo šifravimo raktas (plaintext)
    iškelia: ValueError – jei master_key neteisingas arba duomenys sugadinti
    """
    master_key_bytes = master_key.encode("utf-8") if isinstance(master_key, str) else master_key

    fernet = Fernet(master_key_bytes)

    try:
        return fernet.decrypt(encrypted_user_key)
    except InvalidToken as exc:
        # Tai kritinė klaida – gali reikšti:
        #   1. Pakeistas master_key (visi failai prarasti!)
        #   2. DB duomenys sugadinti
        logger.critical(
            "Nepavyko dešifruoti vartotojo rakto! "
            "Patikrinkite ar MASTER_KEY .env faile nesikeitė."
        )
        raise ValueError(
            "Vartotojo šifravimo rakto dešifravimas nepavyko. "
            "Gali būti pakeistas MASTER_KEY arba sugadinti DB duomenys."
        ) from exc


# ============================================
# FAILŲ ŠIFRAVIMAS (STREAMING)
# ============================================

def encrypt_file_to_path(
    source_path: Path,
    dest_path: Path,
    user_key: bytes,
) -> tuple[int, str]:
    """
    gauna: source_path (Path) – kelias iki plaintext failo (upload'inamo)
           dest_path   (Path) – kur saugoti užšifruotą failą
           user_key    (bytes) – vartotojo šifravimo raktas (plaintext)
    daro: skaito plaintext failą po CHUNK_SIZE baitų,
          kiekvieną chunk'ą šifruoja atskirai su Fernet,
          rašo į dest_path su chunk'ų ilgių header'iais.
          NEKRAUNA VISO FAILO Į RAM.
    grąžina: tuple(int, str) – (šifruoto failo dydis baitais, SHA-256 hash)
    iškelia: IOError – jei nepavyksta skaityti/rašyti failus
    """
    from app.core.security import compute_file_hash

    fernet = Fernet(user_key)

    # SHA-256 hasher originaliam failui (integralumo patikrinimui)
    import hashlib
    hasher = hashlib.sha256()

    # Chunk'ų sąrašas – pirmiausia surenkame visus, tada rašome
    # (kad žinotume chunk'ų skaičių header'iui)
    # PASTABA: Chunk'ų metadata (ilgiai) laikome atmintyje, ne patys duomenys
    encrypted_chunks: list[bytes] = []

    # ----------------------------------------
    # 1. FAILO ŠIFRAVIMAS PO CHUNK'US
    # ----------------------------------------
    with open(source_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break

            # Atnaujiname hash (originalus plaintext chunk'as)
            hasher.update(chunk)

            # Šifruojame chunk'ą – kiekvienas chunk turi savo Fernet token'ą
            encrypted_chunk = fernet.encrypt(chunk)
            encrypted_chunks.append(encrypted_chunk)

    # ----------------------------------------
    # 2. RAŠYMAS Į DESTIANTION FAILĄ
    # ----------------------------------------
    encrypted_file_size = 0

    with open(dest_path, "wb") as out:
        # Header: chunk'ų skaičius (4 baitai, big-endian unsigned int)
        chunk_count = len(encrypted_chunks)
        header = struct.pack(">I", chunk_count)
        out.write(header)
        encrypted_file_size += len(header)

        # Kiekvienas chunk'as: [4 baitai ilgis][chunk baitai]
        for enc_chunk in encrypted_chunks:
            chunk_len = len(enc_chunk)
            # Chunk'o ilgis (4 baitai)
            out.write(struct.pack(">I", chunk_len))
            # Chunk'o turinys
            out.write(enc_chunk)
            encrypted_file_size += 4 + chunk_len

    file_hash = hasher.hexdigest()

    logger.debug(
        f"Failas užšifruotas: {source_path.name} → {dest_path.name} | "
        f"Chunk'ų: {chunk_count} | "
        f"Šifruoto dydis: {encrypted_file_size} B"
    )

    return encrypted_file_size, file_hash


def encrypt_bytes_to_path(
    data: bytes,
    dest_path: Path,
    user_key: bytes,
) -> tuple[int, str]:
    """
    gauna: data      (bytes) – plaintext baitai (mažiems failams atmintyje)
           dest_path (Path)  – kur saugoti užšifruotą failą
           user_key  (bytes) – vartotojo šifravimo raktas
    daro: šifruoja baitų masyvą po chunk'us ir saugo į failą.
          Naudojama kai failas jau atmintyje (pvz. upload per FastAPI UploadFile).
    grąžina: tuple(int, str) – (šifruoto failo dydis baitais, SHA-256 hash)
    """
    import hashlib

    fernet = Fernet(user_key)
    hasher = hashlib.sha256()
    encrypted_chunks: list[bytes] = []

    # Padalijame baitų masyvą į chunk'us
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + CHUNK_SIZE]
        hasher.update(chunk)
        encrypted_chunks.append(fernet.encrypt(chunk))
        offset += CHUNK_SIZE

    encrypted_file_size = 0

    with open(dest_path, "wb") as out:
        # Header
        out.write(struct.pack(">I", len(encrypted_chunks)))
        encrypted_file_size += 4

        for enc_chunk in encrypted_chunks:
            out.write(struct.pack(">I", len(enc_chunk)))
            out.write(enc_chunk)
            encrypted_file_size += 4 + len(enc_chunk)

    return encrypted_file_size, hasher.hexdigest()


# ============================================
# FAILŲ DEŠIFRAVIMAS (STREAMING GENERATOR)
# ============================================

def decrypt_file_streaming(
    source_path: Path,
    user_key: bytes,
) -> Generator[bytes, None, None]:
    """
    gauna: source_path (Path)  – kelias iki užšifruoto failo diske
           user_key    (bytes) – vartotojo šifravimo raktas
    daro: skaito ir dešifruoja failą po chunk'us,
          kiekvieną dešifruotą chunk'ą paduoda kaip yield.
          NEKRAUNA VISO FAILO Į RAM.
    grąžina: (Generator[bytes]) – plaintext chunk'ų generatorius

    NAUDOJIMAS (FastAPI StreamingResponse):
        return StreamingResponse(
            decrypt_file_streaming(file_path, user_key),
            media_type="application/octet-stream",
        )

    iškelia: ValueError – jei failas sugadintas arba raktas neteisingas
             FileNotFoundError – jei failas neegzistuoja
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Užšifruotas failas nerastas: {source_path}")

    fernet = Fernet(user_key)

    with open(source_path, "rb") as f:
        # Skaitome header'ą: chunk'ų skaičius
        header_bytes = f.read(4)
        if len(header_bytes) < 4:
            raise ValueError(f"Failas sugadintas (per trumpas header'as): {source_path}")

        chunk_count = struct.unpack(">I", header_bytes)[0]

        # Skaitome ir dešifruojame kiekvieną chunk'ą
        for chunk_index in range(chunk_count):
            # Chunk'o ilgis (4 baitai)
            len_bytes = f.read(4)
            if len(len_bytes) < 4:
                raise ValueError(
                    f"Failas sugadintas (chunk {chunk_index} ilgio klaida): {source_path}"
                )

            chunk_len = struct.unpack(">I", len_bytes)[0]

            # Chunk'o turinys
            enc_chunk = f.read(chunk_len)
            if len(enc_chunk) != chunk_len:
                raise ValueError(
                    f"Failas sugadintas (chunk {chunk_index} trumpesnis nei tikėtasi): {source_path}"
                )

            # Dešifruojame chunk'ą
            try:
                decrypted_chunk = fernet.decrypt(enc_chunk)
            except InvalidToken as exc:
                logger.error(
                    f"Dešifravimo klaida chunk'e {chunk_index} faile: {source_path.name}"
                )
                raise ValueError(
                    f"Failo dešifravimas nepavyko (chunk {chunk_index}). "
                    f"Raktas neteisingas arba failas sugadintas."
                ) from exc

            # Paduodame dešifruotą chunk'ą
            yield decrypted_chunk


def decrypt_file_to_bytes(
    source_path: Path,
    user_key: bytes,
) -> bytes:
    """
    gauna: source_path (Path)  – kelias iki užšifruoto failo
           user_key    (bytes) – vartotojo šifravimo raktas
    daro: dešifruoja visą failą ir grąžina kaip bytes.
          NAUDOTI TIK MAŽIEMS FAILAMS (pvz. preview generavimui)!
          Dideliems failams naudoti decrypt_file_streaming().
    grąžina: (bytes) – visas dešifruotas failo turinys
    """
    return b"".join(decrypt_file_streaming(source_path, user_key))


# ============================================
# INTEGRALUMO TIKRINIMAS
# ============================================

def verify_file_integrity(
    source_path: Path,
    user_key: bytes,
    expected_hash: str,
) -> bool:
    """
    gauna: source_path    (Path)  – kelias iki užšifruoto failo
           user_key       (bytes) – vartotojo šifravimo raktas
           expected_hash  (str)   – SHA-256 hash iš DB (įrašytas upload metu)
    daro: dešifruoja failą ir apskaičiuoja SHA-256 hash,
          palygina su saugotu hash'u
    grąžina: (bool) – True jei failas nesugedęs, False jei sugadintas

    NAUDOJIMAS prieš siuntimą vartotojui:
        if not verify_file_integrity(path, user_key, db_hash):
            raise HTTPException(500, "Failas sugadintas!")
    """
    import hashlib

    hasher = hashlib.sha256()

    for chunk in decrypt_file_streaming(source_path, user_key):
        hasher.update(chunk)

    computed_hash = hasher.hexdigest()
    matches = computed_hash == expected_hash

    if not matches:
        logger.error(
            f"Failo integralumo patikrinimas nepavyko! "
            f"Tikėtasi: {expected_hash[:16]}... | "
            f"Gauta: {computed_hash[:16]}..."
        )

    return matches
