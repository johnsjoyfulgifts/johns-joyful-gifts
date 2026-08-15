from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session


def generate_document_number(db: Session, model, column, prefix: str) -> str:
    """PREFIX-YYYYMMDD-#### with a per-day sequence. Quotations/invoices are
    created one at a time by a logged-in admin (not the high-concurrency
    public checkout path), so a simple count-then-format is safe without
    the advisory-lock machinery generate_order_number needs."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    like_pattern = f"{prefix}-{date_part}-%"
    count_today = db.query(func.count()).select_from(model).filter(column.like(like_pattern)).scalar() or 0
    sequence = count_today + 1
    return f"{prefix}-{date_part}-{sequence:04d}"
