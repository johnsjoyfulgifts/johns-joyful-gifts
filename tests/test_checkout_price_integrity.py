"""
Spec section 49: the browser must never be trusted for final pricing. The
cart cookie only ever carries product_id -> quantity (see cart_service.py),
so there is no client-supplied price field to tamper with in the first
place — these tests prove the total actually charged always matches the
CURRENT server-side product price, even after the price changes between
adding to cart and checking out, and that snapshots then stay frozen.
"""

from fastapi.testclient import TestClient

from app.database import SessionLocal
from tests.helpers import checkout_cookies, checkout_payload, make_customer, make_product


def test_total_is_computed_from_current_server_side_price(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Price Integrity Product", price=100.0, stock=10)
    product_id = product.id
    customer = make_customer(db, mobile="9833300001")
    customer_id = customer.id
    db.close()

    # Simulate the product's price changing after it was added to a cart
    # (cart only ever stores product_id + quantity, never a price).
    db = SessionLocal()
    from app.models import Product

    live_product = db.get(Product, product_id)
    live_product.price = 250.0
    db.commit()
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("price-integrity-key"),
        cookies=checkout_cookies({product_id: 2}, customer_id),
    )
    assert response.status_code == 200
    order_number = response.json()["order_number"]

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.subtotal == 500.0, "2 x current price (250), not any stale/client price"
    assert order.items[0].price_snapshot == 250.0
    db.close()


def test_order_snapshot_survives_later_price_change(fastapi_app):
    """Once placed, an order's recorded price must never drift when the
    product's live price changes afterwards (spec section 19)."""
    db = SessionLocal()
    product = make_product(db, name="Snapshot Product", price=80.0, stock=10)
    product_id = product.id
    customer = make_customer(db, mobile="9833300002")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("snapshot-key"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert response.status_code == 200
    order_number = response.json()["order_number"]

    db = SessionLocal()
    from app.models import Product

    live_product = db.get(Product, product_id)
    live_product.price = 999.0
    live_product.name = "Renamed Product"
    db.commit()
    db.close()

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.items[0].price_snapshot == 80.0, "Order must keep the price at time of purchase"
    assert order.items[0].product_name_snapshot == "Snapshot Product", "Order must keep the name at time of purchase"
    assert order.total == 80.0 + order.delivery_charge
    db.close()


def test_delivery_charge_is_server_computed_not_client_supplied(fastapi_app):
    """The checkout request schema has no delivery_charge/subtotal/total
    field at all — proving there is nothing for a client to override."""
    from app.schemas import CheckoutRequest

    assert "delivery_charge" not in CheckoutRequest.model_fields
    assert "subtotal" not in CheckoutRequest.model_fields
    assert "total" not in CheckoutRequest.model_fields
    assert "price" not in CheckoutRequest.model_fields
