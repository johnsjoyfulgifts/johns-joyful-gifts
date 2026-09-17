from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.customer_auth import get_current_customer
from app.database import get_db
from app.models import Product, Review
from app.storage import UploadValidationError, delete_product_image, save_review_photo

router = APIRouter()


@router.post("/product/{slug}/review")
def submit_review(
    slug: str,
    request: Request,
    rating: int = Form(...),
    review_text: str = Form(""),
    photo: UploadFile | None = File(None),
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

    photo_url = None
    if photo is not None and photo.filename:
        try:
            photo_url = save_review_photo(photo)
        except UploadValidationError:
            # Photo upload is a nice-to-have on a review — don't block the
            # review itself over a bad image file.
            photo_url = None

    existing = (
        db.query(Review)
        .filter(Review.product_id == product.id, Review.customer_id == customer.id)
        .first()
    )
    if existing is not None:
        existing.rating = rating
        existing.review_text = review_text
        if photo_url:
            if existing.photo_url:
                delete_product_image(existing.photo_url)
            existing.photo_url = photo_url
        existing.approved = False
    else:
        db.add(
            Review(
                product_id=product.id,
                customer_id=customer.id,
                rating=rating,
                review_text=review_text,
                photo_url=photo_url,
                approved=False,
            )
        )
    db.commit()

    return RedirectResponse(url=f"/product/{slug}?review=submitted", status_code=303)
