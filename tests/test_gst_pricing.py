"""
Per-product GST pricing. Admin enters Base Price + GST % for a product; the
server (never the browser) computes GST Amount and Final Selling Price as
Product.price — the same field every existing customer-facing/cart/checkout
code path already reads, so GST support slots into pricing that already
existed rather than replacing it.

Existing (pre-GST) products must keep charging exactly what they charged
before: migrated with gst_percent=0 and base_price=price, so price is
unchanged. See tests/helpers.py make_product, which defaults the same way.
"""

from fastapi.testclient import TestClient

from app.auth import hash_password as admin_hash_password
from app.database import SessionLocal
from app.models import Admin, Product
from tests.helpers import checkout_cookies, checkout_payload, ensure_manual_payment, make_customer, make_product


def _make_admin(db, email, password="originalpass1"):
    admin = Admin(name="Test Admin", email=email, password_hash=admin_hash_password(password), role="super_admin")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def _login_admin(client, email, password="originalpass1"):
    resp = client.post("/admin/login", data={"email": email, "password": password}, follow_redirects=False)
    assert resp.status_code == 303


def test_existing_product_defaults_to_zero_gst_and_unchanged_price(db_session):
    """Simulates a pre-GST product carried through the migration: no GST
    fields were ever set for it, so it must keep its exact selling price."""
    product = make_product(db_session, name="Legacy Product", price=250.0)
    assert product.gst_percent == 0
    assert product.base_price == 250.0
    assert product.price == 250.0
    assert product.gst_amount == 0


def test_admin_edit_computes_gst_amount_and_final_price_server_side(fastapi_app):
    db = SessionLocal()
    _make_admin(db, "gst-admin1@example.com")
    product = make_product(db, name="Gift Mug", price=100.0, stock=10)
    product_id = product.id
    db.close()

    client = TestClient(fastapi_app)
    _login_admin(client, "gst-admin1@example.com")

    response = client.post(
        f"/admin/products/{product_id}/edit",
        data={
            "name": "Gift Mug",
            "base_price": "100",
            "gst_percent": "18",
            "stock": "10",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    updated = db.get(Product, product_id)
    assert updated.base_price == 100.0
    assert updated.gst_percent == 18.0
    assert updated.gst_amount == 18.0
    assert updated.price == 118.0, "Final price = base + GST amount, computed server-side"
    db.close()


def test_admin_edit_ignores_any_client_supplied_final_price(fastapi_app):
    """The form has no 'price' field at all anymore -- only base_price and
    gst_percent are accepted, so there is nothing for a tampered request to
    override the computed final price with."""
    from app.routers.admin_pages import product_edit_submit
    import inspect

    params = inspect.signature(product_edit_submit).parameters
    assert "price" not in params
    assert "base_price" in params
    assert "gst_percent" in params


def test_admin_edit_rejects_out_of_range_gst_percent(fastapi_app):
    db = SessionLocal()
    _make_admin(db, "gst-admin2@example.com")
    product = make_product(db, name="Bad GST Product", price=50.0, stock=5)
    product_id = product.id
    db.close()

    client = TestClient(fastapi_app)
    _login_admin(client, "gst-admin2@example.com")

    response = client.post(
        f"/admin/products/{product_id}/edit",
        data={"name": "Bad GST Product", "base_price": "50", "gst_percent": "150", "stock": "5"},
    )
    assert response.status_code == 400
    assert "GST" in response.text

    db = SessionLocal()
    unchanged = db.get(Product, product_id)
    assert unchanged.price == 50.0, "Rejected submission must never mutate the stored price"
    db.close()


def test_checkout_snapshots_gst_breakdown_on_order_item(fastapi_app):
    db = SessionLocal()
    ensure_manual_payment(db)
    product = make_product(db, name="GST Snapshot Product", base_price=100.0, gst_percent=18.0, price=118.0, stock=10)
    product_id = product.id
    customer = make_customer(db, mobile="9833300101")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("gst-snapshot-key"),
        cookies=checkout_cookies({product_id: 2}, customer_id),
    )
    assert response.status_code == 200
    order_number = response.json()["order_number"]

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    item = order.items[0]
    assert item.base_price_snapshot == 100.0
    assert item.gst_percent_snapshot == 18.0
    assert item.gst_amount_snapshot == 18.0
    assert item.price_snapshot == 118.0
    assert item.subtotal == 236.0, "2 x final (GST-inclusive) unit price"
    assert order.subtotal == 236.0
    db.close()


def test_order_gst_snapshot_survives_later_product_gst_change(fastapi_app):
    """Once placed, an order's GST breakdown must never drift when the
    product's own base price/GST rate changes afterwards -- same guarantee
    as the existing price-snapshot immutability tests."""
    db = SessionLocal()
    ensure_manual_payment(db)
    product = make_product(db, name="GST Drift Product", base_price=200.0, gst_percent=12.0, price=224.0, stock=10)
    product_id = product.id
    customer = make_customer(db, mobile="9833300102")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("gst-drift-key"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert response.status_code == 200
    order_number = response.json()["order_number"]

    db = SessionLocal()
    from app.models import Product

    live_product = db.get(Product, product_id)
    live_product.base_price = 500.0
    live_product.gst_percent = 28.0
    live_product.price = 640.0
    db.commit()
    db.close()

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    item = order.items[0]
    assert item.base_price_snapshot == 200.0
    assert item.gst_percent_snapshot == 12.0
    assert item.gst_amount_snapshot == 24.0
    assert item.price_snapshot == 224.0
    db.close()
