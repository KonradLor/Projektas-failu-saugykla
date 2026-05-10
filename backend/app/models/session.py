"""
Vartotojo sesijos (Session) SQLAlchemy ORM modelis.

Lentelė "sessions" saugo aktyvias prisijungimo sesijas:
    - Session token'ą (saugomas HTTP-only cookie'yje)
    - Galiojimo laiką (24 valandos)
    - IP adresą ir naršyklės informaciją (papildoma saugumo info)

KAIP VEIKIA:
    1. Vartotojas prisijungia (password + 2FA) → sukuriama nauja sesija DB
    2. Session token → HTTP-only cookie → naršyklė siunčia su kiekviena užklausa
    3. Kiekviena užklausa: tikriname DB ar sesija egzistuoja ir negaliojusi
    4. Logout: ištriname sesijos įrašą iš DB
    5. Pasibaigus galiojimui → sesija nebegalioja (automatiškai arba per cleanup)

SAUGUMAS:
    - HTTP-only cookie → JavaScript negali pasiekti (XSS apsauga)
    - Secure + SameSite=Strict → CSRF apsauga
    - Session token saugomas DB → galima centralizuotai atšaukti
    - Vienas vartotojas gali turėti KELIS aktyvius session'us
      (pvz. naršyklė + telefonas)

RYŠIAI:
    Session → User (kiekviena sesija priklauso vartotojui)
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base


# ============================================
# MODELIS
# ============================================

class Session(Base):
    """
    Vartotojo sesijos duomenų bazės modelis.

    Lentelės pavadinimas: sessions
    """

    __tablename__ = "sessions"

    # ----------------------------------------
    # PIRMINIAI LAUKAI
    # ----------------------------------------

    # Session token - tai pats sesijos "raktas"
    # Saugomas vartotojo naršyklėje kaip HTTP-only cookie
    # Generuojamas: secrets.token_urlsafe(32) → ~43 simboliai
    # Naudojame kaip PRIMARY KEY (ne int id) - tiesioginis token'o paieška DB
    token: Mapped[str] = mapped_column(
        String(128),        # Atsarga - token'ai paprastai ~43-64 simboliai
        primary_key=True,
        comment="Session token (HTTP-only cookie reikšmė)",
    )

    # ----------------------------------------
    # PRIKLAUSOMYBĖ
    # ----------------------------------------

    # Kuriam vartotojui priklauso sesija
    # CASCADE - ištrynus vartotoją, ištrinamos ir jo sesijos
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,         # Dažnai ieškom visų vartotojo sesijų (logout all)
        comment="Nuoroda į vartotoją (users.id), kuriam priklauso sesija",
    )

    # ----------------------------------------
    # LAIKO ŽYMĖS
    # ----------------------------------------

    # Sesijos sukūrimo laikas
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="Sesijos sukūrimo data ir laikas (UTC)",
    )

    # Sesijos galiojimo pabaiga
    # Po šio laiko sesija nebegalioja - vartotojas turi iš naujo prisijungti
    # Default: sukūrimo laikas + SESSION_EXPIRE_HOURS (iš konfigūracijos)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(hours=settings.session_expire_hours),
        index=True,         # Indeksas - dažnai filtruosim pasibaigusias sesijas
        comment="Sesijos galiojimo pabaigos data ir laikas (UTC)",
    )

    # ----------------------------------------
    # PAPILDOMA SAUGUMO INFORMACIJA
    # ----------------------------------------

    # IP adresas iš kurio buvo prisijungta
    # Naudojama:
    #   - Log'uose įtartinos veiklos stebėjimui
    #   - Admin panel'e (rodoma paskutinis IP)
    # GDPR pastaba: IP adresas yra asmens duomenys - saugojame tik tam tikrą laiką
    ip_address: Mapped[str | None] = mapped_column(
        String(45),         # IPv4 max 15, IPv6 max 39, su tarpais/portais max 45
        nullable=True,
        comment="IP adresas iš kurio buvo prisijungta (IPv4 arba IPv6)",
    )

    # Naršyklės identifikatorius (User-Agent header)
    # Naudojama saugumo log'ui - galima matyti "Sekmadienį prisijungė Chrome on Windows"
    # Gali būti ilgas → Text tipas
    user_agent: Mapped[str | None] = mapped_column(
        String(500),        # User-Agent gali būti ilgas, bet apribojame
        nullable=True,
        comment="Naršyklės User-Agent header'is (saugumo informacijai)",
    )

    # ----------------------------------------
    # RYŠIAI (relationships)
    # ----------------------------------------

    # Sesijos vartotojas
    user: Mapped["User"] = relationship(
        "User",
        back_populates="sessions",
    )

    # ----------------------------------------
    # PAGALBINIAI METODAI
    # ----------------------------------------

    @property
    def is_expired(self) -> bool:
        """
        gauna: nieko (savybė)
        daro: tikrina ar sesija jau pasibaigė lyginant su dabartiniu UTC laiku
        grąžina: (bool) - True jei sesija nebegalioja
        """
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """
        gauna: nieko (savybė)
        daro: tikrina ar sesija galiojanti (atvirkštinis is_expired)
        grąžina: (bool) - True jei sesija dar galioja
        """
        return not self.is_expired

    @property
    def time_remaining(self) -> timedelta:
        """
        gauna: nieko (savybė)
        daro: apskaičiuoja kiek laiko liko iki sesijos pasibaigimo
        grąžina: (timedelta) - laikas iki pasibaigimo (gali būti neigiamas jei pasibaigė)
        """
        return self.expires_at - datetime.now(timezone.utc)

    def renew(self) -> None:
        """
        gauna: nieko
        daro: pratęsia sesijos galiojimą - prideda SESSION_EXPIRE_HOURS nuo dabar.
              Kviečiama kai vartotojas aktyviai naudojasi sistema - kad neišmestų.
        grąžina: None
        """
        self.expires_at = datetime.now(timezone.utc) + timedelta(
            hours=settings.session_expire_hours
        )

    def __repr__(self) -> str:
        """
        gauna: nieko
        daro: sukuria žmoniškai skaitomą eilutę debug'ui
        grąžina: (str) - objekto tekstinis vaizdas
        """
        return (
            f"<Session token='{self.token[:8]}...' "
            f"user_id={self.user_id} "
            f"expires_at={self.expires_at.isoformat()} "
            f"is_valid={self.is_valid}>"
        )
