from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.cart_service import (
    cart_totals,
    clear_cart,
    get_cart_lines,
    lines_for_cart,
    read_cart,
    write_cart,
)
from app.database import get_db
from app.models import Product
from app.settings_service import compute_delivery_charge, get_all_settings
from app.templating import render
from app.utils.whatsapp import cart_enquiry_message, whatsapp_chat_link

router = APIRouter()


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept or request.headers.get("x-requested-with") == "fetch"


@router.get("/cart")
def cart_page(request: Request, db: Session = Depends(get_db)):
    lines = get_cart_lines(request, db)
    subtotal, item_count = cart_totals(lines)
    delivery_charge = compute_delivery_charge(db, subtotal) if lines else 0.0
    total = round(subtotal + delivery_charge, 2)

    cart_whatsapp_link = None
    if lines:
        whatsapp_number = get_all_settings(db).get("whatsapp_number", "")
        cart_whatsapp_link = whatsapp_chat_link(whatsapp_number, cart_enquiry_message(lines, subtotal, total))

    return render(
        request,
        "customer/cart.html",
        {
            "lines": lines,
            "subtotal": subtotal,
            "item_count": item_count,
            "delivery_charge": delivery_charge,
            "total": total,
            "cart_whatsapp_link": cart_whatsapp_link,
        },
        db,
    )


def _respond(request: Request, redirect_to: str, new_cart: dict[str, int], db: Session):
    if not _wants_json(request):
        response = RedirectResponse(url=redirect_to, status_code=303)
        write_cart(response, new_cart)
        return response

    lines = lines_for_cart(new_cart, db)
    subtotal, item_count = cart_totals(lines)
    response = JSONResponse({"cart_count": item_count, "subtotal": subtotal})
    write_cart(response, new_cart)
    return response


@router.post("/api/cart/add")
def api_add_to_cart(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None or not product.active:
        return JSONResponse({"detail": "This product is no longer available."}, status_code=404)
    if product.stock <= 0:
        return JSONResponse({"detail": "This product is out of stock."}, status_code=400)

    cart = read_cart(request)
    key = str(product_id)
    cart[key] = min(cart.get(key, 0) + max(quantity, 1), 20, product.stock)
    return _respond(request, "/cart", cart, db)


@router.post("/api/cart/update")
def api_update_cart(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(...),
    db: Session = Depends(get_db),
):
    cart = read_cart(request)
    key = str(product_id)
    if quantity <= 0:
        cart.pop(key, None)
    else:
        cart[key] = min(quantity, 20)
    return _respond(request, "/cart", cart, db)


@router.post("/api/cart/remove")
def api_remove_from_cart(request: Request, product_id: int = Form(...), db: Session = Depends(get_db)):
    cart = read_cart(request)
    cart.pop(str(product_id), None)
    return _respond(request, "/cart", cart, db)


@router.post("/api/cart/clear")
def api_clear_cart(request: Request):
    response = RedirectResponse(url="/cart", status_code=303)
    clear_cart(response)
    return response
