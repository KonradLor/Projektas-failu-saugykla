"""
SQLAlchemy ORM modelių paketas.

Šis __init__.py importuoja VISUS modelius, kad SQLAlchemy juos "matytų"
kai kviečiamas Base.metadata.create_all() arba Alembic.

SVARBU: Jei pridedi naują modelį, privaloma jį čia importuoti!
Kitaip init_db() nesukurs naujos lentelės.

Importavimas iš kitur:
    from app.models import User, Folder, File, ShareLink, Session
    # arba atskirai:
    from app.models.user import User
"""

# ============================================
# MODELIŲ IMPORTAI
# ============================================

# Importuojami visi modeliai - šitie importai PRIVALOMI
# (net jei IDE rodo "unused import" įspėjimą - jie registruoja modelius SQLAlchemy)
from app.models.file import File
from app.models.folder import Folder
from app.models.session import Session
from app.models.share_link import ShareLink
from app.models.user import User

# ============================================
# VIEŠAS API
# ============================================

# Nurodo ką galima importuoti su: from app.models import *
__all__ = [
    "User",
    "Folder",
    "File",
    "ShareLink",
    "Session",
]
