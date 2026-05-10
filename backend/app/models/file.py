"""
Failo (File) SQLAlchemy ORM modelis.

Lentelė "files" saugo failų metaduomenis - NEBE patį failą!
Tikras failų turinys saugomas diske, užšifruotas:
    /var/konradvault/encrypted/{stored_filename}

Kiekvienas failas turi:
    - Originalų pavadinimą (rodoma vartotojui)
    - Unikalų UUID pavadinimą diske (saugumo sumetimais)
    - Šifravimo IV (initialization vector) - unikalus kiekvienam failui
    - SHA-256 hash integralumui patikrinti (po dešifravimo)
    - Soft delete (trash bin)

SVARBU apie šifravimą:
    - Failai šifruojami Fernet (AES-128-CBC + HMAC-SHA256)
    - Kiekvienas failas turi UNIKALŲ IV → net tie patys failai skirtingai šifruojami
    - IV saugomas DB, bet tai saugi informacija (IV nėra paslaptis)

RYŠIAI:
    File → User      (kiekvienas failas priklauso vartotojui)
    File → Folder    (failas gali būti aplanke arba root lygyje)
    File → ShareLink (failas gali turėti daug share linkų)
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================
# MODELIS
# ============================================

class File(Base):
    """
    Failo metaduomenų duomenų bazės modelis.

    Lentelės pavadinimas: files
    """

    __tablename__ = "files"

    # ----------------------------------------
    # PIRMINIAI LAUKAI
    # ----------------------------------------

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        comment="Unikalus failo identifikatorius",
    )

    # ----------------------------------------
    # PRIKLAUSOMYBĖ
    # ----------------------------------------

    # Kuriam vartotojui priklauso failas
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Nuoroda į vartotoją (users.id), kuriam priklauso šis failas",
    )

    # Kuriam aplanke yra failas
    # NULL = root lygis (jokiame aplanke)
    # SET NULL - ištrynus aplanką, failai "iškyla" į root lygį
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
        comment="Aplanko id (NULL = root lygis, be aplanko)",
    )

    # ----------------------------------------
    # FAILŲ PAVADINIMAI
    # ----------------------------------------

    # Originalus failo pavadinimas - rodomas vartotojui
    # Saugojamas atskiras nuo disko pavadinimo dėl saugumo
    original_filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Originalus failo pavadinimas kaip įkėlė vartotojas",
    )

    # UUID pavadinimas diske - atsitiktinis, jokios informacijos apie turinį
    # Tai apsaugo nuo:
    #   - Path traversal atakų (../../../etc/passwd stilius)
    #   - Informacijos nutekėjimo (niekas iš pavadinimo nespėja turinio)
    # Formatas: 36 simboliai, pvz. "550e8400-e29b-41d4-a716-446655440000"
    stored_filename: Mapped[str] = mapped_column(
        String(36),
        unique=True,        # UUID privalo būti unikalus
        nullable=False,
        index=True,
        comment="UUID pavadinimas užšifruoto failo diske (saugumo sumetimais ne originalus)",
    )

    # ----------------------------------------
    # FAILO METADUOMENYS
    # ----------------------------------------

    # MIME tipas - pvz. "image/jpeg", "application/pdf", "video/mp4"
    # Naudojamas preview funkcionalumui (ar galima rodyti thumbnail)
    # NULL = nepavyko nustatyti tipo
    mime_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="MIME tipas (pvz. image/jpeg, application/pdf)",
    )

    # Failo dydis baitais - saugojamas DB (neskaitome iš disko kiekvieną kartą)
    # Naudojamas: storage quota skaičiavimui, vartotojui rodyti
    # BigInteger - nes 500MB = 524288000 baitų (Integer max ~2.1 mlrd - tilptų, bet BigInteger saugiau)
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Originalaus (ne užšifruoto) failo dydis baitais",
    )

    # ----------------------------------------
    # ŠIFRAVIMO DUOMENYS
    # ----------------------------------------

    # Šifravimo Initialization Vector (IV) - unikalus kiekvienam failui
    # IV nėra paslaptis - jis gali būti viešas, tik turi būti unikalus
    # Fernet tvarkosi su IV pats, bet mums reikia saugoti dėl streaming
    # BLOB tipas - baitai
    encryption_iv: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        comment="Šifravimo IV (initialization vector) - unikalus kiekvienam failui",
    )

    # SHA-256 hash originalaus (ne užšifruoto) failo turinio
    # Naudojamas integralumui patikrinti po dešifravimo:
    #   1. Upload: apskaičiuojame → saugome DB
    #   2. Download: dešifruojame → apskaičiuojame → lyginame su DB
    #   Jei nesutampa → failas sugadintas (arba manipuliuotas)!
    file_hash: Mapped[str | None] = mapped_column(
        String(64),         # SHA-256 hex = 64 simboliai
        nullable=True,
        comment="SHA-256 hash (hex) originalaus failo - integralumo tikrinimui",
    )

    # ----------------------------------------
    # LAIKO ŽYMĖS
    # ----------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="Failo įkėlimo data ir laikas (UTC)",
    )

    # ----------------------------------------
    # SOFT DELETE (TRASH BIN)
    # ----------------------------------------

    # Ar failas "ištrintas" - soft delete
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="True = failas trash bin'e, False = aktyvus",
    )

    # Kada buvo perkeltas į trash
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Trash perkelimo data (NULL = neištrinta)",
    )

    # ----------------------------------------
    # RYŠIAI (relationships)
    # ----------------------------------------

    # Failo savininkas
    owner: Mapped["User"] = relationship(
        "User",
        back_populates="files",
    )

    # Aplankas, kuriame yra failas (gali būti None = root)
    folder: Mapped["Folder | None"] = relationship(
        "Folder",
        back_populates="files",
    )

    # Share linkai šiam failui
    # Ištrynus failą - ištrinami ir visi jo share linkai
    share_links: Mapped[list["ShareLink"]] = relationship(
        "ShareLink",
        back_populates="file",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ----------------------------------------
    # PAGALBINIAI METODAI
    # ----------------------------------------

    @property
    def size_mb(self) -> float:
        """
        gauna: nieko (savybė)
        daro: konvertuoja size_bytes į megabaitus (rodymui vartotojui)
        grąžina: (float) - failo dydis MB, apvalinta 2 skaitmenimis
        """
        return round(self.size_bytes / (1024 * 1024), 2)

    @property
    def is_image(self) -> bool:
        """
        gauna: nieko (savybė)
        daro: tikrina ar failas yra paveikslėlis pagal MIME tipą
        grąžina: (bool) - True jei paveikslėlis
        """
        if not self.mime_type:
            return False
        return self.mime_type.startswith("image/")

    @property
    def is_pdf(self) -> bool:
        """
        gauna: nieko (savybė)
        daro: tikrina ar failas yra PDF
        grąžina: (bool) - True jei PDF
        """
        return self.mime_type == "application/pdf"

    @property
    def extension(self) -> str:
        """
        gauna: nieko (savybė)
        daro: ištraukia failo plėtinį iš original_filename
        grąžina: (str) - plėtinys mažosiomis raidėmis (pvz. "pdf", "jpg", "")
        """
        if "." not in self.original_filename:
            return ""
        return self.original_filename.rsplit(".", 1)[-1].lower()

    def soft_delete(self) -> None:
        """
        gauna: nieko
        daro: žymi failą kaip "ištrintą" (perkeltas į trash bin)
        grąžina: None
        """
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def restore(self) -> None:
        """
        gauna: nieko
        daro: atgrąžina failą iš trash bin
        grąžina: None
        """
        self.is_deleted = False
        self.deleted_at = None

    def __repr__(self) -> str:
        """
        gauna: nieko
        daro: sukuria žmoniškai skaitomą eilutę debug'ui
        grąžina: (str) - objekto tekstinis vaizdas
        """
        return (
            f"<File id={self.id} "
            f"original='{self.original_filename}' "
            f"stored='{self.stored_filename}' "
            f"size={self.size_bytes}B "
            f"is_deleted={self.is_deleted}>"
        )
