"""normalize existing customer mobile numbers to 10 digits

Revision ID: de69f0cd6a98
Revises: 1854e3313581
Create Date: 2026-08-15 21:06:44.500517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de69f0cd6a98'
down_revision: Union[str, None] = '1854e3313581'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Matches app.schemas.normalize_mobile(): strip to digits, keep the last
    # 10. Fixes accounts stored as e.g. '+917449111705' so they match a
    # customer typing '7449111705' on a later login or order-tracking
    # lookup — same fix, applied once to existing rows. Only touches rows
    # that aren't already a clean 10-digit number.
    op.execute(
        "UPDATE customers "
        "SET mobile = RIGHT(regexp_replace(mobile, '[^0-9]', '', 'g'), 10) "
        "WHERE mobile !~ '^[0-9]{10}$'"
    )


def downgrade() -> None:
    # Not reversible: the original formatting (whether +91 was present, a
    # leading 0, dashes/spaces) isn't recoverable once stripped.
    pass
