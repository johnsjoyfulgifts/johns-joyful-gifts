"""
Store-configurable settings (delivery charge, WhatsApp number, Instagram link, etc.)
live in the `settings` key/value table so the owner can change them from /admin
without redeploying. Each key falls back to the .env-driven default the first
time it's read, and the DB is only written to when the admin actually saves.
"""

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Setting

_DEFAULTS_SOURCE = get_settings()

DEFAULTS: dict[str, str] = {
    "store_name": _DEFAULTS_SOURCE.store_name,
    "store_tagline": _DEFAULTS_SOURCE.store_tagline,
    "whatsapp_number": _DEFAULTS_SOURCE.whatsapp_number,
    "instagram_url": _DEFAULTS_SOURCE.instagram_url,
    "delivery_mode": "flat",  # flat | free | disabled
    "flat_delivery_charge": str(_DEFAULTS_SOURCE.default_delivery_charge),
    "free_delivery_threshold": str(_DEFAULTS_SOURCE.free_delivery_threshold),
    "about_text": "We're a small family-run gift shop. Details coming soon.",
    "contact_email": "",
    "contact_address": "",
}


def get_all_settings(db: Session) -> dict[str, str]:
    rows = db.query(Setting).all()
    values = {row.key: row.value for row in rows}
    return {**DEFAULTS, **values}


def get_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, "")


def set_settings(db: Session, updates: dict[str, str]) -> None:
    for key, value in updates.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.commit()


def compute_delivery_charge(db: Session, subtotal: float) -> float:
    values = get_all_settings(db)
    mode = values.get("delivery_mode", "flat")
    if mode == "disabled":
        return 0.0
    if mode == "free":
        return 0.0
    flat = float(values.get("flat_delivery_charge") or 0)
    threshold = float(values.get("free_delivery_threshold") or 0)
    if threshold > 0 and subtotal >= threshold:
        return 0.0
    return flat
