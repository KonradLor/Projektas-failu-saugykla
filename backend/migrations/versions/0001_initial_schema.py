"""Pradinė DB schema – visos lentelės

Revizija:    0001
Sukurta:     2026-05-07
Priklausomybė: None

Sukuria lenteles:
    - users       → vartotojai (auth, šifravimo raktai, storage)
    - folders     → aplankalai (medžio struktūra, spalvos, trash)
    - files       → failų metaduomenys (šifravimo info, hash)
    - share_links → viešo dalinimosi nuorodos
    - sessions    → aktyvios prisijungimo sesijos
"""

# ============================================
# IMPORTAI
# ============================================
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# ============================================
# REVIZIJOS DUOMENYS
# ============================================

revision: str = "0001"
down_revision: Union[str, None] = None      # Pirmoji migracija – nėra priklausomybių
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================
# UPGRADE – LENTELIŲ KŪRIMAS
# ============================================

def upgrade() -> None:
    """
    gauna: nieko
    daro: sukuria visas KonradVault lenteles pagal planą
    grąžina: None
    """

    # ----------------------------------------
    # LENTELĖ: users
    # ----------------------------------------
    op.create_table(
        "users",

        # Pirminis raktas
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),

        # Autentifikacijos laukai
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("totp_secret", sa.String(64), nullable=False),

        # Šifravimo raktas (BLOB – baitai)
        sa.Column("encryption_key_encrypted", sa.LargeBinary(), nullable=True),

        # Rolė ir statusas
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),

        # Disko vietos stebėjimas (2GB limito kontrolei)
        sa.Column("storage_used_bytes", sa.BigInteger(), nullable=False, server_default="0"),

        # Laiko žymės
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),

        # Apribojimai
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    # Indeksas greitesnei paieškai pagal username (login metu)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ----------------------------------------
    # LENTELĖ: folders
    # ----------------------------------------
    op.create_table(
        "folders",

        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),

        # Priklausomybės
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),

        # Aplanko duomenys
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("color", sa.String(7), nullable=False, server_default="#3B82F6"),

        # Laiko žymės
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),

        # Soft delete
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),

        # Apribojimai ir FK
        sa.ForeignKeyConstraint(["parent_id"], ["folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_folders_user_id", "folders", ["user_id"])
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])
    op.create_index("ix_folders_is_deleted", "folders", ["is_deleted"])

    # ----------------------------------------
    # LENTELĖ: files
    # ----------------------------------------
    op.create_table(
        "files",

        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),

        # Priklausomybės
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),

        # Failų pavadinimai
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("stored_filename", sa.String(36), nullable=False),   # UUID

        # Metaduomenys
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),

        # Šifravimo duomenys
        sa.Column("encryption_iv", sa.LargeBinary(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),          # SHA-256

        # Laiko žymė
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        # Soft delete
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),

        # Apribojimai ir FK
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_filename"),
    )

    op.create_index("ix_files_user_id", "files", ["user_id"])
    op.create_index("ix_files_folder_id", "files", ["folder_id"])
    op.create_index("ix_files_stored_filename", "files", ["stored_filename"], unique=True)
    op.create_index("ix_files_is_deleted", "files", ["is_deleted"])

    # ----------------------------------------
    # LENTELĖ: share_links
    # ----------------------------------------
    op.create_table(
        "share_links",

        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),

        # Priklausomybė
        sa.Column("file_id", sa.Integer(), nullable=False),

        # Share link duomenys
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_downloads", sa.Integer(), nullable=False),

        # Laiko žymė
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        # Statusas
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default="0"),

        # Apribojimai ir FK
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )

    op.create_index("ix_share_links_token", "share_links", ["token"], unique=True)
    op.create_index("ix_share_links_file_id", "share_links", ["file_id"])
    op.create_index("ix_share_links_is_disabled", "share_links", ["is_disabled"])

    # ----------------------------------------
    # LENTELĖ: sessions
    # ----------------------------------------
    op.create_table(
        "sessions",

        # Token kaip PRIMARY KEY (ne int id)
        sa.Column("token", sa.String(128), nullable=False),

        # Priklausomybė
        sa.Column("user_id", sa.Integer(), nullable=False),

        # Laiko žymės
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),

        # Saugumo informacija
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),

        # Apribojimai ir FK
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )

    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])


# ============================================
# DOWNGRADE – LENTELIŲ TRYNIMAS
# ============================================

def downgrade() -> None:
    """
    gauna: nieko
    daro: ištrina visas lenteles atvirkštine tvarka
          (pradedant nuo tų, kurios turi FK į kitas)
    grąžina: None
    """
    # Trynimo tvarka: pirma tos kurios turi FK, paskiausiai – bazinės
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_share_links_is_disabled", table_name="share_links")
    op.drop_index("ix_share_links_file_id", table_name="share_links")
    op.drop_index("ix_share_links_token", table_name="share_links")
    op.drop_table("share_links")

    op.drop_index("ix_files_is_deleted", table_name="files")
    op.drop_index("ix_files_stored_filename", table_name="files")
    op.drop_index("ix_files_folder_id", table_name="files")
    op.drop_index("ix_files_user_id", table_name="files")
    op.drop_table("files")

    op.drop_index("ix_folders_is_deleted", table_name="folders")
    op.drop_index("ix_folders_parent_id", table_name="folders")
    op.drop_index("ix_folders_user_id", table_name="folders")
    op.drop_table("folders")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
