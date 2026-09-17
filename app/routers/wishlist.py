from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from app.customer_auth import get_current_customer, require_customer
from app.database import get_db
from app.models import Customer, Product, Wishlist
from app.templating import render

router = APIRouter()


@router.get("/api/wishlist/ids")
def wishlist_ids(request: Request, db: Session = Depends(get_db)):
    customer = get_current_customer(request, db)
    if customer is None:
        return JSONResponse({"ids": []})
    ids = [w.product_id for w in db.query(Wishlist).filter(Wishlist.customer_id == customer.id).all()]
    return JSONResponse({"ids": ids})


@router.post("/api/wishlist/toggle")
def wishlist_toggle(
    request: Request,
    product_id: int = Form(...),
    db: Session = Depends(get_db),
):
    customer = get_current_customer(request, db)
    if customer is None:
        return JSONResponse({"detail": "Please log in to save items to your wishlist."}, status_code=401)

    product = db.get(Product, product_id)
    if product is None:
        return JSONResponse({"detail": "Product not found."}, status_code=404)

    existing = (
        db.query(Wishlist)
        .filter(Wishlist.customer_id == customer.id, Wishlist.product_id == product_id)
        .first()
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        return JSONResponse({"in_wishlist": False})

    db.add(Wishlist(customer_id=customer.id, product_id=product_id))
    db.commit()
    return JSONResponse({"in_wishlist": True})


@router.get("/wishlist")
def wishlist_page(request: Request, db: Session = Depends(get_db), customer: Customer = Depends(require_customer)):
    items = (
        db.query(Wishlist)
        .options(joinedload(Wishlist.product).joinedload(Product.images))
        .filter(Wishlist.customer_id == customer.id)
        .order_by(Wishlist.created_at.desc())
        .all()
    )
    products = [w.product for w in items if w.product is not None and w.product.active and w.product.deleted_at is None]
    return render(request, "customer/wishlist.html", {"products": products}, db)
