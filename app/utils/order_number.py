from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Order

PREFIX = "JJG"


def generate_order_number(db: Session) -> str:
    """
    JJG-YYYYMMDD-#### where #### is a per-day sequence.
    Called inside the same BEGIN IMMEDIATE transaction as order creation, so the
    count-then-insert is race-free (SQLite serializes writers at BEGIN IMMEDIATE).
    """
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    like_pattern = f"{PREFIX}-{date_part}-%"
    count_today = db.query(func.count(Order.id)).filter(Order.order_number.like(like_pattern)).scalar() or 0
    sequence = count_today + 1
    return f"{PREFIX}-{date_part}-{sequence:04d}"
