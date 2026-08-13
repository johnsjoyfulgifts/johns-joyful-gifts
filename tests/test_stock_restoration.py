"""
Cancelling an order (COD or online) must release the stock it reserved,
exactly once — this matters more now that online payments can be abandoned
mid-checkout, leaving a Pending order holding stock nobody will ever pay for.
"""

from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import SessionLocal
from app.models import Admin
from tests.helpers import checkout_cookies, checkout_payload, make_customer, make_product


def _login_as_admin(client: TestClient, email: str) -> None:
    db = SessionLocal()
    if not db.query(Admin).filter(Admin.email == email).first():
        db.add(Admin(name="Test Admin", email=email, password_hash=hash_password("testpass123"), role="owner"))
        db.commit()
    db.close()
    response = client.post("/admin/login", data={"email": email, "password": "testpass123"}, follow_redirects=False)
    assert response.status_code == 303, response.text


def test_cancelling_order_restores_stock(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Cancel Restock Product", price=150.0, stock=5)
    product_id = product.id
    customer = make_customer(db, mobile="9844400001")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    checkout_response = client.post(
        "/api/checkout",
        json=checkout_payload("cancel-restock-key"),
        cookies=checkout_cookies({product_id: 2}, customer_id),
    )
    assert checkout_response.status_code == 200
    order_number = checkout_response.json()["order_number"]

    db = SessionLocal()
    from app.models import Product

    after_order = db.get(Product, product_id).stock
    db.close()
    assert after_order == 3, "stock should be decremented by the order quantity"

    _login_as_admin(client, "stockrestore-admin@example.com")
    cancel_response = client.post(
        f"/admin/orders/{order_number}/status", data={"status": "Cancelled"}, follow_redirects=False
    )
    assert cancel_response.status_code == 303

    db = SessionLocal()
    from app.models import Order

    restocked = db.get(Product, product_id).stock
    order = db.query(Order).filter(Order.order_number == order_number).first()
    db.close()
    assert restocked == 5, "cancelling must restore the full reserved quantity"
    assert order.order_status == "Cancelled"
    assert order.stock_restored is True


def test_cancelling_twice_does_not_double_restore_stock(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Double Cancel Product", price=90.0, stock=4)
    product_id = product.id
    customer = make_customer(db, mobile="9844400002")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    checkout_response = client.post(
        "/api/checkout",
        json=checkout_payload("double-cancel-key"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert checkout_response.status_code == 200
    order_number = checkout_response.json()["order_number"]

    _login_as_admin(client, "doublecancel-admin@example.com")

    client.post(f"/admin/orders/{order_number}/status", data={"status": "Cancelled"}, follow_redirects=False)
    # Move it to another status and back to Cancelled — must not re-restore.
    client.post(f"/admin/orders/{order_number}/status", data={"status": "Order Confirmed"}, follow_redirects=False)
    client.post(f"/admin/orders/{order_number}/status", data={"status": "Cancelled"}, follow_redirects=False)

    db = SessionLocal()
    from app.models import Product

    final_stock = db.get(Product, product_id).stock
    db.close()
    assert final_stock == 4, "stock must only be restored once, not on every re-cancel"
