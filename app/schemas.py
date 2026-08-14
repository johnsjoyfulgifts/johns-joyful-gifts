import re

from pydantic import BaseModel, field_validator

MOBILE_RE = re.compile(r"^[0-9+][0-9\-\s]{7,17}$")
PINCODE_RE = re.compile(r"^[0-9]{4,10}$")


class CheckoutRequest(BaseModel):
    """Name/mobile are NOT collected here — checkout requires a logged-in
    customer, so those come from the account (Customer.name/mobile)."""

    address: str
    city: str
    state: str
    pincode: str
    delivery_instructions: str | None = None
    idempotency_key: str
    payment_method: str = "cod"
    gift_wrap: bool = False
    gift_message: str | None = None

    @field_validator("address", "city", "state")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
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
        if value not in ("cod", "manual"):
            raise ValueError("Invalid payment method.")
        return value

    @field_validator("gift_message")
    @classmethod
    def valid_gift_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 300:
            raise ValueError("Gift message must be 300 characters or fewer.")
        return value or None


class RegisterRequest(BaseModel):
    name: str
    mobile: str
    password: str
    email: str | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Please enter your name.")
        return value

    @field_validator("mobile")
    @classmethod
    def valid_mobile(cls, value: str) -> str:
        value = value.strip()
        if not MOBILE_RE.match(value):
            raise ValueError("Please enter a valid mobile number.")
        return value

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value


class TrackOrderRequest(BaseModel):
    order_number: str
    mobile: str
