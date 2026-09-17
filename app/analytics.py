from sqlalchemy.orm import Session

from app.models import ProductEvent

EVENT_TYPES = ("view", "enquiry", "add_to_cart")


def log_event(db: Session, product_id: int, event_type: str) -> None:
    """Fire-and-forget engagement counter — never raises, since a failed
    analytics write must never break the page/action the customer is
    actually trying to use."""
    if event_type not in EVENT_TYPES:
        return
    try:
        db.add(ProductEvent(product_id=product_id, event_type=event_type))
        db.commit()
    except Exception:
        db.rollback()
