from datetime import datetime, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import Order

PREFIX = "JJG"


def generate_order_number(db: Session) -> str:
    """
    JJG-YYYYMMDD-#### where #### is a per-day sequence.

    The count-then-format below is only race-free if concurrent checkouts
    can't interleave it, but the checkout transaction's row locks are on
    Product rows (see checkout.py), not on Order — two orders for different
    products could otherwise count the same "today" total and mint the same
    number. pg_advisory_xact_lock serializes just this step, keyed by date,
    and releases automatically at commit/rollback.
    """
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    db.execute(text("SELECT pg_advisory_xact_lock(:key1, hashtext(:date_part))"), {"key1": 1, "date_part": date_part})
    like_pattern = f"{PREFIX}-{date_part}-%"
    count_today = db.query(func.count(Order.id)).filter(Order.order_number.like(like_pattern)).scalar() or 0
    sequence = count_today + 1
    return f"{PREFIX}-{date_part}-{sequence:04d}"
