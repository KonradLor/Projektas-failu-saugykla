"""
Aplankų API – aplankų valdymo modulis.

ENDPOINT'AI:
    POST   /api/folders              – sukurti naują aplanką
    GET    /api/folders              – gauti vartotojo aplankų sąrašą
    GET    /api/folders/tree         – gauti aplankų medžio struktūrą (sidebar'ui)
    GET    /api/folders/{id}         – gauti vieno aplanko duomenis
    PATCH  /api/folders/{id}         – pervadinti arba pakeisti spalvą
    DELETE /api/folders/{id}         – perkelti į šiukšlinę (soft delete)
    POST   /api/folders/{id}/restore – atkurti iš šiukšlinės

MEDŽIO STRUKTŪRA:
    Aplankalai gali būti įdėti vienas į kitą (parent_id).
    GET /tree grąžina visą medį vienu JSON su children[] rekursija.
    Tai leidžia sidebar'ui atvaizduoti visą struktūrą be kelių užklausų.

SOFT DELETE:
    Trinant aplanką → soft delete (is_deleted=True).
    Visi failai ir vaikiniai aplankalai LIEKA – tik aplankas pažymimas.
    Galutinis trynimas – per /api/trash endpoint'ą.
"""

# ============================================
# IMPORTAI
# ============================================
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.file import File as FileModel
from app.models.folder import Folder
from app.models.user import User
from app.schemas.folder import (
    FolderCreate,
    FolderResponse,
    FolderTreeResponse,
    FolderUpdate,
)
from app.utils.api_helpers import folder_to_response as _folder_to_response


# ============================================
# ROUTER IR LOGGER
# ============================================

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# PAGALBINĖS FUNKCIJOS
# ============================================

def _get_folder_or_404(
    folder_id: int,
    user: User,
    db: DBSession,
    *,
    allow_deleted: bool = False,
) -> Folder:
    """
    gauna: folder_id     (int)      – aplanko ID iš URL
           user          (User)     – prisijungęs vartotojas
           db            (DBSession) – DB sesija
           allow_deleted (bool)     – ar leisti grąžinti ištryntus aplankus
    daro: ieško aplanko DB pagal ID ir vartotoją.
          Patikrina nuosavybę – vartotojas gali matyti TIK savo aplankus.
    grąžina: (Folder) – rastas aplanko ORM objektas
    iškelia: HTTPException 404 – jei aplankas nerastas arba ne vartotojo
    """
    query = db.query(Folder).filter(
        Folder.id == folder_id,
        Folder.user_id == user.id,
    )

    if not allow_deleted:
        query = query.filter(Folder.is_deleted == False)  # noqa: E712

    folder = query.first()

    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aplankas #{folder_id} nerastas.",
        )

    return folder


# PASTABA: _folder_to_response importuotas iš app/utils/api_helpers.py –
# bendras helper'is folders.py ir search.py moduliams.


def _build_tree(
    folder: Folder,
    file_counts: dict[int, int],
) -> FolderTreeResponse:
    """
    gauna: folder      (Folder)       – aplanko ORM objektas (su children)
           file_counts (dict[int,int]) – {folder_id: file_count} žodynas
    daro: rekursyviai sukuria FolderTreeResponse medžio struktūrą.
          Naudoja SQLAlchemy eager-loaded children ryšį.
    grąžina: (FolderTreeResponse) – aplanko mazgas su vaikiniais aplankalais
    """
    return FolderTreeResponse(
        id=folder.id,
        name=folder.name,
        color=folder.color,
        parent_id=folder.parent_id,
        file_count=file_counts.get(folder.id, 0),
        children=[
            _build_tree(child, file_counts)
            for child in folder.children
            if not child.is_deleted   # Neįtraukiame ištrintų vaikinių
        ],
    )


# ============================================
# POST /api/folders
# ============================================

@router.post(
    "",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sukurti naują aplanką",
)
def create_folder(
    payload: FolderCreate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FolderResponse:
    """
    gauna: payload      (FolderCreate) – { name, parent_id?, color? }
           current_user                – prisijungęs vartotojas
           db                          – DB sesija
    daro: sukuria naują aplanką.
          Jei nurodytas parent_id – patikrina ar tėvinis aplankas egzistuoja
          ir priklauso šiam vartotojui.
    grąžina: (FolderResponse) – sukurto aplanko duomenys
    iškelia: 404 – tėvinis aplankas nerastas
    """
    # Jei nurodytas tėvinis aplankas – patikriname ar egzistuoja
    if payload.parent_id is not None:
        parent = db.query(Folder).filter(
            Folder.id == payload.parent_id,
            Folder.user_id == current_user.id,
            Folder.is_deleted == False,  # noqa: E712
        ).first()

        if not parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tėvinis aplankas #{payload.parent_id} nerastas.",
            )

    now = datetime.now(timezone.utc)

    new_folder = Folder(
        user_id=current_user.id,
        parent_id=payload.parent_id,
        name=payload.name,
        color=payload.color,
        created_at=now,
        updated_at=now,
    )

    db.add(new_folder)
    db.commit()
    db.refresh(new_folder)

    logger.info(
        f"Aplankas sukurtas: '{new_folder.name}' (ID={new_folder.id}) | "
        f"Vartotojas: {current_user.username}"
    )

    return _folder_to_response(new_folder, db)


# ============================================
# GET /api/folders
# ============================================

@router.get(
    "",
    response_model=list[FolderResponse],
    summary="Aplankų sąrašas",
)
def list_folders(
    parent_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> list[FolderResponse]:
    """
    gauna: parent_id (int|None) – filtruoti pagal tėvinį aplanką
                                   (None = root lygio aplankalai)
           current_user         – prisijungęs vartotojas
           db                   – DB sesija
    daro: grąžina vartotojo aplankų sąrašą nurodyto lygio.
          Neįtraukia ištrintų aplankų.
          file_count gaunamas vienu agreguotu query (apsauga nuo N+1).
    grąžina: (list[FolderResponse]) – aplankų sąrašas
    """
    folders = db.query(Folder).filter(
        Folder.user_id == current_user.id,
        Folder.is_deleted == False,  # noqa: E712
        Folder.parent_id == parent_id,
    ).order_by(Folder.name).all()

    if not folders:
        return []

    # Apsauga nuo N+1: vienu query gauname failų skaičius VISIEMS šiems aplankams
    folder_ids = [f.id for f in folders]
    count_rows = db.query(
        FileModel.folder_id,
        func.count(FileModel.id).label("cnt"),
    ).filter(
        FileModel.folder_id.in_(folder_ids),
        FileModel.is_deleted == False,  # noqa: E712
    ).group_by(FileModel.folder_id).all()

    file_counts: dict[int, int] = {row.folder_id: row.cnt for row in count_rows}

    # Sudarome FolderResponse su iš anksto apskaičiuotais file_count
    return [
        FolderResponse(
            id=f.id,
            user_id=f.user_id,
            parent_id=f.parent_id,
            name=f.name,
            color=f.color,
            created_at=f.created_at,
            updated_at=f.updated_at,
            is_deleted=f.is_deleted,
            deleted_at=f.deleted_at,
            file_count=file_counts.get(f.id, 0),
        )
        for f in folders
    ]


# ============================================
# GET /api/folders/tree
# ============================================

@router.get(
    "/tree",
    response_model=list[FolderTreeResponse],
    summary="Aplankų medžio struktūra (sidebar'ui)",
)
def get_folder_tree(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> list[FolderTreeResponse]:
    """
    gauna: current_user – prisijungęs vartotojas
           db           – DB sesija
    daro: grąžina VISĄ aplankų medį vienu JSON objektu.
          Root lygio aplankalai su įdėtais children[] rekursyviai.
          Frontend sidebar'as naudoja šį endpoint'ą vienkartiniam
          visos struktūros nuskaitymui.
    grąžina: (list[FolderTreeResponse]) – root lygio aplankalai su children
    """
    # Gauname VISUS vartotojo aplankus (neištrintus) vienu query
    all_folders = db.query(Folder).filter(
        Folder.user_id == current_user.id,
        Folder.is_deleted == False,  # noqa: E712
    ).order_by(Folder.name).all()

    # Gauname failų kiekius visiems aplankams vienu query
    # (efektyviau nei N atskirų COUNT query'ų)
    count_rows = db.query(
        FileModel.folder_id,
        func.count(FileModel.id).label("cnt"),
    ).filter(
        FileModel.user_id == current_user.id,
        FileModel.is_deleted == False,  # noqa: E712
        FileModel.folder_id.isnot(None),
    ).group_by(FileModel.folder_id).all()

    file_counts: dict[int, int] = {row.folder_id: row.cnt for row in count_rows}

    # Surandame root lygio aplankus (parent_id = None)
    root_folders = [f for f in all_folders if f.parent_id is None]

    # Sukuriame žodyną {id: Folder} greitai vaikinių aplankalų paieškai
    # PASTABA: SQLAlchemy children ryšys užkraunamas lazy – mums to pakanka
    return [_build_tree(folder, file_counts) for folder in root_folders]


# ============================================
# GET /api/folders/{id}
# ============================================

@router.get(
    "/{folder_id}",
    response_model=FolderResponse,
    summary="Vieno aplanko duomenys",
)
def get_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FolderResponse:
    """
    gauna: folder_id   (int) – aplanko ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: grąžina vieno aplanko duomenis.
    grąžina: (FolderResponse) – aplanko duomenys su file_count
    iškelia: 404 – aplankas nerastas
    """
    folder = _get_folder_or_404(folder_id, current_user, db)
    return _folder_to_response(folder, db)


# ============================================
# PATCH /api/folders/{id}
# ============================================

@router.patch(
    "/{folder_id}",
    response_model=FolderResponse,
    summary="Pervadinti arba pakeisti spalvą",
)
def update_folder(
    folder_id: int,
    payload: FolderUpdate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FolderResponse:
    """
    gauna: folder_id   (int)          – aplanko ID iš URL
           payload     (FolderUpdate) – { name?, color? }
           current_user               – prisijungęs vartotojas
           db                         – DB sesija
    daro: atnaujina aplanko pavadinimą ir/arba spalvą.
          Visi laukai optional – siunčiami tik keičiami.
    grąžina: (FolderResponse) – atnaujinto aplanko duomenys
    iškelia: 404 – aplankas nerastas
             400 – nieko nenurodyta keisti
    """
    folder = _get_folder_or_404(folder_id, current_user, db)

    # Tikriname ar bent vienas laukas nurodytas
    if payload.name is None and payload.color is None and payload.parent_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nurodykite bent vieną lauką: name, color arba parent_id.",
        )

    if payload.name is not None:
        folder.name = payload.name

    if payload.color is not None:
        folder.color = payload.color

    # ----------------------------------------
    # Perkėlimas į kitą tėvinį aplanką (parent_id)
    # parent_id <= 0  -> į šakninį (None)
    # parent_id  > 0  -> į nurodytą aplanką (su validacija + ciklo apsauga)
    # ----------------------------------------
    if payload.parent_id is not None:
        if payload.parent_id <= 0:
            folder.parent_id = None
        else:
            target_id = payload.parent_id

            # Negalima perkelti aplanko į jį patį
            if target_id == folder.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Aplanko negalima perkelti į jį patį.",
                )

            # Tikslinis aplankas turi egzistuoti ir priklausyti vartotojui
            target = db.query(Folder).filter(
                Folder.id == target_id,
                Folder.user_id == current_user.id,
                Folder.is_deleted == False,  # noqa: E712
            ).first()
            if not target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Tikslinis aplankas #{target_id} nerastas.",
                )

            # Ciklo apsauga: tikslinis aplankas negali būti šio aplanko
            # palikuonis (kitaip susidarytų begalinis ciklas). Einame nuo
            # target'o aukštyn iki šaknies – jei sutinkame folder.id, draudžiam.
            ancestor = target
            while ancestor is not None:
                if ancestor.id == folder.id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Negalima perkelti aplanko į jo paties paaplankį.",
                    )
                ancestor = (
                    db.query(Folder).filter(Folder.id == ancestor.parent_id).first()
                    if ancestor.parent_id is not None
                    else None
                )

            folder.parent_id = target_id

    # Atnaujiname updated_at rankiniu būdu (SQLAlchemy neatnaujina automatiškai)
    folder.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(folder)

    logger.info(
        f"Aplankas atnaujintas: ID={folder_id} | "
        f"Vartotojas: {current_user.username}"
    )

    return _folder_to_response(folder, db)


# ============================================
# DELETE /api/folders/{id}  (soft delete → šiukšlinė)
# ============================================

@router.delete(
    "/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Perkelti aplanką į šiukšlinę",
)
def delete_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    gauna: folder_id   (int) – aplanko ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: perkelia aplanką į šiukšlinę (soft delete).
          Aplankas pažymimas is_deleted=True, bet LIEKA DB ir diske.
          Vaikiniai aplankalai ir failai NEKEIČIAMI – jie tampa "paslėpti"
          nes tėvinis aplankas ištrintas.
          Galutinis trynimas – per DELETE /api/trash/{id}.
    grąžina: 204 No Content
    iškelia: 404 – aplankas nerastas
    """
    folder = _get_folder_or_404(folder_id, current_user, db)

    folder.soft_delete()
    db.commit()

    logger.info(
        f"Aplankas perkeltas į šiukšlinę: '{folder.name}' (ID={folder_id}) | "
        f"Vartotojas: {current_user.username}"
    )


# ============================================
# POST /api/folders/{id}/restore
# ============================================

@router.post(
    "/{folder_id}/restore",
    response_model=FolderResponse,
    summary="Atkurti aplanką iš šiukšlinės",
)
def restore_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> FolderResponse:
    """
    gauna: folder_id   (int) – aplanko ID iš URL
           current_user      – prisijungęs vartotojas
           db                – DB sesija
    daro: atkuria aplanką iš šiukšlinės.
          Jei tėvinis aplankas jau ištrintas – perkeliamas į root lygį.
    grąžina: (FolderResponse) – atkurto aplanko duomenys
    iškelia: 404 – aplankas nerastas šiukšlinėje
             400 – aplankas nėra šiukšlinėje
    """
    folder = _get_folder_or_404(folder_id, current_user, db, allow_deleted=True)

    if not folder.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aplankas nėra šiukšlinėje.",
        )

    # Jei tėvinis aplankas ištrintas – grąžiname į root
    if folder.parent_id is not None:
        parent = db.query(Folder).filter(
            Folder.id == folder.parent_id,
            Folder.is_deleted == False,  # noqa: E712
        ).first()

        if not parent:
            folder.parent_id = None
            logger.info(
                f"Aplanko '{folder.name}' tėvinis aplankas ištrintas – "
                f"grąžinamas į root."
            )

    folder.restore()
    db.commit()
    db.refresh(folder)

    logger.info(
        f"Aplankas atkurtas iš šiukšlinės: '{folder.name}' (ID={folder_id}) | "
        f"Vartotojas: {current_user.username}"
    )

    return _folder_to_response(folder, db)
