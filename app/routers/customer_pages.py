from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.analytics import log_event
from app.customer_auth import get_current_customer
from app.database import get_db
from app.models import Category, Collection, Product, Review
from app.product_query import apply_filters, apply_sort, base_active_query, paginate
from app.settings_service import get_all_settings
from app.templating import render

router = APIRouter()


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    featured = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .filter(Product.featured.is_(True))
        .order_by(Product.created_at.desc())
        .limit(10)
        .all()
    )
    new_arrivals = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .filter(Product.new_arrival.is_(True))
        .order_by(Product.created_at.desc())
        .limit(10)
        .all()
    )
    bestsellers = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .filter(Product.bestseller.is_(True))
        .order_by(Product.created_at.desc())
        .limit(10)
        .all()
    )
    special_offers = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .filter(Product.original_price.isnot(None), Product.original_price > Product.price)
        .order_by(Product.created_at.desc())
        .limit(10)
        .all()
    )
    categories = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()

    # Only collections that currently have at least one visible product are
    # shown — an empty or all-inactive collection just silently disappears
    # from the homepage rather than linking to a blank page. Occasion and
    # age-group collections share the same underlying table, split by `kind`.
    def _visible_collections(kind: str):
        return (
            db.query(Collection)
            .join(Collection.products)
            .filter(
                Collection.kind == kind,
                Collection.active.is_(True),
                Product.active.is_(True),
                Product.deleted_at.is_(None),
            )
            .order_by(Collection.sort_order, Collection.name)
            .distinct()
            .all()
        )

    occasions = _visible_collections("occasion")
    age_groups = _visible_collections("age")

    has_any_products = base_active_query(db).count() > 0

    return render(
        request,
        "customer/home.html",
        {
            "featured": featured,
            "new_arrivals": new_arrivals,
            "bestsellers": bestsellers,
            "special_offers": special_offers,
            "categories": categories,
            "occasions": occasions,
            "age_groups": age_groups,
            "has_any_products": has_any_products,
        },
        db,
    )


@router.get("/shop")
def shop(
    request: Request,
    q: str = "",
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock: bool = False,
    sort: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    category_obj = None
    category_id = None
    if category:
        category_obj = db.query(Category).filter(Category.slug == category).first()
        category_id = category_obj.id if category_obj else -1  # -1 -> no results, not an error

    query = base_active_query(db).options(joinedload(Product.images))
    query = apply_filters(
        query,
        category_id=category_id,
        min_price=min_price,
        max_price=max_price,
        in_stock_only=in_stock,
        search=q or None,
    )
    query = apply_sort(query, sort)
    items, total, total_pages, page = paginate(query, page)

    categories = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()

    return render(
        request,
        "customer/shop.html",
        {
            "products": items,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "categories": categories,
            "current_category": category_obj,
            "filters": {
                "q": q or "",
                "category": category or "",
                "min_price": min_price,
                "max_price": max_price,
                "in_stock": in_stock,
                "sort": sort or "",
            },
            "has_active_filters": bool(q or category or min_price or max_price or in_stock or sort),
            "page_title": category_obj.name if category_obj else "All Products",
        },
        db,
    )


@router.get("/category/{slug}")
def category_page(slug: str, request: Request, page: int = 1, sort: str | None = None, db: Session = Depends(get_db)):
    category_obj = db.query(Category).filter(Category.slug == slug, Category.active.is_(True)).first()
    if category_obj is None:
        query = base_active_query(db).filter(Product.id == -1)
        items, total, total_pages, page = paginate(query, page)
        categories = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()
        return render(
            request,
            "customer/shop.html",
            {
                "products": items,
                "total": total,
                "total_pages": total_pages,
                "page": page,
                "categories": categories,
                "current_category": None,
                "filters": {"q": "", "category": slug, "min_price": None, "max_price": None, "in_stock": False, "sort": ""},
                "has_active_filters": False,
                "page_title": "Category Not Found",
            },
            db,
            status_code=404,
        )

    query = base_active_query(db).options(joinedload(Product.images)).filter(Product.category_id == category_obj.id)
    query = apply_sort(query, sort)
    items, total, total_pages, page = paginate(query, page)
    categories = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()

    return render(
        request,
        "customer/shop.html",
        {
            "products": items,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "categories": categories,
            "current_category": category_obj,
            "filters": {"q": "", "category": slug, "min_price": None, "max_price": None, "in_stock": False, "sort": sort or ""},
            "has_active_filters": bool(sort),
            "page_title": category_obj.name,
        },
        db,
    )


def _collection_detail_page(
    slug: str, request: Request, page: int, sort: str | None, db: Session, kind: str, not_found_title: str
):
    collection_obj = (
        db.query(Collection)
        .filter(Collection.slug == slug, Collection.active.is_(True), Collection.kind == kind)
        .first()
    )
    categories = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()

    if collection_obj is None:
        query = base_active_query(db).filter(Product.id == -1)
        items, total, total_pages, page = paginate(query, page)
        return render(
            request,
            "customer/shop.html",
            {
                "products": items,
                "total": total,
                "total_pages": total_pages,
                "page": page,
                "categories": categories,
                "current_category": None,
                "filters": {"q": "", "category": "", "min_price": None, "max_price": None, "in_stock": False, "sort": ""},
                "has_active_filters": False,
                "page_title": not_found_title,
            },
            db,
            status_code=404,
        )

    query = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .join(Product.collections)
        .filter(Collection.id == collection_obj.id)
    )
    query = apply_sort(query, sort)
    items, total, total_pages, page = paginate(query, page)

    return render(
        request,
        "customer/shop.html",
        {
            "products": items,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "categories": categories,
            "current_category": None,
            "filters": {"q": "", "category": "", "min_price": None, "max_price": None, "in_stock": False, "sort": sort or ""},
            "has_active_filters": bool(sort),
            "page_title": collection_obj.name,
            "page_description": collection_obj.description,
        },
        db,
    )


@router.get("/occasion/{slug}")
def occasion_page(slug: str, request: Request, page: int = 1, sort: str | None = None, db: Session = Depends(get_db)):
    return _collection_detail_page(slug, request, page, sort, db, kind="occasion", not_found_title="Collection Not Found")


@router.get("/age/{slug}")
def age_group_page(slug: str, request: Request, page: int = 1, sort: str | None = None, db: Session = Depends(get_db)):
    return _collection_detail_page(slug, request, page, sort, db, kind="age", not_found_title="Age Group Not Found")


@router.get("/search")
def search(request: Request, q: str = "", page: int = 1, db: Session = Depends(get_db)):
    q = (q or "").strip()
    if not q:
        return render(request, "customer/search.html", {"products": [], "query": q, "total": 0, "total_pages": 1, "page": 1}, db)

    query = base_active_query(db).options(joinedload(Product.images))
    query = apply_filters(query, search=q)
    query = apply_sort(query, None)
    items, total, total_pages, page = paginate(query, page)

    return render(
        request,
        "customer/search.html",
        {"products": items, "query": q, "total": total, "total_pages": total_pages, "page": page},
        db,
    )


@router.get("/product/{slug}")
def product_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    from app.utils.whatsapp import product_enquiry_message, whatsapp_chat_link

    product = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.category), joinedload(Product.collections))
        .filter(Product.slug == slug)
        .first()
    )
    if product is None or not product.active or product.deleted_at is not None:
        return render(request, "errors/404.html", {}, db, status_code=404)

    log_event(db, product.id, "view")

    # Cast a wider net than "same category alone": same collection (occasion)
    # or a similar price band both count as related too, then rank so the
    # closest matches (same category, closest price) surface first — all in
    # one query rather than several separate lookups merged in Python.
    collection_ids = [c.id for c in product.collections]
    price_low, price_high = product.price * 0.5, product.price * 1.5
    relatedness_conditions = [Product.category_id == product.category_id, Product.price.between(price_low, price_high)]
    if collection_ids:
        relatedness_conditions.append(Product.collections.any(Collection.id.in_(collection_ids)))

    related = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .filter(Product.id != product.id, or_(*relatedness_conditions))
        .order_by(
            (Product.category_id != product.category_id),
            func.abs(Product.price - product.price),
        )
        .limit(6)
        .all()
    )

    store_values = get_all_settings(db)
    whatsapp_number = store_values.get("whatsapp_number", "")
    current_customer = get_current_customer(request, db)
    product_whatsapp_link = whatsapp_chat_link(
        whatsapp_number,
        product_enquiry_message(
            product,
            template=store_values.get("whatsapp_product_template", ""),
            customer_name=current_customer.name if current_customer else "",
            product_url=str(request.url_for("product_detail", slug=product.slug)),
        ),
    )

    approved_reviews = (
        db.query(Review)
        .options(joinedload(Review.customer))
        .filter(Review.product_id == product.id, Review.approved.is_(True))
        .order_by(Review.created_at.desc())
        .all()
    )
    review_count = len(approved_reviews)
    average_rating = round(sum(r.rating for r in approved_reviews) / review_count, 1) if review_count else 0

    my_review = None
    if current_customer is not None:
        my_review = (
            db.query(Review)
            .filter(Review.product_id == product.id, Review.customer_id == current_customer.id)
            .first()
        )

    return render(
        request,
        "customer/product_detail.html",
        {
            "product": product,
            "related": related,
            "product_whatsapp_link": product_whatsapp_link,
            "approved_reviews": approved_reviews,
            "review_count": review_count,
            "average_rating": average_rating,
            "my_review": my_review,
            "review_submitted": request.query_params.get("review") == "submitted",
        },
        db,
    )


@router.post("/api/track/enquiry/{product_id}")
def track_enquiry_click(product_id: int, db: Session = Depends(get_db)):
    """Fired by a beacon when a customer clicks "Enquire on WhatsApp" — that
    click opens wa.me in a new tab, so there's no server round trip to hook
    into otherwise."""
    log_event(db, product_id, "enquiry")
    return JSONResponse({"ok": True})


@router.get("/about")
def about_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "customer/about.html", {}, db)


@router.get("/contact")
def contact_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "customer/contact.html", {}, db)


@router.get("/privacy-policy")
def privacy_policy(request: Request, db: Session = Depends(get_db)):
    return render(request, "customer/legal.html", {"page_title": "Privacy Policy", "policy": "privacy"}, db)


@router.get("/terms")
def terms_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "customer/legal.html", {"page_title": "Terms & Conditions", "policy": "terms"}, db)


@router.get("/shipping-policy")
def shipping_policy(request: Request, db: Session = Depends(get_db)):
    return render(request, "customer/legal.html", {"page_title": "Shipping Policy", "policy": "shipping"}, db)


@router.get("/refund-policy")
def refund_policy(request: Request, db: Session = Depends(get_db)):
    return render(request, "customer/legal.html", {"page_title": "Cancellation / Refund Policy", "policy": "refund"}, db)


@router.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    static_paths = ["/", "/shop", "/about", "/contact", "/privacy-policy", "/terms", "/shipping-policy", "/refund-policy"]
    products = db.query(Product.slug).filter(Product.active.is_(True), Product.deleted_at.is_(None)).all()
    categories = db.query(Category.slug).filter(Category.active.is_(True)).all()
    occasions = db.query(Collection.slug).filter(Collection.active.is_(True), Collection.kind == "occasion").all()
    age_groups = db.query(Collection.slug).filter(Collection.active.is_(True), Collection.kind == "age").all()

    urls = [f"{base_url}{p}" for p in static_paths]
    urls += [f"{base_url}/product/{slug}" for (slug,) in products]
    urls += [f"{base_url}/category/{slug}" for (slug,) in categories]
    urls += [f"{base_url}/occasion/{slug}" for (slug,) in occasions]
    urls += [f"{base_url}/age/{slug}" for (slug,) in age_groups]

    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(content=xml, media_type="application/xml")
