"""
Failų I/O pagalbinis modulis.

Atsakingas už:
    - Šifruotų failų saugojimą diske
    - Failų trynimą diske
    - Kelių generavimą (UUID → absoliutus kelias)
    - Egzistavimo tikrinimą

FAILŲ SAUGOJIMO SCHEMA:
    {ENCRYPTED_FILES_DIR}/{uuid}
    Pvz.: /var/konradvault/encrypted/3f2a1b4c-...

SVARBU:
    Šis modulis dirba TIK su disko operacijomis.
    Šifravimą atlieka encryption.py.
    Metaduomenis saugo DB (api/files.py).
"""

# ============================================
# IMPORTAI
# ============================================
import logging
import os
import uuid
from pathlib import Path
from urllib.parse import quote

from app.config import settings


def content_disposition(disposition: str, filename: str) -> str:
    """Sudaro saugią Content-Disposition antraštę.

    HTTP antraštės koduojamos latin-1, todėl ne-ASCII simboliai pavadinime
    (pvz. em-brūkšnys '—', lietuviškos raidės) sukeltų UnicodeEncodeError ir 500.
    Sprendimas (RFC 5987): ASCII atsarginis filename + filename*=UTF-8''<percent>.

    disposition: "attachment" arba "inline".
    """
    ascii_name = (
        filename.encode("ascii", "ignore").decode().replace('"', "'").strip()
        or "download"
    )
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# ============================================
# LOGGER
# ============================================

logger = logging.getLogger(__name__)


# ============================================
# UUID GENERAVIMAS IR KELIAI
# ============================================

def generate_file_uuid() -> str:
    """
    gauna: nieko
    daro: sugeneruoja unikalų UUID4 failo identifikatorių.
          Šis UUID naudojamas kaip failo vardas diske (be plėtinio).
    grąžina: (str) – UUID4 string formatu, pvz. "3f2a1b4c-..."
    """
    return str(uuid.uuid4())


def get_encrypted_file_path(stored_filename: str) -> Path:
    """
    gauna: stored_filename (str) – UUID failo vardas (be plėtinio)
    daro: grąžina absoliutų kelią iki šifruoto failo diske.
          Nekuria failo – tik apskaičiuoja kelią.
    grąžina: (Path) – absoliutus kelias, pvz. /var/konradvault/encrypted/abc-123
    """
    return Path(settings.encrypted_files_dir) / stored_filename


# ============================================
# LAIKINI FAILAI (UPLOAD METU)
# ============================================

def ensure_storage_directory() -> None:
    """
    gauna: nieko
    daro: užtikrina, kad šifruotų failų katalogas egzistuoja.
          Sukuria jei neegzistuoja (rekursyviai).
    grąžina: None
    iškelia: PermissionError – jei nėra teisių sukurti katalogą
    """
    storage_dir = Path(settings.encrypted_files_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Saugojimo katalogas paruoštas: {storage_dir}")


def save_upload_to_temp(upload_data: bytes, temp_dir: Path | None = None) -> Path:
    """
    gauna: upload_data (bytes)    – plaintext failo turinys iš upload'o
           temp_dir   (Path|None) – laikinas katalogas (default: OS temp)
    daro: išsaugo plaintext duomenis į laikiną failą diske.
          Naudojama kai reikia perduoti failą encrypt_file_to_path().
          Laikinas failas turi būti ištrintas po šifravimo!
    grąžina: (Path) – kelias iki laikino failo
    iškelia: IOError – jei nepavyksta rašyti
    """
    import tempfile

    if temp_dir is None:
        temp_dir = Path(tempfile.gettempdir())

    temp_dir.mkdir(parents=True, exist_ok=True)

    # UUID vardas – apsauga nuo race condition tarp kelių upload'ų
    temp_filename = f"konradvault_tmp_{uuid.uuid4().hex}"
    temp_path = temp_dir / temp_filename

    temp_path.write_bytes(upload_data)
    logger.debug(f"Laikinas failas sukurtas: {temp_path} ({len(upload_data)} B)")
    return temp_path


async def stream_upload_to_temp(
    upload_file,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
    temp_dir: Path | None = None,
) -> tuple[Path, int]:
    """
    gauna: upload_file (UploadFile) – FastAPI UploadFile objektas
           max_bytes   (int)        – maksimalus leidžiamas dydis (jei viršija → klaida)
           chunk_size  (int)        – chunk dydis bytes (default 1MB)
           temp_dir    (Path|None)  – laikinas katalogas (default OS temp)
    daro: srautu (chunk po chunk) saugo įkeliamą failą į laikiną disko vietą.
          NEKRAUNA viso failo į RAM – kiekvienas chunk'as iš karto rašomas į diską.
          Tikrina dydį po kiekvieno chunk'o – jei viršijama, sustabdo ir grąžina klaidą.
    grąžina: (Path, int) – (laikinas kelias, bendras dydis baitais)
    iškelia: ValueError – jei failas viršija max_bytes
             IOError    – jei nepavyksta rašyti
    """
    import tempfile

    if temp_dir is None:
        temp_dir = Path(tempfile.gettempdir())

    temp_dir.mkdir(parents=True, exist_ok=True)

    temp_filename = f"konradvault_tmp_{uuid.uuid4().hex}"
    temp_path = temp_dir / temp_filename

    total_size = 0

    try:
        with open(temp_path, "wb") as f:
            while True:
                chunk = await upload_file.read(chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)

                # Saugumas: jei viršyta riba – sustabdome
                if total_size > max_bytes:
                    f.close()
                    temp_path.unlink(missing_ok=True)
                    raise ValueError(
                        f"Failas viršija leidžiamą dydį ({max_bytes} B)"
                    )

                f.write(chunk)
    except Exception:
        # Klaida → išvalome temp
        temp_path.unlink(missing_ok=True)
        raise

    logger.debug(f"Streaming upload baigtas: {temp_path} ({total_size} B)")
    return temp_path, total_size


def delete_temp_file(temp_path: Path) -> None:
    """
    gauna: temp_path (Path) – kelias iki laikino failo
    daro: ištrina laikiną failą diske, ignoruoja jei neegzistuoja.
          Visada kviečiama po šifravimo operacijos (finally bloke).
    grąžina: None
    """
    try:
        if temp_path.exists():
            temp_path.unlink()
            logger.debug(f"Laikinas failas ištrintas: {temp_path}")
    except OSError as exc:
        # Nekritiška – OS valys temp failus pats
        logger.warning(f"Nepavyko ištrinti laikino failo {temp_path}: {exc}")


# ============================================
# ŠIFRUOTŲ FAILŲ OPERACIJOS
# ============================================

def encrypted_file_exists(stored_filename: str) -> bool:
    """
    gauna: stored_filename (str) – UUID failo vardas
    daro: patikrina ar šifruotas failas egzistuoja diske
    grąžina: (bool) – True jei failas randamas
    """
    path = get_encrypted_file_path(stored_filename)
    return path.exists() and path.is_file()


def delete_encrypted_file(stored_filename: str) -> bool:
    """
    gauna: stored_filename (str) – UUID failo vardas
    daro: ištrina šifruotą failą iš disko.
          Naudojama TIKAI galutinio trynimo metu (ne soft delete!).
          Soft delete tik pažymi is_deleted=True DB – failas lieka diske.
    grąžina: (bool) – True jei sėkmingai ištrinta, False jei neegzistavo
    iškelia: PermissionError – jei nėra teisių trinti
    """
    path = get_encrypted_file_path(stored_filename)

    if not path.exists():
        logger.warning(f"Bandyta ištrinti neegzistuojantį failą: {stored_filename}")
        return False

    path.unlink()
    logger.info(f"Šifruotas failas ištrintas iš disko: {stored_filename}")
    return True


def get_encrypted_file_size(stored_filename: str) -> int:
    """
    gauna: stored_filename (str) – UUID failo vardas
    daro: grąžina šifruoto failo dydį baitais.
          Šifruotas dydis ~3% didesnis nei originalus (Fernet overhead).
    grąžina: (int) – failo dydis baitais, 0 jei neegzistuoja
    """
    path = get_encrypted_file_path(stored_filename)

    if not path.exists():
        return 0

    return path.stat().st_size


# ============================================
# SAUGOJIMO STATISTIKA (ADMIN)
# ============================================

def get_storage_stats() -> dict:
    """
    gauna: nieko
    daro: apskaičiuoja šifruotų failų katalogo statistiką.
          Naudojama /api/admin/stats endpoint'e.
    grąžina: (dict) – {total_bytes, total_mb, file_count}
    """
    storage_dir = Path(settings.encrypted_files_dir)

    if not storage_dir.exists():
        return {"total_bytes": 0, "total_mb": 0.0, "file_count": 0}

    file_count = 0
    total_bytes = 0

    try:
        for entry in os.scandir(storage_dir):
            if entry.is_file(follow_symlinks=False):
                file_count += 1
                total_bytes += entry.stat().st_size
    except PermissionError as exc:
        logger.error(f"Nepavyko skaityti saugojimo katalogo: {exc}")

    return {
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "file_count": file_count,
    }
