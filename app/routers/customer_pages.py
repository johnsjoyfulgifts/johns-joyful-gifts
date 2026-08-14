from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Category, Product
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
    categories = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()

    has_any_products = base_active_query(db).count() > 0

    return render(
        request,
        "customer/home.html",
        {
            "featured": featured,
            "new_arrivals": new_arrivals,
            "bestsellers": bestsellers,
            "categories": categories,
            "has_any_products": has_any_products,
        },
        db,
    )


@router.get("/shop")
def shop(
    request: Request,
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
    query = apply_filters(query, category_id=category_id, min_price=min_price, max_price=max_price, in_stock_only=in_stock)
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
                "category": category or "",
                "min_price": min_price,
                "max_price": max_price,
                "in_stock": in_stock,
                "sort": sort or "",
            },
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
                "filters": {"category": slug, "min_price": None, "max_price": None, "in_stock": False, "sort": ""},
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
            "filters": {"category": slug, "min_price": None, "max_price": None, "in_stock": False, "sort": sort or ""},
            "page_title": category_obj.name,
        },
        db,
    )


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
        .options(joinedload(Product.images), joinedload(Product.category))
        .filter(Product.slug == slug)
        .first()
    )
    if product is None or not product.active:
        return render(request, "errors/404.html", {}, db, status_code=404)

    related = (
        base_active_query(db)
        .options(joinedload(Product.images))
        .filter(Product.category_id == product.category_id, Product.id != product.id)
        .limit(6)
        .all()
    )

    whatsapp_number = get_all_settings(db).get("whatsapp_number", "")
    product_whatsapp_link = whatsapp_chat_link(whatsapp_number, product_enquiry_message(product))

    return render(
        request,
        "customer/product_detail.html",
        {"product": product, "related": related, "product_whatsapp_link": product_whatsapp_link},
        db,
    )


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
    products = db.query(Product.slug).filter(Product.active.is_(True)).all()
    categories = db.query(Category.slug).filter(Category.active.is_(True)).all()

    urls = [f"{base_url}{p}" for p in static_paths]
    urls += [f"{base_url}/product/{slug}" for (slug,) in products]
    urls += [f"{base_url}/category/{slug}" for (slug,) in categories]

    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(content=xml, media_type="application/xml")
