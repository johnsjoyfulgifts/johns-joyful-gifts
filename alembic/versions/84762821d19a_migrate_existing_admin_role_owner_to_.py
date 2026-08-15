"""migrate existing admin role owner to super_admin

Revision ID: 84762821d19a
Revises: bd47b98236b3
Create Date: 2026-08-15 10:17:55.755873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84762821d19a'
down_revision: Union[str, None] = 'bd47b98236b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The pre-multi-role default was 'owner' — every existing admin becomes
    # Super Admin (the new highest tier), so nobody's access changes.
    op.execute("UPDATE admins SET role = 'super_admin' WHERE role = 'owner' OR role IS NULL OR role = ''")


def downgrade() -> None:
    op.execute("UPDATE admins SET role = 'owner' WHERE role = 'super_admin'")
