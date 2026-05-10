"""
Paveikslėlių thumbnail'ų generavimo modulis.

TIKSLAS:
    Vietoj pilno paveikslėlio dešifravimo dashboard'e (kuris yra brangus –
    50 paveikslėlių aplanke = 50 lygiagrečių dešifravimų ir pilno failo RAM
    naudojimo), generuojame mažus thumbnail'us po upload'o ir saugome juos
    šifruotus tame pačiame `encrypted/` kataloge su `_thumb` sufiksu.

NAUDOJIMAS:
    1. Po sėkmingo upload'o (api/files.py) kviečiama generate_thumbnail()
    2. Frontend kreipiasi į GET /api/files/{id}/thumbnail
    3. Backend grąžina šifruoto thumbnail'o turinį (mažas dydis – ~10-50KB)

PRIKLAUSOMYBĖS:
    Pillow (PIL) – jau yra requirements.txt
    Jei nebus įdiegta – funkcijos grąžins None ir thumbnail'ai bus
    nesugeneruoti (sistema veiks normaliai – frontend turi fallback).

THUMBNAIL DYDIS:
    256x256 px max (proporciškai apkarpyta)
    JPEG kokybė 80% – balansas tarp dydžio ir vizualaus kokybės
"""

# ============================================
# IMPORTAI
# ============================================
import io
import logging
from pathlib import Path

from app.core.encryption import encrypt_bytes_to_path


# ============================================
# LOGGER
# ============================================
logger = logging.getLogger(__name__)


# ============================================
# KONSTANTOS
# ============================================
THUMBNAIL_MAX_SIZE = (256, 256)        # max dydis pikseliais (proporcingai)
THUMBNAIL_QUALITY = 80                  # JPEG kokybė 0–100
THUMBNAIL_FORMAT = "JPEG"               # Pasirenkam JPEG nors PNG → JPEG
THUMBNAIL_SUFFIX = ".thumb"             # disko failo sufiksas


# ============================================
# PALAIKOMI MIME TIPAI
# ============================================

def is_thumbnailable(mime_type: str | None) -> bool:
    """
    gauna: mime_type (str|None) – failo MIME tipas
    daro: tikrina ar mes galime generuoti thumbnail'ą šiam failo tipui
    grąžina: (bool) – True jei galima
    """
    if not mime_type:
        return False
    return mime_type.startswith("image/") and mime_type not in {
        "image/svg+xml",     # SVG nepalaiko PIL'as standartiškai
        "image/heic",        # iOS formato palaikymas reikalauja papildomų lib
        "image/heif",
    }


# ============================================
# THUMBNAIL GENERAVIMAS
# ============================================

def generate_thumbnail_bytes(image_bytes: bytes) -> bytes | None:
    """
    gauna: image_bytes (bytes) – plaintext paveikslėlio baitai
    daro: sumažina paveikslėlį iki THUMBNAIL_MAX_SIZE proporcingai,
          konvertuoja į JPEG. Jei Pillow nesumontuotas – grąžina None
          (graceful fallback – sistema veikia toliau).
    grąžina: (bytes|None) – JPEG thumbnail'o baitai arba None jei nepavyko
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        logger.warning(
            "Pillow neįdiegtas – thumbnail generavimas praleistas. "
            "Įdiekite: pip install Pillow"
        )
        return None

    try:
        # Atidarome paveikslėlį iš baitų buffer'io
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Pakeičiame orientaciją pagal EXIF (kad nepasikeistų rotacija)
            img = ImageOps.exif_transpose(img)

            # Konvertuojame į RGB (JPEG nepalaiko alpha kanalo)
            if img.mode in ("RGBA", "LA", "P"):
                # Sukuriame baltą foną ir uždedame paveikslėlį
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode in ("RGBA", "LA"):
                    background.paste(img, mask=img.split()[-1])
                else:
                    background.paste(img)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Proporcingai sumažiname (LANCZOS – aukšta kokybė)
            img.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)

            # Išsaugome JPEG formatu į buffer'į
            output = io.BytesIO()
            img.save(output, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY, optimize=True)
            return output.getvalue()

    except Exception as exc:
        logger.warning(f"Thumbnail generavimas nepavyko: {exc}")
        return None


def generate_and_encrypt_thumbnail(
    image_bytes: bytes,
    encrypted_dest_dir: Path,
    stored_filename: str,
    user_key: bytes,
) -> bool:
    """
    gauna: image_bytes        (bytes) – plaintext paveikslėlio baitai
           encrypted_dest_dir (Path)  – kur saugoti (ENCRYPTED_FILES_DIR)
           stored_filename    (str)   – failo UUID (be plėtinio)
           user_key           (bytes) – vartotojo šifravimo raktas
    daro: 1. Sugeneruoja thumbnail'ą iš originalaus paveikslėlio
          2. Šifruoja jį su vartotojo raktu
          3. Saugo: {encrypted_dest_dir}/{stored_filename}.thumb
    grąžina: (bool) – True jei pavyko, False jei ne (sistema veikia toliau)
    """
    thumb_bytes = generate_thumbnail_bytes(image_bytes)
    if thumb_bytes is None:
        return False

    thumb_path = encrypted_dest_dir / f"{stored_filename}{THUMBNAIL_SUFFIX}"

    try:
        encrypt_bytes_to_path(
            data=thumb_bytes,
            dest_path=thumb_path,
            user_key=user_key,
        )
        logger.debug(f"Thumbnail sugeneruotas: {thumb_path.name} ({len(thumb_bytes)} B)")
        return True
    except Exception as exc:
        logger.warning(f"Thumbnail šifravimas nepavyko: {exc}")
        return False


# ============================================
# THUMBNAIL TRYNIMAS
# ============================================

def delete_thumbnail(encrypted_dest_dir: Path, stored_filename: str) -> bool:
    """
    gauna: encrypted_dest_dir (Path) – kur saugomas (ENCRYPTED_FILES_DIR)
           stored_filename    (str)  – failo UUID
    daro: ištrina thumbnail'o failą iš disko (jei egzistuoja).
          Naudojama kai galutinai trinamas pagrindinis failas.
    grąžina: (bool) – True jei buvo ir buvo ištrintas
    """
    thumb_path = encrypted_dest_dir / f"{stored_filename}{THUMBNAIL_SUFFIX}"

    if not thumb_path.exists():
        return False

    try:
        thumb_path.unlink()
        return True
    except OSError as exc:
        logger.warning(f"Nepavyko ištrinti thumbnail'o {thumb_path.name}: {exc}")
        return False


def thumbnail_exists(encrypted_dest_dir: Path, stored_filename: str) -> bool:
    """
    gauna: encrypted_dest_dir (Path) – ENCRYPTED_FILES_DIR
           stored_filename    (str)  – failo UUID
    daro: tikrina ar thumbnail'as egzistuoja diske
    grąžina: (bool) – True jei egzistuoja
    """
    thumb_path = encrypted_dest_dir / f"{stored_filename}{THUMBNAIL_SUFFIX}"
    return thumb_path.exists() and thumb_path.is_file()


def get_thumbnail_path(encrypted_dest_dir: Path, stored_filename: str) -> Path:
    """
    gauna: encrypted_dest_dir (Path) – ENCRYPTED_FILES_DIR
           stored_filename    (str)  – failo UUID
    daro: grąžina absoliutų kelią iki thumbnail'o failo (gali neegzistuoti).
    grąžina: (Path) – thumbnail'o kelias
    """
    return encrypted_dest_dir / f"{stored_filename}{THUMBNAIL_SUFFIX}"
