"""
The spec's core safety requirement (section 14): if stock = 2 and Customer A
orders 2, Customer B must NOT also be able to order 2. This test drives that
exact scenario with real concurrent HTTP requests against a product with
stock = 1, both wanting 1 unit — only one may succeed.
"""

import threading

from fastapi.testclient import TestClient

from app.database import SessionLocal
from tests.helpers import checkout_cookies, checkout_payload, make_customer, make_product


def test_concurrent_checkouts_cannot_oversell(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Limited Teddy", price=500.0, stock=1)
    product_id = product.id
    customer_a = make_customer(db, mobile="9811100001", name="Racer A")
    customer_b = make_customer(db, mobile="9811100002", name="Racer B")
    customer_a_id, customer_b_id = customer_a.id, customer_b.id
    db.close()

    results = []
    barrier = threading.Barrier(2)

    def attempt_checkout(key_suffix: str, customer_id: int):
        client = TestClient(fastapi_app)
        barrier.wait()  # both threads fire as close to simultaneously as possible
        response = client.post(
            "/api/checkout",
            json=checkout_payload(f"race-attempt-{key_suffix}"),
            cookies=checkout_cookies({product_id: 1}, customer_id),
        )
        results.append(response)

    threads = [
        threading.Thread(target=attempt_checkout, args=("1", customer_a_id)),
        threading.Thread(target=attempt_checkout, args=("2", customer_b_id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(results) == 2
    status_codes = sorted(r.status_code for r in results)
    # Exactly one succeeds (200 with an order number), the other is rejected
    # for insufficient stock (409) — never both succeeding, never both failing.
    assert status_codes == [200, 409], f"Unexpected outcome: {[(r.status_code, r.text) for r in results]}"

    winner = next(r for r in results if r.status_code == 200)
    loser = next(r for r in results if r.status_code == 409)
    assert "order_number" in winner.json()
    assert loser.json().get("stock_issue") is True

    db = SessionLocal()
    from app.models import Product

    final_product = db.get(Product, product_id)
    assert final_product.stock == 0, "Stock must be exactly 0, not negative and not still 1"

    from app.models import Order

    orders_for_product = db.query(Order).filter(Order.idempotency_key.like("race-attempt-%")).all()
    assert len(orders_for_product) == 1, "Exactly one order should have been created, not two"
    db.close()


def test_checkout_rejects_when_stock_insufficient(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Out Soon", price=200.0, stock=1)
    product_id = product.id
    customer = make_customer(db, mobile="9811100003")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("insufficient-stock-key"),
        cookies=checkout_cookies({product_id: 5}, customer_id),  # wants 5, only 1 available
    )
    assert response.status_code == 409
    assert response.json().get("stock_issue") is True

    db = SessionLocal()
    from app.models import Product

    final_product = db.get(Product, product_id)
    assert final_product.stock == 1, "Stock must be untouched when the order is rejected"
    db.close()


def test_checkout_rejects_inactive_product(fastapi_app):
    db = SessionLocal()
    product = make_product(db, name="Discontinued Toy", price=150.0, stock=10, active=False)
    product_id = product.id
    customer = make_customer(db, mobile="9811100004")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/checkout",
        json=checkout_payload("inactive-product-key"),
        cookies=checkout_cookies({product_id: 1}, customer_id),
    )
    assert response.status_code == 409
