from app.cart_service import serialize_cart
from app.cart_service import CART_COOKIE_NAME
from app.models import Category, Customer, Product


def ensure_manual_payment(db):
    """UPI/Bank Transfer is the only checkout payment method now that Cash on
    Delivery has been removed, so /api/checkout refuses to place an order
    unless it's configured. Tests that aren't specifically exercising that
    configuration state (payment-method tests live in test_manual_payment.py)
    need this called first so their checkout calls succeed."""
    from app.settings_service import set_settings

    set_settings(db, {"manual_payment_enabled": "true", "upi_id": "teststore@okhdfcbank"})


def make_customer(db, mobile="9000000001", name="Test Customer", password="testpass123"):
    from app.auth import hash_password

    customer = Customer(name=name, mobile=mobile, password_hash=hash_password(password))
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def customer_session_cookie(customer_id: int) -> dict:
    from app.customer_auth import SESSION_COOKIE_NAME, create_session_token

    return {SESSION_COOKIE_NAME: create_session_token(customer_id)}


def checkout_cookies(items: dict, customer_id: int) -> dict:
    """Cart cookie + logged-in customer session cookie, merged for one request."""
    cookies = cart_cookie_header(items)
    cookies.update(customer_session_cookie(customer_id))
    return cookies


def make_product(db, name="Test Product", price=100.0, stock=5, active=True, base_price=None, gst_percent=0, **kwargs):
    from app.utils.slugs import unique_slug

    # Mirrors how existing pre-GST products were migrated: base_price ==
    # price at 0% GST unless a test explicitly wants a GST-priced product.
    product = Product(
        name=name,
        slug=unique_slug(db, Product, name),
        base_price=base_price if base_price is not None else price,
        gst_percent=gst_percent,
        price=price,
        stock=stock,
        active=active,
        **kwargs,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def make_category(db, name="Test Category"):
    from app.utils.slugs import unique_slug

    category = Category(name=name, slug=unique_slug(db, Category, name), active=True)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def cart_cookie_header(items: dict) -> dict:
    """items: {product_id: quantity} -> Cookie header dict for httpx/TestClient."""
    raw = {str(pid): qty for pid, qty in items.items()}
    return {CART_COOKIE_NAME: serialize_cart(raw)}


def checkout_payload(idempotency_key: str, **overrides) -> dict:
    """Name/mobile are no longer part of checkout — they come from the
    logged-in customer's account (see checkout_cookies / make_customer)."""
    payload = {
        "address": "123 Test Street",
        "city": "Chennai",
        "state": "Tamil Nadu",
        "pincode": "600001",
        "idempotency_key": idempotency_key,
    }
    payload.update(overrides)
    return payload
