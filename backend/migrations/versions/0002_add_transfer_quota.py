"""Mėnesinio duomenų srauto (transfer) kvotos stebėjimo laukai

Revizija:    0002
Sukurta:     2026-05-15
Priklausomybė: 0001

Pridedami laukai prie users lentelės:
    - transfer_used_bytes      → kiek baitų vartotojas perdavė per einamąjį mėnesį
    - transfer_period_start    → kurio mėnesio 1-osios dienos data (UTC)

Mėnesio pasikeitimo metu (transfer_period_start != einamasis mėnuo)
vartotojo programinis kodas automatiškai nulina counter'į ir atnaujina
periodo pradžią.

Tikslas: 20 GB per mėnesį limitas, kad apsaugotume serverį nuo
nekontroliuojamo srauto (atsisiuntimai, įkėlimai, share atsisiuntimai).
"""

# ============================================
# IMPORTAI
# ============================================
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# ============================================
# REVIZIJOS DUOMENYS
# ============================================

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================
# UPGRADE
# ============================================

def upgrade() -> None:
    """Pridedam transfer kvotos laukus."""

    # Pridedam 2 naujus stulpelius prie users lentelės
    # batch_alter_table - reikalingas SQLite, nes natural ALTER TABLE ribotas
    with op.batch_alter_table("users") as batch_op:
        # Baitų skaitiklis šio mėnesio sraute
        batch_op.add_column(
            sa.Column(
                "transfer_used_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
                comment="Šio mėnesio perduoti baitai (upload + download + share)",
            )
        )
        # Periodo pradžia (pirma mėnesio diena)
        # server_default - dabartinė data, kad esamiems įrašams iškart
        # nustatytume šios dienos vertę (jie pradeda nuo dabar)
        batch_op.add_column(
            sa.Column(
                "transfer_period_start",
                sa.Date(),
                nullable=False,
                server_default=sa.func.current_date(),
                comment="Einamojo periodo pradžios data (UTC, mėnesio 1-oji)",
            )
        )


# ============================================
# DOWNGRADE
# ============================================

def downgrade() -> None:
    """Pašalinam transfer kvotos laukus (jei reikia atstatyti į 0001)."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("transfer_period_start")
        batch_op.drop_column("transfer_used_bytes")
