"""
Every order requires a logged-in customer (mobile + password) — no guest
checkout. Covers registration, login, and that checkout is genuinely gated
behind an active session rather than just hidden in the UI.
"""

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from tests.helpers import checkout_cookies, checkout_payload, ensure_manual_payment, make_customer, make_product


@pytest.fixture(autouse=True)
def _manual_payment_configured():
    db = SessionLocal()
    ensure_manual_payment(db)
    db.close()


def test_register_creates_account_and_logs_in(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.post(
        "/register",
        data={
            "name": "New Customer",
            "mobile": "9700000001",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "email": "",
            "next": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    from app.models import Customer

    customer = db.query(Customer).filter(Customer.mobile == "9700000001").first()
    assert customer is not None
    assert customer.name == "New Customer"
    db.close()


def test_register_rejects_duplicate_mobile(fastapi_app):
    db = SessionLocal()
    make_customer(db, mobile="9700000002", name="Existing Customer")
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/register",
        data={
            "name": "Another Person",
            "mobile": "9700000002",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "email": "",
            "next": "",
        },
    )
    assert response.status_code == 400
    assert "already exists" in response.text.lower()


def test_register_rejects_short_password(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.post(
        "/register",
        data={
            "name": "Short Password",
            "mobile": "9700000003",
            "password": "short",
            "confirm_password": "short",
            "email": "",
            "next": "",
        },
    )
    assert response.status_code == 400
    assert "8 characters" in response.text


def test_register_rejects_mismatched_passwords(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.post(
        "/register",
        data={
            "name": "Mismatch",
            "mobile": "9700000004",
            "password": "securepass1",
            "confirm_password": "differentpass1",
            "email": "",
            "next": "",
        },
    )
    assert response.status_code == 400
    assert "passwords" in response.text.lower() and "match" in response.text.lower()


def test_login_succeeds_with_correct_password(fastapi_app):
    db = SessionLocal()
    make_customer(db, mobile="9700000005", password="correctpass1")
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/login", data={"mobile": "9700000005", "password": "correctpass1", "next": ""}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "jjg_customer_session" in response.cookies


def test_login_rejects_wrong_password(fastapi_app):
    db = SessionLocal()
    make_customer(db, mobile="9700000006", password="correctpass1")
    db.close()

    client = TestClient(fastapi_app)
    response = client.post("/login", data={"mobile": "9700000006", "password": "wrongpass1", "next": ""})
    assert response.status_code == 401
    assert "incorrect" in response.text.lower()


def test_login_rejects_nonexistent_account(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.post("/login", data={"mobile": "9799999999", "password": "whatever1", "next": ""})
    assert response.status_code == 401


def test_checkout_page_redirects_to_login_when_logged_out(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.get("/checkout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_checkout_api_returns_401_when_logged_out(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Gated Checkout Product", price=100.0, stock=5)
    product_id = product.id
    db.close()

    from tests.helpers import cart_cookie_header

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("logged-out-key"),
        cookies=cart_cookie_header({product_id: 1}),  # cart only, no customer session
    )
    assert response.status_code == 401


def test_checkout_succeeds_when_logged_in_and_links_correct_customer(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Logged In Checkout Product", price=200.0, stock=5)
    product_id = product.id
    customer = make_customer(db, mobile="9700000007", name="Logged In Shopper")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("logged-in-key"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert response.status_code == 200
    order_number = response.json()["order_number"]

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.customer_id == customer_id
    assert order.delivery_name == "Logged In Shopper"
    assert order.delivery_mobile == "9700000007"
    db.close()


def test_two_orders_from_same_customer_reuse_one_account_with_independent_addresses(fastapi_app):
    db = SessionLocal()
    product_a = make_product(db, name="First Order Product", price=100.0, stock=5)
    product_b = make_product(db, name="Second Order Product", price=150.0, stock=5)
    product_a_id, product_b_id = product_a.id, product_b.id
    customer = make_customer(db, mobile="9700000008", name="Repeat Customer")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)

    first = client.post(
        "/api/checkout",
        json=checkout_payload("repeat-order-1", address="First Address, House 1", city="Chennai", pincode="600001"),
        cookies=checkout_cookies({product_a_id: 1}, customer_id),
    )
    assert first.status_code == 200

    second = client.post(
        "/api/checkout",
        json=checkout_payload(
            "repeat-order-2", address="Second Address, House 2", city="Bengaluru", state="Karnataka", pincode="560001"
        ),
        cookies=checkout_cookies({product_b_id: 1}, customer_id),
    )
    assert second.status_code == 200

    db = SessionLocal()
    from app.models import Customer, Order

    all_customers = db.query(Customer).filter(Customer.mobile == "9700000008").all()
    assert len(all_customers) == 1, "No duplicate Customer row should be created across orders"

    orders = db.query(Order).filter(Order.customer_id == customer_id).order_by(Order.created_at).all()
    assert len(orders) == 2
    assert orders[0].delivery_address == "First Address, House 1"
    assert orders[0].delivery_city == "Chennai"
    assert orders[1].delivery_address == "Second Address, House 2"
    assert orders[1].delivery_city == "Bengaluru"
    assert orders[0].delivery_address != orders[1].delivery_address, "Each order keeps its own address snapshot"
    db.close()
