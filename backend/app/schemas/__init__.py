"""
Pydantic schemos – validacijos ir serializacijos paketas.

Schemos naudojamos dviem tikslais:
    1. REQUEST  – vartotojo atsiųstų duomenų validacija (pvz. LoginRequest)
    2. RESPONSE – ką grąžiname vartotojui (pvz. UserResponse)

SKIRTUMAS nuo modelių (app/models/):
    - Modeliai   → SQLAlchemy → atitinka DB lenteles
    - Schemos    → Pydantic   → atitinka API JSON struktūrą
    Jie NESUTAMPA – pvz. UserResponse NETURI password_hash lauko.

IMPORTAVIMAS:
    from app.schemas import UserCreate, UserResponse
    # arba atskirai:
    from app.schemas.auth import LoginRequest
"""

# ============================================
# AUTH SCHEMOS
# ============================================
from app.schemas.auth import (
    LoginRequest,
    TempTokenResponse,
    TokenResponse,
    TwoFARequest,
)

# ============================================
# USER SCHEMOS
# ============================================
from app.schemas.user import (
    AdminUserCreate,
    UserResponse,
    UserStatsResponse,
    UserUpdate,
)

# ============================================
# FOLDER SCHEMOS
# ============================================
from app.schemas.folder import (
    FolderCreate,
    FolderResponse,
    FolderTreeResponse,
    FolderUpdate,
)

# ============================================
# FILE SCHEMOS
# ============================================
from app.schemas.file import (
    FileResponse,
    FileUpdate,
    FileUploadResponse,
    ShareLinkCreate,
    ShareLinkResponse,
    TrashItemResponse,
)

# ============================================
# VIEŠAS API
# ============================================
__all__ = [
    # Auth
    "LoginRequest",
    "TwoFARequest",
    "TempTokenResponse",
    "TokenResponse",
    # User
    "AdminUserCreate",
    "UserResponse",
    "UserUpdate",
    "UserStatsResponse",
    # Folder
    "FolderCreate",
    "FolderResponse",
    "FolderUpdate",
    "FolderTreeResponse",
    # File
    "FileResponse",
    "FileUpdate",
    "FileUploadResponse",
    "ShareLinkCreate",
    "ShareLinkResponse",
    "TrashItemResponse",
]
