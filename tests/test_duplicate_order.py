"""
Spec section 20: double-clicks, refreshes, and slow-network retries must
never create two orders for one customer action. The frontend disables the
submit button, but the real guarantee has to be server-side.
"""

import threading

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from tests.helpers import checkout_cookies, checkout_payload, ensure_manual_payment, make_customer, make_product


@pytest.fixture(autouse=True)
def _manual_payment_configured():
    db = SessionLocal()
    ensure_manual_payment(db)
    db.close()


def test_sequential_retry_with_same_idempotency_key_returns_same_order(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Duplicate Test Product", price=300.0, stock=10)
    product_id = product.id
    customer = make_customer(db, mobile="9822200001")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    key = "same-key-retry-test"

    first = client.post(
        "/api/checkout", json=checkout_payload(key), cookies=checkout_cookies({product_id: 1}, customer_id)
    )
    assert first.status_code == 200
    first_order_number = first.json()["order_number"]

    # Simulate a refresh/retry: same idempotency key, cart cookie already
    # cleared by the first response, so re-supply it as the client would
    # still have it in sessionStorage/local state at retry time.
    second = client.post(
        "/api/checkout", json=checkout_payload(key), cookies=checkout_cookies({product_id: 1}, customer_id)
    )
    assert second.status_code == 200
    assert second.json()["order_number"] == first_order_number

    db = SessionLocal()
    from app.models import Order, Product

    matching_orders = db.query(Order).filter(Order.idempotency_key == key).all()
    assert len(matching_orders) == 1, "Only one order should exist for this idempotency key"

    final_product = db.get(Product, product_id)
    assert final_product.stock == 9, "Stock should only be decremented once, not twice"
    db.close()


def test_truly_concurrent_double_submit_with_same_key_creates_one_order(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Concurrent Duplicate Product", price=250.0, stock=10)
    product_id = product.id
    customer = make_customer(db, mobile="9822200002")
    customer_id = customer.id
    db.close()

    key = "concurrent-same-key"
    results = []
    barrier = threading.Barrier(2)

    def submit():
        client = TestClient(fastapi_app)
        barrier.wait()
        response = client.post(
            "/api/checkout", json=checkout_payload(key), cookies=checkout_cookies({product_id: 1}, customer_id)
        )
        results.append(response)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    order_numbers = {r.json()["order_number"] for r in results}
    assert len(order_numbers) == 1, "Both concurrent submits must resolve to the SAME order"

    db = SessionLocal()
    from app.models import Order, Product

    matching_orders = db.query(Order).filter(Order.idempotency_key == key).all()
    assert len(matching_orders) == 1

    final_product = db.get(Product, product_id)
    assert final_product.stock == 9, "Stock decremented exactly once across both concurrent attempts"
    db.close()
