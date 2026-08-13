"""
Thin wrapper around the Razorpay SDK. Kept in one module so the rest of the
app never touches the SDK or the secret key directly — routes only ever call
create_razorpay_order() and verify_payment_signature() below.
"""

import warnings

# The razorpay SDK (last updated for older setuptools) imports pkg_resources
# internally and triggers this on every import. Harmless and out of our
# control — pinned via setuptools<81 in requirements.txt — silenced here so
# it doesn't clutter server startup logs.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
    import razorpay

from app.config import get_settings

settings = get_settings()


def _client() -> razorpay.Client:
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


def create_razorpay_order(order_number: str, amount_rupees: float) -> dict:
    """Amount must be in paise (smallest currency unit) per Razorpay's API."""
    amount_paise = int(round(amount_rupees * 100))
    return _client().order.create(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": order_number,
            "payment_capture": 1,
        }
    )


def verify_payment_signature(razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    """
    The one security-critical function in this module: confirms the payment
    confirmation actually came from Razorpay (HMAC-signed with our secret
    key) rather than being a client claiming success without having paid.
    """
    try:
        _client().utility.verify_payment_signature(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
        return True
    except razorpay.errors.SignatureVerificationError:
        return False
