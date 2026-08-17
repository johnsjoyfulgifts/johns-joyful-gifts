"""
Cart is stored client-side in a signed cookie. Each entry is either a bare
int (the legacy quantity-only format, from before personalization existed —
kept readable so this change never breaks a cart already sitting in a
customer's browser) or the current shape: {"qty": int, "p": dict | None},
where "p" holds whatever the customer entered in a product's personalization
panel (name/message/date/photo url). Nothing about price, stock, or
personalization options is trusted from the cookie: every read joins against
the live Product table, and checkout re-validates everything again inside
the order transaction.

Personalized lines are always quantity 1 — one personalization applies to
the whole line, so "3 of this mug" can never silently mean 3 different
names. A customer wanting multiple different personalizations of the same
product adds it to the cart again for each one.
"""

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product

settings = get_settings()
CART_COOKIE_NAME = "jjg_cart"
MAX_QTY_PER_ITEM = 20

_serializer = URLSafeSerializer(settings.secret_key, salt="cart")


def _normalize_entry(raw) -> dict:
    if isinstance(raw, dict):
        try:
            qty = int(raw.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        personalization = raw.get("p") if isinstance(raw.get("p"), dict) else None
        return {"qty": qty, "p": personalization}
    # Legacy shape: the cookie value was just the quantity itself.
    try:
        qty = int(raw)
    except (TypeError, ValueError):
        qty = 0
    return {"qty": qty, "p": None}


def serialize_cart(cart: dict[str, dict]) -> str:
    return _serializer.dumps(cart)


def read_cart(request) -> dict[str, dict]:
    token = request.cookies.get(CART_COOKIE_NAME)
    if not token:
        return {}
    try:
        data = _serializer.loads(token)
    except BadSignature:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict] = {}
    for product_id, raw in data.items():
        entry = _normalize_entry(raw)
        if entry["qty"] <= 0:
            continue
        max_qty = 1 if entry["p"] else MAX_QTY_PER_ITEM
        entry["qty"] = min(entry["qty"], max_qty)
        cleaned[str(product_id)] = entry
    return cleaned


def write_cart(response, cart: dict[str, dict]) -> None:
    token = _serializer.dumps(cart)
    response.set_cookie(
        key=CART_COOKIE_NAME,
        value=token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def add_item(request, response, product_id: int, quantity: int, personalization: dict | None = None) -> dict:
    cart = read_cart(request)
    key = str(product_id)
    if personalization:
        cart[key] = {"qty": 1, "p": personalization}
    else:
        current_qty = cart.get(key, {}).get("qty", 0)
        cart[key] = {"qty": min(current_qty + max(quantity, 1), MAX_QTY_PER_ITEM), "p": None}
    write_cart(response, cart)
    return cart


def set_item_quantity(request, response, product_id: int, quantity: int) -> dict:
    cart = read_cart(request)
    key = str(product_id)
    if quantity <= 0:
        cart.pop(key, None)
    else:
        existing = cart.get(key, {"qty": 0, "p": None})
        max_qty = 1 if existing.get("p") else MAX_QTY_PER_ITEM
        cart[key] = {"qty": min(quantity, max_qty), "p": existing.get("p")}
    write_cart(response, cart)
    return cart


def remove_item(request, response, product_id: int) -> dict:
    cart = read_cart(request)
    cart.pop(str(product_id), None)
    write_cart(response, cart)
    return cart


def clear_cart(response) -> None:
    response.delete_cookie(CART_COOKIE_NAME, path="/")


class CartLine:
    def __init__(self, product: Product, quantity: int, personalization: dict | None = None):
        self.product = product
        self.quantity = quantity
        self.subtotal = round(product.price * quantity, 2)
        self.personalization = personalization or {}


def lines_for_cart(raw_cart: dict[str, dict], db: Session) -> list[CartLine]:
    """Live view of a cart dict: drops products that were deleted/deactivated since being added."""
    if not raw_cart:
        return []
    product_ids = [int(pid) for pid in raw_cart.keys()]
    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    products_by_id = {p.id: p for p in products}

    lines: list[CartLine] = []
    for pid_str, entry in raw_cart.items():
        product = products_by_id.get(int(pid_str))
        if product is None or not product.active or product.deleted_at is not None:
            continue
        qty = entry.get("qty", 0)
        capped_qty = min(qty, product.stock) if product.stock > 0 else qty
        lines.append(CartLine(product, capped_qty, entry.get("p")))
    return lines


def get_cart_lines(request, db: Session) -> list[CartLine]:
    return lines_for_cart(read_cart(request), db)


def cart_totals(lines: list[CartLine]) -> tuple[float, int]:
    subtotal = round(sum(line.subtotal for line in lines), 2)
    item_count = sum(line.quantity for line in lines)
    return subtotal, item_count
