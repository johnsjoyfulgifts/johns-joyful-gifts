"""
Manual, admin-only data export — a logical (JSON) backup rather than a raw
pg_dump, since the Render runtime doesn't ship Postgres client binaries.
Restoring from this means re-inserting rows via a script, not `psql < file`;
good enough for disaster recovery on a small shop's dataset, not meant to
replace real point-in-time recovery (see README for what Supabase's free
tier actually provides).
"""

from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import (
    Admin,
    Category,
    Coupon,
    Customer,
    Order,
    OrderItem,
    OrderStatusHistory,
    Product,
    ProductImage,
    Review,
    Setting,
    Wishlist,
)

BACKUP_TABLES = [
    ("categories", Category),
    ("products", Product),
    ("product_images", ProductImage),
    ("customers", Customer),
    ("orders", Order),
    ("order_items", OrderItem),
    ("order_status_history", OrderStatusHistory),
    ("coupons", Coupon),
    ("reviews", Review),
    ("wishlist_items", Wishlist),
    ("admins", Admin),
    ("settings", Setting),
]


def _row_to_dict(obj) -> dict:
    result = {}
    for col in inspect(obj).mapper.column_attrs:
        value = getattr(obj, col.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[col.key] = value
    return result


def build_backup(db: Session) -> dict:
    data = {
        "app": "John's Joyful Gifts",
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    for key, model in BACKUP_TABLES:
        rows = db.query(model).all()
        data[key] = [_row_to_dict(row) for row in rows]
    return data
