from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Product

PAGE_SIZE = 20


def base_active_query(db: Session):
    return db.query(Product).filter(Product.active.is_(True))


def apply_filters(query, category_id=None, min_price=None, max_price=None, in_stock_only=False, search=None):
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)
    if in_stock_only:
        query = query.filter(Product.stock > 0)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(Product.name.ilike(like), Product.short_description.ilike(like), Product.description.ilike(like)))
    return query


def apply_sort(query, sort: str | None):
    if sort == "price_asc":
        return query.order_by(Product.price.asc())
    if sort == "price_desc":
        return query.order_by(Product.price.desc())
    if sort == "newest":
        return query.order_by(Product.created_at.desc())
    return query.order_by(Product.featured.desc(), Product.created_at.desc())


def paginate(query, page: int, page_size: int = PAGE_SIZE):
    page = max(page, 1)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    total_pages = max((total + page_size - 1) // page_size, 1)
    return items, total, total_pages, page
