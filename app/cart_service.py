"""
Cart is stored client-side in a signed cookie (product_id -> quantity only).
Nothing about price or stock is trusted from the cookie: every read joins
against the live Product table, and checkout re-validates everything again
inside the order transaction. Worst case of a tampered cookie is a wrong
quantity number, which checkout will clamp/reject anyway.
"""

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product

settings = get_settings()
CART_COOKIE_NAME = "jjg_cart"
MAX_QTY_PER_ITEM = 20

_serializer = URLSafeSerializer(settings.secret_key, salt="cart")


def serialize_cart(cart: dict[str, int]) -> str:
    return _serializer.dumps(cart)


def read_cart(request) -> dict[str, int]:
    token = request.cookies.get(CART_COOKIE_NAME)
    if not token:
        return {}
    try:
        data = _serializer.loads(token)
    except BadSignature:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, int] = {}
    for product_id, qty in data.items():
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            continue
        if qty_int > 0:
            cleaned[str(product_id)] = min(qty_int, MAX_QTY_PER_ITEM)
    return cleaned


def write_cart(response, cart: dict[str, int]) -> None:
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


def add_item(request, response, product_id: int, quantity: int) -> dict[str, int]:
    cart = read_cart(request)
    key = str(product_id)
    cart[key] = min(cart.get(key, 0) + max(quantity, 1), MAX_QTY_PER_ITEM)
    write_cart(response, cart)
    return cart


def set_item_quantity(request, response, product_id: int, quantity: int) -> dict[str, int]:
    cart = read_cart(request)
    key = str(product_id)
    if quantity <= 0:
        cart.pop(key, None)
    else:
        cart[key] = min(quantity, MAX_QTY_PER_ITEM)
    write_cart(response, cart)
    return cart


def remove_item(request, response, product_id: int) -> dict[str, int]:
    cart = read_cart(request)
    cart.pop(str(product_id), None)
    write_cart(response, cart)
    return cart


def clear_cart(response) -> None:
    response.delete_cookie(CART_COOKIE_NAME, path="/")


class CartLine:
    def __init__(self, product: Product, quantity: int):
        self.product = product
        self.quantity = quantity
        self.subtotal = round(product.price * quantity, 2)


def lines_for_cart(raw_cart: dict[str, int], db: Session) -> list[CartLine]:
    """Live view of a cart dict: drops products that were deleted/deactivated since being added."""
    if not raw_cart:
        return []
    product_ids = [int(pid) for pid in raw_cart.keys()]
    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    products_by_id = {p.id: p for p in products}

    lines: list[CartLine] = []
    for pid_str, qty in raw_cart.items():
        product = products_by_id.get(int(pid_str))
        if product is None or not product.active or product.deleted_at is not None:
            continue
        capped_qty = min(qty, product.stock) if product.stock > 0 else qty
        lines.append(CartLine(product, capped_qty))
    return lines


def get_cart_lines(request, db: Session) -> list[CartLine]:
    return lines_for_cart(read_cart(request), db)


def cart_totals(lines: list[CartLine]) -> tuple[float, int]:
    subtotal = round(sum(line.subtotal for line in lines), 2)
    item_count = sum(line.quantity for line in lines)
    return subtotal, item_count
