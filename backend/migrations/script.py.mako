"""${message}

Revizija:    ${up_revision}
Sukurta:     ${create_date}
Priklausomybė: ${down_revision | comma,n}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# Perrašymo (downgrade) informacija
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Migracija pirmyn – taiko pakeitimus."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Migracija atgal – atšaukia pakeitimus."""
    ${downgrades if downgrades else "pass"}
