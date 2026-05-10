"""
Share Link SQLAlchemy ORM modelis.

Lentelė "share_links" saugo viešus failų dalinimosi nuorodas:
    - Unikalų URL token'ą (32 simboliai, atsitiktinis)
    - Maksimalų atsisiuntimų skaičių (privalomas, pvz. 10)
    - Esamą atsisiuntimų skaičių (didėja kiekvieno download'o metu)
    - Automatinis išjungimas pasiekus limitą (is_disabled)

KAIP VEIKIA:
    1. Vartotojas spaudžia "Share" → įveda max_downloads
    2. Sistema sugeneruoja token → URL: http://<ip>/share/{token}
    3. Share URL pasiunčiamas draugui
    4. Draugas atidaro URL → matomas failas ir "Download" mygtukas
    5. Kiekvienas atsisiuntimas: download_count += 1
    6. Kai download_count >= max_downloads → is_disabled = True
    7. Bandant atsisiųsti išjungtą link'ą → 410 Gone klaida

RYŠIAI:
    ShareLink → File (kiekvienas share link susijęs su vienu failu)
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================
# MODELIS
# ============================================

class ShareLink(Base):
    """
    Share link duomenų bazės modelis.

    Lentelės pavadinimas: share_links
    """

    __tablename__ = "share_links"

    # ----------------------------------------
    # PIRMINIAI LAUKAI
    # ----------------------------------------

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        comment="Unikalus share link identifikatorius",
    )

    # ----------------------------------------
    # PRIKLAUSOMYBĖ
    # ----------------------------------------

    # Kuriam failui skirtas šis share link
    # CASCADE - ištrynus failą, automatiškai ištrinami visi jo share linkai
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Nuoroda į failą (files.id), kurį dalijama",
    )

    # ----------------------------------------
    # SHARE LINK DUOMENYS
    # ----------------------------------------

    # Unikalus URL token'as - tai ta dalis URL adrese po /share/
    # Sugeneruojamas: secrets.token_urlsafe(24) → 32 simboliai
    # URL-safe base64 simboliai: A-Z a-z 0-9 - _
    # Pvz.: "xK4mN8Pw2Qs7Ry1Lv3Bt6Du" → http://<ip>/share/xK4mN8Pw2Qs7Ry1Lv3Bt6Du
    token: Mapped[str] = mapped_column(
        String(64),         # Atsarga - 32 tikri + 32 buferis
        unique=True,        # Du vienodi token'ai negalimi
        nullable=False,
        index=True,         # Dažnai ieškosim pagal token'ą (viešas puslapis)
        comment="Unikalus URL token'as (atsitiktinis, URL-safe base64)",
    )

    # ----------------------------------------
    # ATSISIUNTIMŲ SKAITIKLIS
    # ----------------------------------------

    # Kiek kartų jau buvo atsisiųsta per šį link'ą
    # Didinamas KIEKVIENĄ kartą, kai kas nors parsisiunčia failą
    # ATOMINĖ operacija DB lygiu (SELECT + UPDATE viena transakcija)
    download_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Bendras atsisiuntimų skaičius per šį link'ą",
    )

    # Maksimalus leistinas atsisiuntimų skaičius
    # PRIVALOMAS - vartotojas privalo nurodyti (1-100 ribose, bet DB be apribojimo)
    # Kai download_count >= max_downloads → link automatiškai išjungiamas
    max_downloads: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Maksimalus leistinas atsisiuntimų skaičius (privalomas)",
    )

    # ----------------------------------------
    # LAIKO ŽYMĖS
    # ----------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="Share link sukūrimo data ir laikas (UTC)",
    )

    # ----------------------------------------
    # STATUSAS
    # ----------------------------------------

    # Ar link'as išjungtas
    # True = atsisiuntimų limitas pasiektas arba rankiniu būdu išjungtas
    # Tikrinamas KAI VIENAS IŠ DVIEJŲ:
    #   1. is_disabled = True
    #   2. download_count >= max_downloads
    is_disabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,         # Indeksas - dažnai tikrinsim ar link'as aktyvus
        comment="True = link'as išjungtas (limitas pasiektas arba rankiniu būdu)",
    )

    # ----------------------------------------
    # RYŠIAI (relationships)
    # ----------------------------------------

    # Failas, kurį dalijama
    file: Mapped["File"] = relationship(
        "File",
        back_populates="share_links",
    )

    # ----------------------------------------
    # PAGALBINIAI METODAI
    # ----------------------------------------

    @property
    def downloads_remaining(self) -> int:
        """
        gauna: nieko (savybė)
        daro: apskaičiuoja kiek dar atsisiuntimų liko
        grąžina: (int) - likusių atsisiuntimų skaičius (min 0)
        """
        remaining = self.max_downloads - self.download_count
        # max(0, ...) - kad negrąžintų neigiamo skaičiaus (jei kažkaip viršytas)
        return max(0, remaining)

    @property
    def is_active(self) -> bool:
        """
        gauna: nieko (savybė)
        daro: tikrina ar link'as dar aktyvus (gali būti naudojamas)
              link'as aktyvus KAI ABU sąlygos tenkina:
              1. Nerankiniu būdu išjungtas
              2. Nepasiektas atsisiuntimų limitas
        grąžina: (bool) - True jei link'as aktyvus
        """
        return not self.is_disabled and self.download_count < self.max_downloads

    def disable(self) -> None:
        """
        gauna: nieko
        daro: rankiniu būdu išjungia share link'ą (vartotojas arba sistema)
        grąžina: None
        """
        self.is_disabled = True

    def record_download(self) -> None:
        """
        gauna: nieko
        daro: fiksuoja vieną atsisiuntimą (padidina download_count).
              Jei pasiektas limitas - automatiškai išjungia link'ą.
              PASTABA: DB transakcija turi būti atominė (race condition apsauga)!
        grąžina: None
        """
        self.download_count += 1

        # Automatinis išjungimas pasiekus limitą
        if self.download_count >= self.max_downloads:
            self.is_disabled = True

    def __repr__(self) -> str:
        """
        gauna: nieko
        daro: sukuria žmoniškai skaitomą eilutę debug'ui
        grąžina: (str) - objekto tekstinis vaizdas
        """
        return (
            f"<ShareLink id={self.id} "
            f"token='{self.token[:8]}...' "
            f"file_id={self.file_id} "
            f"downloads={self.download_count}/{self.max_downloads} "
            f"is_active={self.is_active}>"
        )
