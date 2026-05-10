"""
Google Authenticator TOTP (Time-based One-Time Password) modulis.

Apima:
    - TOTP paslapties generavimą (base32)
    - TOTP kodo verifikaciją
    - QR kodo generavimą (PNG, base64) skenavimui su Google Authenticator
    - TOTP URI generavimą rankiniam įvedimui

KAIP VEIKIA TOTP:
    1. Registracija: sistema sugeneruoja 32-simbolių base32 paslaptį
    2. Paslaptis → QR kodas → Google Authenticator ją nuskaito
    3. Kiekvienoje 30s lange: TOTP(paslaptis + dabartinis laikas) → 6 skaitmenų kodas
    4. Prisijungimas: vartotojas įveda kodą → serveris tikrina tą patį skaičiavimą

BIBLIOTEKOS:
    - pyotp  → TOTP algoritmui
    - qrcode → QR kodo paveikslėlio generavimui
    - Pillow → qrcode priklausomybė (PIL)
"""

# ============================================
# IMPORTAI
# ============================================
import base64
import io
import logging

import pyotp
import qrcode
from qrcode.image.pure import PyPNGImage

from app.config import settings


# ============================================
# LOGGER
# ============================================

logger = logging.getLogger(__name__)


# ============================================
# TOTP PASLAPTIES GENERAVIMAS
# ============================================

def generate_totp_secret() -> str:
    """
    gauna: nieko
    daro: generuoja naują atsitiktinę TOTP paslaptį
          base32 formatu (tinkamas Google Authenticator'iui)
    grąžina: (str) – 32 simbolių base32 eilutė (pvz. "JBSWY3DPEHPK3PXP...")

    KADA NAUDOTI:
        - Kuriant naują vartotoją
        - Reset'inant 2FA (admin panel)
    """
    # pyotp.random_base32() naudoja os.urandom() → kriptografiškai saugus
    # 32 simboliai → 160 bitų entropija → pakanka TOTP saugumui
    return pyotp.random_base32()


# ============================================
# TOTP URI IR QR KODAS
# ============================================

def generate_totp_uri(secret: str, username: str) -> str:
    """
    gauna: secret   (str) – TOTP paslaptis (base32)
           username (str) – vartotojo vardas (rodomas Google Authenticator'e)
    daro: sugeneruoja otpauth:// URI formatą, kurį supranta
          Google Authenticator, Authy ir kitos TOTP programėlės
    grąžina: (str) – otpauth:// URI

    PAVYZDYS:
        otpauth://totp/KonradVault:konradas?secret=JBSWY3DP&issuer=KonradVault
    """
    totp = pyotp.TOTP(secret)

    # provisioning_uri sukuria standartinį otpauth:// URI
    # issuer_name – rodomas Google Authenticator programėlėje (skirtingoms paskyros)
    return totp.provisioning_uri(
        name=username,
        issuer_name=settings.app_name,
    )


def generate_qr_code_base64(totp_uri: str) -> str:
    """
    gauna: totp_uri (str) – otpauth:// URI (iš generate_totp_uri())
    daro: generuoja QR kodo paveikslėlį (PNG formatas)
          ir grąžina jį kaip base64 eilutę (tinkama <img src="data:..."> tag'ui)
    grąžina: (str) – base64 koduotas PNG paveikslėlis

    NAUDOJIMAS HTML'e:
        <img src="data:image/png;base64,{qr_code_base64}" />
    """
    # Sukuriame QR kodo objektą
    qr = qrcode.QRCode(
        # Versija None → automatiškai parenka pagal duomenų ilgį
        version=None,

        # Error correction level L → 7% klaidų taisymas (pakanka QR kodui)
        error_correction=qrcode.constants.ERROR_CORRECT_L,

        # Kiekvieno kvadratėlio dydis pikseliais
        box_size=10,

        # Baltas kraštas aplink QR kodą (4 kvadratėliai)
        border=4,
    )

    # Įdedame TOTP URI kaip QR kodo turinį
    qr.add_data(totp_uri)
    qr.make(fit=True)

    # Generuojame PNG paveikslėlį į atminties buferį (ne į failą)
    buffer = io.BytesIO()
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(buffer, format="PNG")

    # Konvertuojame į base64 eilutę
    buffer.seek(0)
    png_bytes = buffer.read()
    return base64.b64encode(png_bytes).decode("utf-8")


# ============================================
# TOTP KODO VERIFIKACIJA
# ============================================

def verify_totp_code(secret: str, code: str) -> bool:
    """
    gauna: secret (str) – TOTP paslaptis iš DB (base32)
           code   (str) – 6 skaitmenų kodas iš vartotojo
    daro: tikrina ar kodas teisingas dabartiniame laiko lange.
          valid_window=1 → priima dabartinį ±1 laiko langą (±30s).
          Tai reikalinga jei vartotojo laikrodis šiek tiek nesutampa.
    grąžina: (bool) – True jei kodas teisingas, False jei ne

    SAUGUMAS:
        - Kiekvienas kodas galioja tik 30s
        - Serverio laikas turi būti teisingas (NTP)
        - valid_window=1 leidžia ±30s nukrypimą
    """
    # Apdorojame kodą – pašaliname tarpus (vartotojas gali netyčia įvesti)
    clean_code = code.strip()

    # Sukuriame TOTP objektą su vartotojo paslaptimi
    totp = pyotp.TOTP(secret)

    # verify() tikrina ar kodas teisingas
    # valid_window=1 → priima current-1, current, current+1 laiko langus
    return totp.verify(clean_code, valid_window=1)


def get_current_totp_code(secret: str) -> str:
    """
    gauna: secret (str) – TOTP paslaptis (base32)
    daro: apskaičiuoja dabartinį TOTP kodą (tą patį kurį rodo Google Auth)
          TIK TEST'UI – production'e niekada nereikia žinoti kodo serveryje
    grąžina: (str) – dabartinis 6 skaitmenų kodas

    PASTABA: Ši funkcija naudojama TIK testavimui (create_user.py ir t.t.)
    """
    return pyotp.TOTP(secret).now()
