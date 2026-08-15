from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.cart_service import cart_totals, clear_cart, get_cart_lines, read_cart
from app.customer_auth import require_customer, require_customer_api
from app.database import get_db
from app.models import Coupon, Customer, Order, OrderItem, OrderStatus, OrderStatusHistory, Product
from app.qr import generate_qr_png_bytes, upi_payment_uri
from app.schemas import CheckoutRequest
from app.settings_service import compute_delivery_charge, get_all_settings, get_setting, manual_payment_available
from app.templating import render
from app.utils.order_number import generate_order_number
from app.utils.whatsapp import order_confirmation_message, whatsapp_chat_link

router = APIRouter()

MANUAL_PAYMENT_METHOD_LABEL = "UPI / Bank Transfer"


class _StockProblem(Exception):
    pass


class _CouponProblem(Exception):
    pass


def _validate_and_price_coupon(db: Session, code: str, subtotal: float, lock: bool) -> tuple[Coupon, float]:
    """Shared by the live checkout-page preview (lock=False, read-only) and
    the actual order-creation transaction (lock=True, inside the same
    row-locked section as inventory safety — see api_checkout). Locking the
    coupon row itself is what stops two customers from both winning the last
    use of a limited-use code."""
    query = db.query(Coupon).filter(Coupon.code == code)
    if lock:
        query = query.with_for_update()
    coupon = query.first()
    if coupon is None or not coupon.active:
        raise _CouponProblem("Invalid coupon code.")
    if coupon.expires_at and coupon.expires_at < datetime.now(timezone.utc):
        raise _CouponProblem("This coupon has expired.")
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise _CouponProblem("This coupon has reached its usage limit.")
    if subtotal < coupon.min_order_value:
        raise _CouponProblem(f"This coupon needs a minimum order of ₹{coupon.min_order_value:.0f}.")

    if coupon.discount_type == "percent":
        discount = subtotal * coupon.discount_value / 100
    else:
        discount = coupon.discount_value
    discount = round(min(discount, subtotal), 2)
    return coupon, discount


@router.get("/checkout")
def checkout_page(request: Request, db: Session = Depends(get_db), customer: Customer = Depends(require_customer)):
    lines = get_cart_lines(request, db)
    subtotal, item_count = cart_totals(lines)
    delivery_charge = compute_delivery_charge(db, subtotal) if lines else 0.0
    return render(
        request,
        "customer/checkout.html",
        {
            "customer": customer,
            "lines": lines,
            "subtotal": subtotal,
            "item_count": item_count,
            "delivery_charge": delivery_charge,
            "total": round(subtotal + delivery_charge, 2),
            "manual_payment_available": manual_payment_available(db),
        },
        db,
    )


def _order_number_response(order: Order) -> JSONResponse:
    response = JSONResponse({"order_number": order.order_number})
    clear_cart(response)
    return response


@router.post("/api/coupon/validate")
def validate_coupon(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(get_db),
    customer: Customer = Depends(require_customer_api),
):
    lines = get_cart_lines(request, db)
    subtotal, _ = cart_totals(lines)
    try:
        coupon, discount_amount = _validate_and_price_coupon(db, code.strip().upper(), subtotal, lock=False)
    except _CouponProblem as exc:
        return JSONResponse({"valid": False, "detail": str(exc)}, status_code=400)
    return JSONResponse({"valid": True, "code": coupon.code, "discount_amount": discount_amount})


@router.post("/api/checkout")
async def api_checkout(
    request: Request,
    db: Session = Depends(get_db),
    customer: Customer = Depends(require_customer_api),
):
    raw_cart = read_cart(request)
    if not raw_cart:
        return JSONResponse({"detail": "Your cart is empty."}, status_code=400)

    try:
        payload = await request.json()
        checkout_data = CheckoutRequest(**payload)
    except (ValidationError, ValueError) as exc:
        message = (
            exc.errors()[0]["msg"]
            if isinstance(exc, ValidationError) and exc.errors()
            else "Please check the information you entered."
        )
        return JSONResponse({"detail": message}, status_code=422)

    # Never trust the client's claim that manual payment is configured —
    # re-check server-side in case the admin turned it off after the page
    # loaded, and silently fall back to COD rather than failing checkout.
    if checkout_data.payment_method == "manual" and not manual_payment_available(db):
        checkout_data.payment_method = "cod"

    # Fast-path idempotency check on the ordinary (read-only) session — if a
    # previous attempt with this key already succeeded, just return it.
    existing = db.query(Order).filter(Order.idempotency_key == checkout_data.idempotency_key).first()
    if existing is not None:
        return _order_number_response(existing)

    product_ids = [int(pid) for pid in raw_cart.keys()]

    try:
        # SELECT ... FOR UPDATE takes a row-level lock on exactly these
        # products for the rest of this transaction, so a second concurrent
        # checkout touching the same product blocks here until this one
        # commits (then re-reads genuinely current stock) instead of both
        # reading stale stock and both succeeding — the "last 2 units, two
        # simultaneous buyers" oversell scenario. Ordering by id gives every
        # checkout the same lock-acquisition order, which avoids deadlocks
        # when two orders share more than one product.
        products = (
            db.query(Product)
            .filter(Product.id.in_(product_ids))
            .order_by(Product.id)
            .with_for_update()
            .all()
        )
        products_by_id = {p.id: p for p in products}

        order_items_data = []
        problems = []
        for pid_str, qty in raw_cart.items():
            pid = int(pid_str)
            product = products_by_id.get(pid)
            if product is None or not product.active or product.deleted_at is not None:
                problems.append("One of the items in your cart is no longer available.")
                continue
            if product.stock < qty:
                if product.stock <= 0:
                    problems.append(f"'{product.name}' just went out of stock.")
                else:
                    problems.append(f"Only {product.stock} of '{product.name}' left in stock.")
                continue
            order_items_data.append((product, qty))

        if problems or not order_items_data:
            raise _StockProblem(" ".join(problems) or "Your cart is empty.")

        subtotal = round(sum(product.price * qty for product, qty in order_items_data), 2)
        delivery_charge = compute_delivery_charge(db, subtotal)

        discount_amount = 0.0
        applied_coupon_code = None
        if checkout_data.coupon_code:
            coupon, discount_amount = _validate_and_price_coupon(db, checkout_data.coupon_code, subtotal, lock=True)
            coupon.used_count += 1
            applied_coupon_code = coupon.code

        total = round(subtotal + delivery_charge - discount_amount, 2)

        order = Order(
            order_number=generate_order_number(db),
            customer_id=customer.id,
            delivery_name=customer.name,
            delivery_mobile=customer.mobile,
            delivery_address=checkout_data.address,
            delivery_city=checkout_data.city,
            delivery_state=checkout_data.state,
            delivery_pincode=checkout_data.pincode,
            subtotal=subtotal,
            delivery_charge=delivery_charge,
            coupon_code=applied_coupon_code,
            discount_amount=discount_amount,
            total=total,
            payment_method=MANUAL_PAYMENT_METHOD_LABEL if checkout_data.payment_method == "manual" else "Cash on Delivery",
            payment_status="Pending",
            order_status=OrderStatus.PLACED.value,
            notes=checkout_data.delivery_instructions or None,
            idempotency_key=checkout_data.idempotency_key,
            gift_wrap=checkout_data.gift_wrap,
            gift_message=checkout_data.gift_message,
        )
        db.add(order)
        db.flush()

        for product, qty in order_items_data:
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name_snapshot=product.name,
                    price_snapshot=product.price,
                    quantity=qty,
                    subtotal=round(product.price * qty, 2),
                )
            )
            product.stock -= qty

        db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.PLACED.value))

        # Keep the account's saved address current so next checkout
        # pre-fills with wherever they most recently shipped to. Purely
        # a convenience default — never affects this or any past order.
        account = db.get(Customer, customer.id)
        account.address = checkout_data.address
        account.city = checkout_data.city
        account.state = checkout_data.state
        account.pincode = checkout_data.pincode

        order_number = order.order_number
        db.commit()
    except _StockProblem as exc:
        db.rollback()
        return JSONResponse({"detail": str(exc), "stock_issue": True}, status_code=409)
    except _CouponProblem as exc:
        db.rollback()
        return JSONResponse({"detail": str(exc), "coupon_issue": True}, status_code=400)
    except IntegrityError:
        # Two truly concurrent submits with the same idempotency key: the
        # loser's INSERT hits the UNIQUE constraint. Not an error for the
        # user — the order was created by the other request; return it.
        db.rollback()
        winner = db.query(Order).filter(Order.idempotency_key == checkout_data.idempotency_key).first()
        if winner is not None:
            return _order_number_response(winner)
        return JSONResponse({"detail": "We couldn't place your order just now. Please try again."}, status_code=500)

    final_order = db.query(Order).filter(Order.order_number == order_number).first()
    return _order_number_response(final_order)


@router.get("/order/{order_number}/success")
def order_success(order_number: str, request: Request, db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.customer))
        .filter(Order.order_number == order_number)
        .first()
    )
    if order is None:
        return render(request, "errors/404.html", {}, db, status_code=404)

    store_whatsapp = get_setting(db, "whatsapp_number")
    share_link = whatsapp_chat_link(store_whatsapp, order_confirmation_message(order))
    settings_values = get_all_settings(db)

    return render(
        request,
        "customer/order_success.html",
        {
            "order": order,
            "whatsapp_share_link": share_link,
            "upi_id": settings_values.get("upi_id", ""),
            "bank_account_name": settings_values.get("bank_account_name", ""),
            "bank_account_number": settings_values.get("bank_account_number", ""),
            "bank_ifsc": settings_values.get("bank_ifsc", ""),
            "bank_name": settings_values.get("bank_name", ""),
        },
        db,
    )


@router.get("/order/{order_number}/payment-qr.png")
def order_payment_qr(order_number: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if order is None or order.payment_method != MANUAL_PAYMENT_METHOD_LABEL:
        return Response(status_code=404)

    upi_id = get_setting(db, "upi_id").strip()
    if not upi_id:
        return Response(status_code=404)

    store_name = get_setting(db, "store_name") or "Store"
    uri = upi_payment_uri(upi_id, store_name, order.total, f"Order {order.order_number}")
    png_bytes = generate_qr_png_bytes(uri)
    return Response(content=png_bytes, media_type="image/png")
