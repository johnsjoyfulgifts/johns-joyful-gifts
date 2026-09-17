"""add product image thumbnail url

Revision ID: 9fb186ac397e
Revises: 86207b2e4396
Create Date: 2026-08-15 08:36:23.128645

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9fb186ac397e'
down_revision: Union[str, None] = '86207b2e4396'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('product_images', schema=None) as batch_op:
        batch_op.add_column(sa.Column('thumbnail_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('product_images', schema=None) as batch_op:
        batch_op.drop_column('thumbnail_url')
