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
    "announcement_enabled": "false",
    "announcement_text": "",
    "last_backup_at": "",
    "low_stock_threshold": "5",
    "whatsapp_product_template": "",
    "whatsapp_cart_template": "",
    "whatsapp_quotation_template": "",
    "gst_number": "",
    "default_gst_rate": "18",
    "site_language": "en",
    "manual_payment_enabled": "false",
    "upi_id": "",
    "bank_account_name": "",
    "bank_account_number": "",
    "bank_ifsc": "",
    "bank_name": "",
}


def manual_payment_available(db: Session) -> bool:
    """True only when the admin has turned it on AND actually filled in a
    UPI ID or bank details — mirrors the pattern used for the (since
    removed) Razorpay toggle, so an empty configuration never shows a
    broken payment option at checkout."""
    values = get_all_settings(db)
    if values.get("manual_payment_enabled") != "true":
        return False
    has_upi = bool(values.get("upi_id", "").strip())
    has_bank = bool(values.get("bank_account_number", "").strip())
    return has_upi or has_bank


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
