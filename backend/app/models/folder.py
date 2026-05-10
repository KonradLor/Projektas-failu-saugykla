"""
Aplanko (Folder) SQLAlchemy ORM modelis.

Lentelė "folders" saugo vartotojų aplankų struktūrą:
    - Pavadinimą ir spalvą (8 preset spalvų pasirinkimas)
    - Tėvinį aplanką (parent_id → rekursinė medžio struktūra)
    - Soft delete (is_deleted + deleted_at) - "Trash bin" funkcija

STRUKTŪROS PAVYZDYS:
    Root (parent_id = NULL)
    ├── Dokumentai        (id=1, parent_id=NULL)
    │   ├── Darbas        (id=3, parent_id=1)
    │   └── Asmeninis     (id=4, parent_id=1)
    └── Nuotraukos        (id=2, parent_id=NULL)

RYŠIAI:
    Folder → User   (kiekvienas aplankas priklauso vartotojui)
    Folder → Folder (tėvinis-vaikinis aplanko ryšys, self-referential)
    Folder → File   (aplankas gali turėti daug failų)
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================
# MODELIS
# ============================================

class Folder(Base):
    """
    Aplanko duomenų bazės modelis.

    Lentelės pavadinimas: folders
    """

    __tablename__ = "folders"

    # ----------------------------------------
    # PIRMINIAI LAUKAI
    # ----------------------------------------

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        comment="Unikalus aplanko identifikatorius",
    )

    # ----------------------------------------
    # PRIKLAUSOMYBĖ
    # ----------------------------------------

    # Kuriam vartotojui priklauso šis aplankas
    # ondelete="CASCADE" - ištrynus vartotoją, automatiškai ištrinami jo aplankalai
    # (papildoma apsauga be SQLAlchemy cascade)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,         # Indeksas - dažnai ieškome aplankalų pagal user_id
        comment="Nuoroda į vartotoją (users.id), kuriam priklauso šis aplankas",
    )

    # Tėvinis aplankas - NULL reiškia "root" lygio aplankas
    # self-referential FK - nuoroda į tą pačią lentelę
    # SET NULL - ištrynus tėvinį aplanką, vaikiniai aplankalai "iškyla" į root
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
        comment="Tėvinio aplanko id (NULL = root lygis)",
    )

    # ----------------------------------------
    # APLANKO DUOMENYS
    # ----------------------------------------

    # Aplanko pavadinimas - gali kartotis (net tame pačiame lygyje)
    # Unikalumą užtikrina UX (UI neleis sukurti tokio paties pavadinimo)
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Aplanko pavadinimas (rodomas vartotojui)",
    )

    # Aplanko spalva - HEX formatas (#RRGGBB)
    # 8 preset spalvos: #3B82F6, #22C55E, #EF4444, #EAB308,
    #                   #F97316, #8B5CF6, #EC4899, #6B7280
    # Default: mėlyna (#3B82F6)
    color: Mapped[str] = mapped_column(
        String(7),              # HEX formatas: # + 6 simboliai = 7
        nullable=False,
        default="#3B82F6",
        comment="Aplanko spalva HEX formatu (pvz. #3B82F6)",
    )

    # ----------------------------------------
    # LAIKO ŽYMĖS
    # ----------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="Aplanko sukūrimo data ir laikas (UTC)",
    )

    # Paskutinis atnaujinimas - pervadinus arba pakeitus spalvą
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),  # Automatiškai atnaujinama
        comment="Paskutinio pakeitimo data ir laikas (UTC)",
    )

    # ----------------------------------------
    # SOFT DELETE (TRASH BIN)
    # ----------------------------------------

    # Ar aplankas "ištrintas" - soft delete, ne tikras trynimas
    # True = aplankas trash bin'e, rodomas Trash puslapyje
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,         # Indeksas - dažnai filtruojame is_deleted=False
        comment="True = aplankas trash bin'e, False = aktyvus",
    )

    # Kada buvo "ištrinta" (perkeltas į trash)
    # NULL = dar neištrinta
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Trash perkelimo data ir laikas (NULL = neištrinta)",
    )

    # ----------------------------------------
    # RYŠIAI (relationships)
    # ----------------------------------------

    # Priklausomybė vartotojui
    owner: Mapped["User"] = relationship(
        "User",
        back_populates="folders",
    )

    # Tėvinis aplankas (nuoroda į save - parent)
    # remote_side=[id] - nurodo, kad id yra "vieno" pusė ryšio
    parent: Mapped["Folder | None"] = relationship(
        "Folder",
        back_populates="children",
        remote_side="Folder.id",
        foreign_keys=[parent_id],
    )

    # Vaikiniai aplankalai (nuoroda į save - children)
    children: Mapped[list["Folder"]] = relationship(
        "Folder",
        back_populates="parent",
        foreign_keys="Folder.parent_id",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # Failai šiame aplanke
    files: Mapped[list["File"]] = relationship(
        "File",
        back_populates="folder",
        lazy="select",
    )

    # ----------------------------------------
    # PAGALBINIAI METODAI
    # ----------------------------------------

    def soft_delete(self) -> None:
        """
        gauna: nieko
        daro: žymi aplanką kaip "ištrintą" (perkeltas į trash bin).
              Tai soft delete - duomenys lieka DB, tik paslėpti.
        grąžina: None
        """
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def restore(self) -> None:
        """
        gauna: nieko
        daro: atgrąžina aplanką iš trash bin - pažymi kaip aktyvų
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
            f"<Folder id={self.id} "
            f"name='{self.name}' "
            f"user_id={self.user_id} "
            f"parent_id={self.parent_id} "
            f"is_deleted={self.is_deleted}>"
        )
