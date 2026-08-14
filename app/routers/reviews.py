from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.customer_auth import get_current_customer
from app.database import get_db
from app.models import Product, Review

router = APIRouter()


@router.post("/product/{slug}/review")
def submit_review(
    slug: str,
    request: Request,
    rating: int = Form(...),
    review_text: str = Form(""),
    db: Session = Depends(get_db),
):
    customer = get_current_customer(request, db)
    if customer is None:
        return RedirectResponse(url=f"/login?next=/product/{slug}", status_code=303)

    product = db.query(Product).filter(Product.slug == slug).first()
    if product is None:
        return RedirectResponse(url="/shop", status_code=303)

    rating = max(1, min(5, rating))
    review_text = review_text.strip() or None

    existing = (
        db.query(Review)
        .filter(Review.product_id == product.id, Review.customer_id == customer.id)
        .first()
    )
    if existing is not None:
        existing.rating = rating
        existing.review_text = review_text
        existing.approved = False
    else:
        db.add(Review(product_id=product.id, customer_id=customer.id, rating=rating, review_text=review_text, approved=False))
    db.commit()

    return RedirectResponse(url=f"/product/{slug}?review=submitted", status_code=303)
