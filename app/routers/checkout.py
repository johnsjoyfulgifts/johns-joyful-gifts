from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.cart_service import cart_totals, clear_cart, get_cart_lines, read_cart
from app.database import get_db, immediate_write_session
from app.models import Customer, Order, OrderItem, OrderStatus, OrderStatusHistory, Product
from app.payments import create_razorpay_order, verify_payment_signature
from app.schemas import CheckoutRequest, VerifyPaymentRequest
from app.settings_service import compute_delivery_charge, get_setting, online_payment_available
from app.templating import render
from app.utils.order_number import generate_order_number
from app.utils.whatsapp import order_confirmation_message, whatsapp_chat_link
from app.config import get_settings

settings = get_settings()

router = APIRouter()


class _StockProblem(Exception):
    pass


@router.get("/checkout")
def checkout_page(request: Request, db: Session = Depends(get_db)):
    lines = get_cart_lines(request, db)
    subtotal, item_count = cart_totals(lines)
    delivery_charge = compute_delivery_charge(db, subtotal) if lines else 0.0
    return render(
        request,
        "customer/checkout.html",
        {
            "lines": lines,
            "subtotal": subtotal,
            "item_count": item_count,
            "delivery_charge": delivery_charge,
            "total": round(subtotal + delivery_charge, 2),
            "online_payment_available": online_payment_available(db),
            "razorpay_key_id": settings.razorpay_key_id,
        },
        db,
    )


def _ensure_razorpay_order(db: Session, order: Order) -> dict:
    """Creates the Razorpay order on first call, reuses it on retries
    (e.g. duplicate-submit returning the same local order). Network call to
    Razorpay — deliberately kept outside any SQLite write-lock transaction."""
    if not order.razorpay_order_id:
        try:
            razorpay_order = create_razorpay_order(order.order_number, order.total)
        except Exception:
            return {"payment_error": True}
        order.razorpay_order_id = razorpay_order["id"]
        db.commit()
    return {
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_key_id": settings.razorpay_key_id,
        "amount_paise": int(round(order.total * 100)),
    }


def _order_number_response(order: Order, db: Session | None = None) -> JSONResponse:
    payload = {"order_number": order.order_number, "payment_method": order.payment_method}
    if order.payment_method == "Online Payment" and order.payment_status != "Paid" and db is not None:
        payload.update(_ensure_razorpay_order(db, order))
    response = JSONResponse(payload)
    clear_cart(response)
    return response


@router.post("/api/checkout")
async def api_checkout(request: Request, db: Session = Depends(get_db)):
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

    # Never trust the client's claim that online payment is available —
    # re-check server-side in case the admin disabled it after the page
    # loaded, and silently fall back to COD rather than failing checkout.
    if checkout_data.payment_method == "online" and not online_payment_available(db):
        checkout_data.payment_method = "cod"

    # Fast-path idempotency check on the ordinary (read-only) session — if a
    # previous attempt with this key already succeeded, just return it.
    existing = db.query(Order).filter(Order.idempotency_key == checkout_data.idempotency_key).first()
    if existing is not None:
        return _order_number_response(existing, db)

    product_ids = [int(pid) for pid in raw_cart.keys()]

    try:
        with immediate_write_session() as write_db:
            products = write_db.query(Product).filter(Product.id.in_(product_ids)).all()
            products_by_id = {p.id: p for p in products}

            order_items_data = []
            problems = []
            for pid_str, qty in raw_cart.items():
                pid = int(pid_str)
                product = products_by_id.get(pid)
                if product is None or not product.active:
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
            delivery_charge = compute_delivery_charge(write_db, subtotal)
            total = round(subtotal + delivery_charge, 2)

            customer = Customer(
                name=checkout_data.full_name,
                mobile=checkout_data.mobile,
                email=checkout_data.email or None,
                address=checkout_data.address,
                city=checkout_data.city,
                state=checkout_data.state,
                pincode=checkout_data.pincode,
            )
            write_db.add(customer)
            write_db.flush()

            order = Order(
                order_number=generate_order_number(write_db),
                customer_id=customer.id,
                subtotal=subtotal,
                delivery_charge=delivery_charge,
                total=total,
                payment_method="Online Payment" if checkout_data.payment_method == "online" else "Cash on Delivery",
                payment_status="Pending",
                order_status=OrderStatus.PLACED.value,
                notes=checkout_data.delivery_instructions or None,
                idempotency_key=checkout_data.idempotency_key,
            )
            write_db.add(order)
            write_db.flush()

            for product, qty in order_items_data:
                write_db.add(
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

            write_db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.PLACED.value))
            order_number = order.order_number
    except _StockProblem as exc:
        return JSONResponse({"detail": str(exc), "stock_issue": True}, status_code=409)
    except IntegrityError:
        # Two truly concurrent submits with the same idempotency key: the
        # loser's INSERT hits the UNIQUE constraint. Not an error for the
        # user — the order was created by the other request; return it.
        winner = db.query(Order).filter(Order.idempotency_key == checkout_data.idempotency_key).first()
        if winner is not None:
            return _order_number_response(winner, db)
        return JSONResponse({"detail": "We couldn't place your order just now. Please try again."}, status_code=500)

    final_order = db.query(Order).filter(Order.order_number == order_number).first()
    return _order_number_response(final_order, db)


@router.post("/api/checkout/verify-payment")
async def api_verify_payment(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
        data = VerifyPaymentRequest(**payload)
    except (ValidationError, ValueError):
        return JSONResponse({"detail": "Invalid payment confirmation."}, status_code=422)

    order = (
        db.query(Order)
        .filter(Order.order_number == data.order_number, Order.razorpay_order_id == data.razorpay_order_id)
        .first()
    )
    if order is None:
        return JSONResponse({"detail": "Order not found."}, status_code=404)

    if order.payment_status == "Paid":
        return JSONResponse({"order_number": order.order_number})  # already verified, idempotent

    # The one place a client's "payment succeeded" claim is trusted: only
    # after an HMAC signature computed with our Razorpay secret key checks
    # out. A tampered/forged confirmation is rejected here, not upstream.
    is_valid = verify_payment_signature(data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature)
    if not is_valid:
        return JSONResponse({"detail": "Payment verification failed. Please contact us if money was deducted."}, status_code=400)

    order.payment_status = "Paid"
    order.razorpay_payment_id = data.razorpay_payment_id
    db.commit()

    return JSONResponse({"order_number": order.order_number})


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

    return render(request, "customer/order_success.html", {"order": order, "whatsapp_share_link": share_link}, db)
