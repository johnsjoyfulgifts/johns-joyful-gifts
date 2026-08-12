"""
Spec section 22: order tracking must require BOTH order ID and the mobile
number used at checkout, must never leak whether an order ID merely exists,
and must never expose another customer's order.
"""

from fastapi.testclient import TestClient

from app.database import SessionLocal
from tests.helpers import cart_cookie_header, checkout_payload, make_product


def _place_test_order(fastapi_app, mobile="9111111111"):
    db = SessionLocal()
    product = make_product(db, name="Tracking Test Product", price=100.0, stock=5)
    product_id = product.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload(f"track-test-{mobile}", mobile=mobile),
        cookies=cart_cookie_header({product_id: 1}),
    )
    assert response.status_code == 200
    return response.json()["order_number"]


def test_tracking_succeeds_with_correct_order_and_mobile(fastapi_app):
    order_number = _place_test_order(fastapi_app, mobile="9111111111")
    client = TestClient(fastapi_app)
    response = client.post("/api/track-order", json={"order_number": order_number, "mobile": "9111111111"})
    assert response.status_code == 200
    body = response.json()
    assert body["order_number"] == order_number


def test_tracking_rejects_correct_order_wrong_mobile(fastapi_app):
    order_number = _place_test_order(fastapi_app, mobile="9222222222")
    client = TestClient(fastapi_app)
    response = client.post("/api/track-order", json={"order_number": order_number, "mobile": "0000000000"})
    assert response.status_code == 404


def test_tracking_rejects_nonexistent_order(fastapi_app):
    client = TestClient(fastapi_app)
    response = client.post("/api/track-order", json={"order_number": "JJG-99999999-9999", "mobile": "9876543210"})
    assert response.status_code == 404


def test_tracking_error_message_identical_for_wrong_order_and_wrong_mobile(fastapi_app):
    """The error message must not let an attacker distinguish 'order doesn't
    exist' from 'order exists but mobile is wrong' — that would let someone
    enumerate valid order numbers."""
    order_number = _place_test_order(fastapi_app, mobile="9333333333")
    client = TestClient(fastapi_app)

    wrong_mobile_response = client.post(
        "/api/track-order", json={"order_number": order_number, "mobile": "1234567890"}
    )
    nonexistent_order_response = client.post(
        "/api/track-order", json={"order_number": "JJG-00000000-0000", "mobile": "1234567890"}
    )

    assert wrong_mobile_response.status_code == nonexistent_order_response.status_code == 404
    assert wrong_mobile_response.json()["detail"] == nonexistent_order_response.json()["detail"]


def test_cannot_access_another_customers_order_by_guessing_number(fastapi_app):
    order_a = _place_test_order(fastapi_app, mobile="9444444444")
    _order_b = _place_test_order(fastapi_app, mobile="9555555555")

    client = TestClient(fastapi_app)
    # Attacker knows order A's number (e.g. sequential JJG-... guessing) but
    # not the mobile number that placed it.
    response = client.post("/api/track-order", json={"order_number": order_a, "mobile": "9555555555"})
    assert response.status_code == 404
