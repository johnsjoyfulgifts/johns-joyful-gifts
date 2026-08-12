from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Order
from app.rate_limit import is_rate_limited
from app.schemas import TrackOrderRequest
from app.templating import render

router = APIRouter()

GENERIC_NOT_FOUND = "We couldn't find an order matching that Order ID and mobile number. Please double-check and try again."


@router.get("/track-order")
def track_order_page(request: Request, order_number: str = "", db: Session = Depends(get_db)):
    return render(request, "customer/track_order.html", {"prefill_order_number": order_number}, db)


@router.post("/api/track-order")
async def api_track_order(request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(f"track:{client_ip}", max_attempts=20, window_seconds=300):
        return JSONResponse({"detail": "Too many attempts. Please wait a few minutes and try again."}, status_code=429)

    try:
        payload = await request.json()
        data = TrackOrderRequest(**payload)
    except (ValidationError, ValueError):
        return JSONResponse({"detail": "Please enter a valid order ID and mobile number."}, status_code=422)

    order_number = data.order_number.strip()
    mobile = data.mobile.strip()

    order = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.customer), joinedload(Order.status_history))
        .filter(Order.order_number == order_number)
        .first()
    )

    # Same generic message whether the order number is wrong or the mobile
    # number doesn't match — never confirm that an order number exists to
    # someone who doesn't also know the mobile number on it.
    if order is None or order.customer.mobile.strip() != mobile:
        return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)

    return JSONResponse(
        {
            "order_number": order.order_number,
            "created_at": order.created_at.isoformat(),
            "order_status": order.order_status,
            "total": order.total,
            "courier_name": order.courier_name,
            "tracking_id": order.tracking_id,
            "tracking_url": order.tracking_url,
            "estimated_delivery": order.estimated_delivery,
            "items": [
                {"name": item.product_name_snapshot, "quantity": item.quantity, "subtotal": item.subtotal}
                for item in order.items
            ],
            "status_history": [
                {"status": h.status, "created_at": h.created_at.isoformat()} for h in order.status_history
            ],
        }
    )
