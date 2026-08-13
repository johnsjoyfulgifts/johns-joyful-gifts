"""
Covers the one security-critical piece of the online-payment integration:
a payment is only ever marked Paid if its HMAC signature verifies against
our Razorpay secret key. A tampered or forged confirmation must be rejected.

The actual "create a Razorpay order" step (app.payments.create_razorpay_order)
requires a real network call to Razorpay's API with real credentials and is
NOT exercised here — these tests use fake test-mode credentials (set in
conftest.py) purely for the local HMAC computation, which never leaves the
process. See README.md for how to manually verify the full flow with real
test-mode keys.
"""

import hashlib
import hmac

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.payments import verify_payment_signature
from tests.helpers import checkout_payload, make_product

TEST_SECRET = get_settings().razorpay_key_secret


def _sign(order_id: str, payment_id: str) -> str:
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(TEST_SECRET.encode(), msg, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted():
    signature = _sign("order_ABC123", "pay_XYZ789")
    assert verify_payment_signature("order_ABC123", "pay_XYZ789", signature) is True


def test_tampered_signature_is_rejected():
    signature = _sign("order_ABC123", "pay_XYZ789")
    # Attacker reuses a valid signature but swaps in a different payment id.
    assert verify_payment_signature("order_ABC123", "pay_DIFFERENT", signature) is False


def test_garbage_signature_is_rejected():
    assert verify_payment_signature("order_ABC123", "pay_XYZ789", "not-a-real-signature") is False


def _place_online_order_bypassing_razorpay_api(mobile="9600000001"):
    """
    Creates a real order the same way checkout.py does for an online
    payment, but skips the actual Razorpay API call (create_razorpay_order)
    since that needs real network access + real credentials. Directly
    exercises the DB state a genuine online checkout would leave behind:
    an Order with payment_method="Online Payment", payment_status="Pending",
    and a razorpay_order_id, so verify-payment can be tested end-to-end.
    """
    from app.models import Order, OrderItem, OrderStatus, OrderStatusHistory, Customer

    db = SessionLocal()
    product = make_product(db, name="Online Pay Test Product", price=400.0, stock=5)

    customer = Customer(
        name="Online Payer", mobile=mobile, address="1 Test Rd", city="Chennai", state="TN", pincode="600001"
    )
    db.add(customer)
    db.flush()

    order = Order(
        order_number=f"JJG-TEST-{mobile}",
        customer_id=customer.id,
        subtotal=400.0,
        delivery_charge=0,
        total=400.0,
        payment_method="Online Payment",
        payment_status="Pending",
        order_status=OrderStatus.PLACED.value,
        idempotency_key=f"online-test-{mobile}",
        razorpay_order_id=f"order_test_{mobile}",
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            price_snapshot=400.0,
            quantity=1,
            subtotal=400.0,
        )
    )
    db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.PLACED.value))
    db.commit()
    order_number, razorpay_order_id = order.order_number, order.razorpay_order_id
    db.close()
    return order_number, razorpay_order_id


def test_verify_payment_marks_order_paid_with_valid_signature(fastapi_app):
    order_number, razorpay_order_id = _place_online_order_bypassing_razorpay_api(mobile="9600000001")
    payment_id = "pay_valid_001"
    signature = _sign(razorpay_order_id, payment_id)

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout/verify-payment",
        json={
            "order_number": order_number,
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        },
    )
    assert response.status_code == 200

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.payment_status == "Paid"
    assert order.razorpay_payment_id == payment_id
    db.close()


def test_verify_payment_rejects_forged_signature(fastapi_app):
    order_number, razorpay_order_id = _place_online_order_bypassing_razorpay_api(mobile="9600000002")

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout/verify-payment",
        json={
            "order_number": order_number,
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": "pay_forged",
            "razorpay_signature": "0" * 64,
        },
    )
    assert response.status_code == 400

    db = SessionLocal()
    from app.models import Order

    order = db.query(Order).filter(Order.order_number == order_number).first()
    assert order.payment_status == "Pending", "A forged signature must never mark an order as paid"
    db.close()


def test_verify_payment_rejects_mismatched_order(fastapi_app):
    """A signature valid for one order/payment pair must not verify against
    a different order_number — prevents replaying a real payment onto an
    unrelated order."""
    order_number, razorpay_order_id = _place_online_order_bypassing_razorpay_api(mobile="9600000003")
    other_order_number, _ = _place_online_order_bypassing_razorpay_api(mobile="9600000004")

    payment_id = "pay_valid_002"
    signature = _sign(razorpay_order_id, payment_id)  # valid for `order_number`'s razorpay_order_id

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout/verify-payment",
        json={
            "order_number": other_order_number,  # wrong order
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        },
    )
    assert response.status_code == 404


def test_checkout_online_payment_field_defaults_to_cod():
    from app.schemas import CheckoutRequest

    payload = checkout_payload("default-method-key")
    parsed = CheckoutRequest(**payload)
    assert parsed.payment_method == "cod"


def test_checkout_rejects_invalid_payment_method():
    from pydantic import ValidationError
    from app.schemas import CheckoutRequest

    payload = checkout_payload("bad-method-key", payment_method="bitcoin")
    try:
        CheckoutRequest(**payload)
        assert False, "should have raised"
    except ValidationError:
        pass
