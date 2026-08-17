from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.analytics import log_event
from app.cart_service import (
    cart_totals,
    clear_cart,
    get_cart_lines,
    lines_for_cart,
    read_cart,
    write_cart,
)
from app.customer_auth import get_current_customer
from app.database import get_db
from app.models import Product
from app.settings_service import compute_delivery_charge, get_all_settings
from app.storage import UploadValidationError, save_personalization_photo
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
        store_values = get_all_settings(db)
        current_customer = get_current_customer(request, db)
        cart_whatsapp_link = whatsapp_chat_link(
            store_values.get("whatsapp_number", ""),
            cart_enquiry_message(
                lines,
                subtotal,
                total,
                template=store_values.get("whatsapp_cart_template", ""),
                customer_name=current_customer.name if current_customer else "",
            ),
        )

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


def _respond(request: Request, redirect_to: str, new_cart: dict[str, dict], db: Session):
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
async def api_add_to_cart(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
    personalization_name: str = Form(""),
    personalization_message: str = Form(""),
    personalization_date: str = Form(""),
    personalization_photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None or not product.active or product.deleted_at is not None:
        return JSONResponse({"detail": "This product is no longer available."}, status_code=404)
    if product.stock <= 0:
        return JSONResponse({"detail": "This product is out of stock."}, status_code=400)

    personalization = None
    if product.is_personalizable:
        # Never trust the client to have enforced "required" on the fields
        # this specific product actually asks for — re-check server-side.
        has_photo = personalization_photo is not None and bool(personalization_photo.filename)
        missing = []
        if product.personalize_name and not personalization_name.strip():
            missing.append("name")
        if product.personalize_message and not personalization_message.strip():
            missing.append("message")
        if product.personalize_date and not personalization_date.strip():
            missing.append("date")
        if product.personalize_photo and not has_photo:
            missing.append("photo")
        if missing:
            return JSONResponse(
                {"detail": f"Please fill in the personalization {', '.join(missing)} before adding this to your cart."},
                status_code=400,
            )

        personalization = {}
        if product.personalize_name:
            personalization["name"] = personalization_name.strip()[:120]
        if product.personalize_message:
            personalization["message"] = personalization_message.strip()[:300]
        if product.personalize_date:
            personalization["date"] = personalization_date.strip()[:60]
        if product.personalize_photo and has_photo:
            try:
                personalization["photo"] = save_personalization_photo(personalization_photo)
            except UploadValidationError as exc:
                return JSONResponse({"detail": exc.detail}, status_code=400)

    cart = read_cart(request)
    key = str(product_id)
    if personalization:
        cart[key] = {"qty": 1, "p": personalization}
    else:
        current_qty = cart.get(key, {}).get("qty", 0)
        cart[key] = {"qty": min(current_qty + max(quantity, 1), 20, product.stock), "p": None}
    log_event(db, product_id, "add_to_cart")
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
        existing = cart.get(key, {"qty": 0, "p": None})
        max_qty = 1 if existing.get("p") else 20
        cart[key] = {"qty": min(quantity, max_qty), "p": existing.get("p")}
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
