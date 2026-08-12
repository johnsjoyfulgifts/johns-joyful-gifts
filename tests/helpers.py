from app.cart_service import serialize_cart
from app.cart_service import CART_COOKIE_NAME
from app.models import Category, Product


def make_product(db, name="Test Product", price=100.0, stock=5, active=True, **kwargs):
    from app.utils.slugs import unique_slug

    product = Product(
        name=name,
        slug=unique_slug(db, Product, name),
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
    payload = {
        "full_name": "Test Customer",
        "mobile": "9876543210",
        "address": "123 Test Street",
        "city": "Chennai",
        "state": "Tamil Nadu",
        "pincode": "600001",
        "idempotency_key": idempotency_key,
    }
    payload.update(overrides)
    return payload
