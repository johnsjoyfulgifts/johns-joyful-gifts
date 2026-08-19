from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.cart_service import read_cart, write_cart
from app.database import get_db
from app.models import ProductBundle
from app.templating import render

router = APIRouter()


@router.get("/combos")
def combos_list(request: Request, db: Session = Depends(get_db)):
    bundles = (
        db.query(ProductBundle)
        .options(joinedload(ProductBundle.items))
        .filter(ProductBundle.active.is_(True))
        .order_by(ProductBundle.sort_order, ProductBundle.name)
        .all()
    )
    bundles = [b for b in bundles if len(b.items) >= 2]
    return render(request, "customer/combos.html", {"bundles": bundles}, db)


@router.get("/combo/{slug}")
def combo_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    bundle = (
        db.query(ProductBundle)
        .options(joinedload(ProductBundle.items))
        .filter(ProductBundle.slug == slug, ProductBundle.active.is_(True))
        .first()
    )
    if bundle is None:
        return RedirectResponse(url="/combos", status_code=303)
    return render(request, "customer/combo_detail.html", {"bundle": bundle}, db)


@router.post("/combo/{slug}/add-all")
def combo_add_all(slug: str, request: Request, db: Session = Depends(get_db)):
    bundle = (
        db.query(ProductBundle)
        .options(joinedload(ProductBundle.items))
        .filter(ProductBundle.slug == slug, ProductBundle.active.is_(True))
        .first()
    )
    if bundle is None:
        return RedirectResponse(url="/combos", status_code=303)

    cart = read_cart(request)
    skipped = 0
    for item in bundle.items:
        product = item.product
        if product is None or not product.active or product.deleted_at is not None or product.stock <= 0:
            skipped += 1
            continue
        if product.is_personalizable:
            # Personalization needs the customer's own input (name/message/photo) —
            # can't be auto-filled, so this product is left out of the bulk add.
            skipped += 1
            continue
        key = str(product.id)
        current_qty = cart.get(key, {}).get("qty", 0)
        cart[key] = {"qty": min(current_qty + item.quantity, 20, product.stock), "p": None}

    redirect_url = "/cart" if not skipped else f"/cart?combo_skipped={skipped}"
    response = RedirectResponse(url=redirect_url, status_code=303)
    write_cart(response, cart)
    return response
