"""add sku unique index and backfill existing product skus

Revision ID: 86207b2e4396
Revises: b4fbd5174aa4
Create Date: 2026-08-15 03:39:53.276413

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '86207b2e4396'
down_revision: Union[str, None] = 'b4fbd5174aa4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill first so every existing product has a SKU before the unique
    # index goes on — new rows use JJG-<id> which can never collide with a
    # hand-entered SKU of that same shape since ids are already unique.
    op.execute("UPDATE products SET sku = 'JJG-' || lpad(id::text, 4, '0') WHERE sku IS NULL")

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_products_sku'), ['sku'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_sku'))
