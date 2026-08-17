"""
Free manual payment (UPI / bank transfer): no gateway, no fees, no API keys.
It's the only checkout payment method (Cash on Delivery has been removed).
The admin fills in a UPI ID / bank details and manually marks orders Paid
after checking their own UPI/bank app -- these tests cover that checkout is
refused until it's actually configured, and that a client can't force it on
server-side by claiming it's available when it isn't.
"""

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.settings_service import manual_payment_available, set_settings
from tests.helpers import checkout_cookies, checkout_payload, make_customer, make_product


def _login_as_admin(client: TestClient, email: str) -> None:
    from app.auth import hash_password
    from app.models import Admin

    db = SessionLocal()
    if not db.query(Admin).filter(Admin.email == email).first():
        db.add(Admin(name="Test Admin", email=email, password_hash=hash_password("testpass123"), role="super_admin"))
        db.commit()
    db.close()
    response = client.post("/admin/login", data={"email": email, "password": "testpass123"}, follow_redirects=False)
    assert response.status_code == 303, response.text


def test_manual_payment_unavailable_when_disabled():
    db = SessionLocal()
    set_settings(db, {"manual_payment_enabled": "false", "upi_id": "", "bank_account_number": ""})
    assert manual_payment_available(db) is False
    db.close()


def test_manual_payment_unavailable_when_enabled_but_not_configured():
    db = SessionLocal()
    # Toggled on, but no UPI/bank filled in -- explicitly cleared rather than
    # relying on a fresh DB, since other tests in the suite also configure
    # these same settings keys.
    set_settings(db, {"manual_payment_enabled": "true", "upi_id": "", "bank_account_number": ""})
    assert manual_payment_available(db) is False
    db.close()


def test_manual_payment_available_once_upi_configured():
    db = SessionLocal()
    set_settings(db, {"manual_payment_enabled": "true", "upi_id": "shop@okhdfcbank"})
    assert manual_payment_available(db) is True
    db.close()


def test_checkout_rejected_when_payment_not_configured(fastapi_app):
    """Cash on Delivery has been removed, so there's no fallback left --
    checkout must refuse the order (not silently accept one with no valid
    way to pay) if the admin hasn't actually configured UPI/bank details,
    regardless of what the client's payload claims."""
    db = SessionLocal()
    set_settings(db, {"manual_payment_enabled": "false", "upi_id": "", "bank_account_number": ""})
    product = make_product(db, name="Unavailable Payment Product", price=100.0, stock=5)
    product_id = product.id
    customer = make_customer(db, mobile="9700000101")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("manual-unavailable-key", payment_method="manual"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert response.status_code == 400
    assert "payment" in response.json()["detail"].lower()

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.idempotency_key == "manual-unavailable-key").first()
    assert order is None, "no order should be created when there's no valid payment method"
    db.close()


def test_checkout_manual_payment_succeeds_when_configured(fastapi_app):
    db = SessionLocal()
    set_settings(db, {"manual_payment_enabled": "true", "upi_id": "shop@okhdfcbank"})
    product = make_product(db, name="Manual Payment Product", price=250.0, stock=5)
    product_id = product.id
    customer = make_customer(db, mobile="9700000102")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("manual-success-key", payment_method="manual"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert response.status_code == 200
    order_number = response.json()["order_number"]

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.payment_method == "UPI / Bank Transfer"
    assert order.payment_status == "Pending"
    db.close()

    # QR endpoint should now serve a real PNG for this order.
    qr_response = client.get(f"/order/{order_number}/payment-qr.png")
    assert qr_response.status_code == 200
    assert qr_response.headers["content-type"] == "image/png"
    assert qr_response.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes


def test_qr_endpoint_404s_for_non_manual_payment_order(fastapi_app):
    """Defense in depth: 'cod' is no longer a selectable payment method, but
    a legacy order from before it was removed could still have that value
    stored -- the QR endpoint must never serve a payment QR for it."""
    db = SessionLocal()
    set_settings(db, {"manual_payment_enabled": "true", "upi_id": "shop@okhdfcbank"})
    customer = make_customer(db, mobile="9700000103")

    from app.models import Order, OrderStatus

    order = Order(
        order_number="JJG-TEST-LEGACYCOD01",
        customer_id=customer.id,
        delivery_name=customer.name,
        delivery_mobile=customer.mobile,
        delivery_address="123 Test Street",
        delivery_city="Chennai",
        delivery_state="Tamil Nadu",
        delivery_pincode="600001",
        subtotal=100.0,
        delivery_charge=0.0,
        total=100.0,
        payment_method="Cash on Delivery",
        payment_status="Pending",
        order_status=OrderStatus.PLACED.value,
        idempotency_key="legacy-cod-order-key",
    )
    db.add(order)
    db.commit()
    order_number = order.order_number
    db.close()

    client = TestClient(fastapi_app)
    qr_response = client.get(f"/order/{order_number}/payment-qr.png")
    assert qr_response.status_code == 404


def test_qr_endpoint_404s_for_nonexistent_order(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.get("/order/JJG-00000000-9999/payment-qr.png")
    assert response.status_code == 404


def test_admin_can_mark_order_as_paid(fastapi_app):
    db = SessionLocal()
    set_settings(db, {"manual_payment_enabled": "true", "upi_id": "shop@okhdfcbank"})
    product = make_product(db, name="Mark Paid Product", price=150.0, stock=5)
    product_id = product.id
    customer = make_customer(db, mobile="9700000104")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    checkout_response = client.post(
        "/api/checkout",
        json=checkout_payload("mark-paid-key", payment_method="manual"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    order_number = checkout_response.json()["order_number"]

    _login_as_admin(client, "markpaid-admin@example.com")
    mark_paid_response = client.post(f"/admin/orders/{order_number}/mark-paid", follow_redirects=False)
    assert mark_paid_response.status_code == 303

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.payment_status == "Paid"
    db.close()
