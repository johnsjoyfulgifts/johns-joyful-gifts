"""add per-product GST pricing (base_price, gst_percent) and order item GST snapshot

Revision ID: 08c4a24ae87e
Revises: c2e9cbdcaf14
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08c4a24ae87e'
down_revision: Union[str, None] = 'c2e9cbdcaf14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # products.price already holds the final (GST-inclusive) selling price
    # and stays that way — nothing here touches it, so no existing customer
    # price changes. base_price is the new "actual price before GST" entry
    # point; for rows that already exist it starts out equal to price at 0%
    # GST (added as nullable so the backfill below can run, then locked to
    # NOT NULL once every row has a value).
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('base_price', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('gst_percent', sa.Float(), nullable=False, server_default='0'))

    op.execute("UPDATE products SET base_price = price WHERE base_price IS NULL")

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.alter_column('base_price', existing_type=sa.Float(), nullable=False)

    # order_items.price_snapshot already holds the final unit price charged
    # (unchanged) — these are the additional GST breakdown fields captured
    # at checkout so a product's GST/price changing later never rewrites a
    # past order's numbers. Existing order rows had no GST, so they backfill
    # to base_price_snapshot = price_snapshot, gst_percent/amount = 0.
    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('base_price_snapshot', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('gst_percent_snapshot', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('gst_amount_snapshot', sa.Float(), nullable=False, server_default='0'))

    op.execute("UPDATE order_items SET base_price_snapshot = price_snapshot WHERE base_price_snapshot IS NULL")

    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.alter_column('base_price_snapshot', existing_type=sa.Float(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.drop_column('gst_amount_snapshot')
        batch_op.drop_column('gst_percent_snapshot')
        batch_op.drop_column('base_price_snapshot')

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_column('gst_percent')
        batch_op.drop_column('base_price')
