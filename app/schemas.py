import re

from pydantic import BaseModel, field_validator

MOBILE_RE = re.compile(r"^[0-9+][0-9\-\s]{7,17}$")
PINCODE_RE = re.compile(r"^[0-9]{4,10}$")


class CheckoutRequest(BaseModel):
    full_name: str
    mobile: str
    address: str
    city: str
    state: str
    pincode: str
    email: str | None = None
    delivery_instructions: str | None = None
    idempotency_key: str
    payment_method: str = "cod"

    @field_validator("full_name", "address", "city", "state")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
        return value

    @field_validator("mobile")
    @classmethod
    def valid_mobile(cls, value: str) -> str:
        value = value.strip()
        if not MOBILE_RE.match(value):
            raise ValueError("Please enter a valid mobile number.")
        return value

    @field_validator("pincode")
    @classmethod
    def valid_pincode(cls, value: str) -> str:
        value = value.strip()
        if not PINCODE_RE.match(value):
            raise ValueError("Please enter a valid pincode.")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        value = value.strip()
        if not (8 <= len(value) <= 80):
            raise ValueError("Invalid request.")
        return value

    @field_validator("payment_method")
    @classmethod
    def valid_payment_method(cls, value: str) -> str:
        if value not in ("cod", "online"):
            raise ValueError("Invalid payment method.")
        return value


class VerifyPaymentRequest(BaseModel):
    order_number: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class TrackOrderRequest(BaseModel):
    order_number: str
    mobile: str
