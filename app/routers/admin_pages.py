from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi import File as FastAPIFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.auth import (
    clear_session_cookie,
    get_current_admin,
    require_admin,
    set_session_cookie,
    verify_password,
)
from app.database import get_db
from app.rate_limit import is_rate_limited
from app.models import (
    Admin,
    Category,
    Customer,
    Order,
    OrderStatus,
    OrderStatusHistory,
    Product,
    ProductImage,
)
from app.settings_service import DEFAULTS, get_all_settings, set_settings
from app.storage import UploadValidationError, delete_product_image, save_product_image
from app.templating import render_admin
from app.utils.slugs import unique_slug

router = APIRouter(prefix="/admin")

ORDER_STATUSES = [s.value for s in OrderStatus]


# ---------- Auth ----------

@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_admin(request, db) is not None:
        return RedirectResponse(url="/admin", status_code=303)
    return render_admin(request, "admin/login.html", {}, db)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(f"login:{client_ip}"):
        return render_admin(
            request,
            "admin/login.html",
            {"error": "Too many login attempts. Please wait a few minutes and try again.", "email": email},
            db,
            status_code=429,
        )

    admin = db.query(Admin).filter(Admin.email == email.strip().lower()).first()
    if admin is None or not verify_password(password, admin.password_hash):
        return render_admin(
            request, "admin/login.html", {"error": "Incorrect email or password.", "email": email}, db, status_code=401
        )
    response = RedirectResponse(url="/admin", status_code=303)
    set_session_cookie(response, admin.id)
    return response


@router.get("/logout")
def logout(request: Request):
    response = RedirectResponse(url="/admin/login", status_code=303)
    clear_session_cookie(response)
    return response


# ---------- Dashboard ----------

@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    counts_by_status = dict(db.query(Order.order_status, func.count(Order.id)).group_by(Order.order_status).all())
    total_orders = sum(counts_by_status.values())
    total_sales = db.query(func.coalesce(func.sum(Order.total), 0)).filter(Order.order_status != OrderStatus.CANCELLED.value).scalar()
    low_stock = db.query(Product).filter(Product.active.is_(True), Product.stock <= 5, Product.stock > 0).order_by(Product.stock).limit(8).all()
    out_of_stock_count = db.query(Product).filter(Product.active.is_(True), Product.stock <= 0).count()
    new_order_count = db.query(Order).filter(Order.viewed_by_admin.is_(False)).count()
    recent_orders = db.query(Order).options(joinedload(Order.customer)).order_by(Order.created_at.desc()).limit(10).all()

    return render_admin(
        request,
        "admin/dashboard.html",
        {
            "active_nav": "dashboard",
            "total_orders": total_orders,
            "new_order_count": new_order_count,
            "processing_count": counts_by_status.get(OrderStatus.PROCESSING.value, 0),
            "shipped_count": counts_by_status.get(OrderStatus.SHIPPED.value, 0),
            "delivered_count": counts_by_status.get(OrderStatus.DELIVERED.value, 0),
            "cancelled_count": counts_by_status.get(OrderStatus.CANCELLED.value, 0),
            "total_sales": total_sales,
            "low_stock": low_stock,
            "out_of_stock_count": out_of_stock_count,
            "recent_orders": recent_orders,
        },
        db,
    )


# ---------- Categories ----------

@router.get("/categories")
def categories_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    categories = db.query(Category).order_by(Category.sort_order, Category.name).all()
    return render_admin(request, "admin/categories_list.html", {"active_nav": "categories", "categories": categories}, db)


@router.get("/categories/new")
def category_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    return render_admin(request, "admin/category_form.html", {"active_nav": "categories", "category": None}, db)


@router.post("/categories/new")
def category_new_submit(
    request: Request,
    name: str = Form(...),
    sort_order: int = Form(0),
    active: bool = Form(False),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    name = name.strip()
    if not name:
        return render_admin(
            request, "admin/category_form.html", {"active_nav": "categories", "category": None, "error": "Name is required."}, db, status_code=400
        )

    image_url = None
    if image is not None and image.filename:
        try:
            image_url = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(
                request, "admin/category_form.html", {"active_nav": "categories", "category": None, "error": exc.detail}, db, status_code=400
            )

    category = Category(name=name, slug=unique_slug(db, Category, name), sort_order=sort_order, active=active, image=image_url)
    db.add(category)
    db.commit()
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.get("/categories/{category_id}/edit")
def category_edit_page(category_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    category = db.get(Category, category_id)
    if category is None:
        return RedirectResponse(url="/admin/categories", status_code=303)
    return render_admin(request, "admin/category_form.html", {"active_nav": "categories", "category": category}, db)


@router.post("/categories/{category_id}/edit")
def category_edit_submit(
    category_id: int,
    request: Request,
    name: str = Form(...),
    sort_order: int = Form(0),
    active: bool = Form(False),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    category = db.get(Category, category_id)
    if category is None:
        return RedirectResponse(url="/admin/categories", status_code=303)

    name = name.strip()
    if not name:
        return render_admin(
            request, "admin/category_form.html", {"active_nav": "categories", "category": category, "error": "Name is required."}, db, status_code=400
        )

    if image is not None and image.filename:
        try:
            new_image_url = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(
                request, "admin/category_form.html", {"active_nav": "categories", "category": category, "error": exc.detail}, db, status_code=400
            )
        if category.image:
            delete_product_image(category.image)
        category.image = new_image_url

    if name != category.name:
        category.slug = unique_slug(db, Category, name, exclude_id=category.id)
    category.name = name
    category.sort_order = sort_order
    category.active = active
    db.commit()
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.post("/categories/{category_id}/delete")
def category_delete(category_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    category = db.get(Category, category_id)
    if category is not None:
        db.delete(category)
        db.commit()
    return RedirectResponse(url="/admin/categories", status_code=303)


# ---------- Products ----------

@router.get("/products")
def products_list(
    request: Request,
    q: str = "",
    category: int | None = None,
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    query = db.query(Product)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(Product.name.ilike(like))
    if category:
        query = query.filter(Product.category_id == category)
    if status == "active":
        query = query.filter(Product.active.is_(True))
    elif status == "inactive":
        query = query.filter(Product.active.is_(False))
    elif status == "out_of_stock":
        query = query.filter(Product.stock <= 0)

    page = max(page, 1)
    page_size = 20
    total = query.count()
    products = query.order_by(Product.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    total_pages = max((total + page_size - 1) // page_size, 1)

    categories = db.query(Category).order_by(Category.name).all()

    return render_admin(
        request,
        "admin/products_list.html",
        {
            "active_nav": "products",
            "products": products,
            "categories": categories,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "q": q,
            "selected_category": category,
            "status": status,
        },
        db,
    )


def _product_form_context(db: Session, product=None, error=None) -> dict:
    return {
        "active_nav": "products",
        "product": product,
        "categories": db.query(Category).order_by(Category.name).all(),
        "error": error,
    }


@router.get("/products/new")
def product_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    return render_admin(request, "admin/product_form.html", _product_form_context(db), db)


@router.post("/products/new")
async def product_new_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    short_description: str = Form(""),
    category_id: str = Form(""),
    price: float = Form(...),
    original_price: str = Form(""),
    stock: int = Form(0),
    sku: str = Form(""),
    featured: bool = Form(False),
    bestseller: bool = Form(False),
    new_arrival: bool = Form(False),
    active: bool = Form(True),
    images: list[UploadFile] = FastAPIFile(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    name = name.strip()
    error = None
    if not name:
        error = "Product name is required."
    elif price < 0:
        error = "Price cannot be negative."
    elif stock < 0:
        error = "Stock cannot be negative."

    if error:
        return render_admin(request, "admin/product_form.html", _product_form_context(db, error=error), db, status_code=400)

    saved_urls = []
    for image in images:
        if image is not None and image.filename:
            try:
                saved_urls.append(save_product_image(image))
            except UploadValidationError as exc:
                for url in saved_urls:
                    delete_product_image(url)
                return render_admin(request, "admin/product_form.html", _product_form_context(db, error=exc.detail), db, status_code=400)

    product = Product(
        name=name,
        slug=unique_slug(db, Product, name),
        description=description or None,
        short_description=short_description or None,
        category_id=int(category_id) if category_id else None,
        price=price,
        original_price=float(original_price) if original_price else None,
        stock=stock,
        sku=sku or None,
        featured=featured,
        bestseller=bestseller,
        new_arrival=new_arrival,
        active=active,
    )
    db.add(product)
    db.flush()

    for idx, url in enumerate(saved_urls):
        db.add(ProductImage(product_id=product.id, image_url=url, sort_order=idx))

    db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/products/{product_id}/edit")
def product_edit_page(product_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    product = db.query(Product).options(joinedload(Product.images)).filter(Product.id == product_id).first()
    if product is None:
        return RedirectResponse(url="/admin/products", status_code=303)
    return render_admin(request, "admin/product_form.html", _product_form_context(db, product=product), db)


@router.post("/products/{product_id}/edit")
async def product_edit_submit(
    product_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    short_description: str = Form(""),
    category_id: str = Form(""),
    price: float = Form(...),
    original_price: str = Form(""),
    stock: int = Form(0),
    sku: str = Form(""),
    featured: bool = Form(False),
    bestseller: bool = Form(False),
    new_arrival: bool = Form(False),
    active: bool = Form(True),
    delete_image_ids: list[int] = Form(default=[]),
    images: list[UploadFile] = FastAPIFile(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    product = db.query(Product).options(joinedload(Product.images)).filter(Product.id == product_id).first()
    if product is None:
        return RedirectResponse(url="/admin/products", status_code=303)

    name = name.strip()
    error = None
    if not name:
        error = "Product name is required."
    elif price < 0:
        error = "Price cannot be negative."
    elif stock < 0:
        error = "Stock cannot be negative."

    if error:
        return render_admin(request, "admin/product_form.html", _product_form_context(db, product=product, error=error), db, status_code=400)

    for img in list(product.images):
        if img.id in delete_image_ids:
            delete_product_image(img.image_url)
            db.delete(img)

    max_sort = max([img.sort_order for img in product.images if img.id not in delete_image_ids], default=-1)
    for image in images:
        if image is not None and image.filename:
            try:
                url = save_product_image(image)
            except UploadValidationError as exc:
                db.rollback()
                return render_admin(request, "admin/product_form.html", _product_form_context(db, product=product, error=exc.detail), db, status_code=400)
            max_sort += 1
            db.add(ProductImage(product_id=product.id, image_url=url, sort_order=max_sort))

    if name != product.name:
        product.slug = unique_slug(db, Product, name, exclude_id=product.id)
    product.name = name
    product.description = description or None
    product.short_description = short_description or None
    product.category_id = int(category_id) if category_id else None
    product.price = price
    product.original_price = float(original_price) if original_price else None
    product.stock = stock
    product.sku = sku or None
    product.featured = featured
    product.bestseller = bestseller
    product.new_arrival = new_arrival
    product.active = active

    db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
def product_delete(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    product = db.query(Product).options(joinedload(Product.images)).filter(Product.id == product_id).first()
    if product is not None:
        for img in product.images:
            delete_product_image(img.image_url)
        db.delete(product)
        db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/products/{product_id}/duplicate")
def product_duplicate(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    original = db.query(Product).options(joinedload(Product.images)).filter(Product.id == product_id).first()
    if original is None:
        return RedirectResponse(url="/admin/products", status_code=303)

    copy_name = f"{original.name} (Copy)"
    duplicate = Product(
        name=copy_name,
        slug=unique_slug(db, Product, copy_name),
        description=original.description,
        short_description=original.short_description,
        category_id=original.category_id,
        price=original.price,
        original_price=original.original_price,
        stock=0,
        sku=None,
        featured=False,
        bestseller=False,
        new_arrival=original.new_arrival,
        active=False,
    )
    db.add(duplicate)
    db.flush()
    for img in original.images:
        db.add(ProductImage(product_id=duplicate.id, image_url=img.image_url, sort_order=img.sort_order))
    db.commit()
    return RedirectResponse(url=f"/admin/products/{duplicate.id}/edit", status_code=303)


@router.post("/products/{product_id}/toggle-active")
def product_toggle_active(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    product = db.get(Product, product_id)
    if product is not None:
        product.active = not product.active
        db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


# ---------- Settings ----------

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    values = get_all_settings(db)
    return render_admin(request, "admin/settings.html", {"active_nav": "settings", "values": values}, db)


@router.post("/settings")
def settings_submit(
    request: Request,
    store_name: str = Form(...),
    store_tagline: str = Form(""),
    whatsapp_number: str = Form(...),
    instagram_url: str = Form(""),
    delivery_mode: str = Form("flat"),
    flat_delivery_charge: str = Form("0"),
    free_delivery_threshold: str = Form("0"),
    about_text: str = Form(""),
    contact_email: str = Form(""),
    contact_address: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    set_settings(
        db,
        {
            "store_name": store_name.strip() or DEFAULTS["store_name"],
            "store_tagline": store_tagline.strip(),
            "whatsapp_number": whatsapp_number.strip(),
            "instagram_url": instagram_url.strip(),
            "delivery_mode": delivery_mode if delivery_mode in ("flat", "free", "disabled") else "flat",
            "flat_delivery_charge": flat_delivery_charge or "0",
            "free_delivery_threshold": free_delivery_threshold or "0",
            "about_text": about_text,
            "contact_email": contact_email.strip(),
            "contact_address": contact_address.strip(),
        },
    )
    return RedirectResponse(url="/admin/settings", status_code=303)


# ---------- Orders ----------

@router.get("/orders")
def orders_list(
    request: Request,
    q: str = "",
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    query = db.query(Order).options(joinedload(Order.customer))
    if status:
        query = query.filter(Order.order_status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(Order.customer).filter(
            or_(Order.order_number.ilike(like), Customer.name.ilike(like), Customer.mobile.ilike(like))
        )

    page = max(page, 1)
    page_size = 20
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    total_pages = max((total + page_size - 1) // page_size, 1)

    return render_admin(
        request,
        "admin/orders_list.html",
        {
            "active_nav": "orders",
            "orders": orders,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "q": q,
            "status": status,
            "statuses": ORDER_STATUSES,
        },
        db,
    )


@router.get("/orders/{order_number}")
def order_detail(order_number: str, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    order = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.customer), joinedload(Order.status_history))
        .filter(Order.order_number == order_number)
        .first()
    )
    if order is None:
        return RedirectResponse(url="/admin/orders", status_code=303)

    if not order.viewed_by_admin:
        order.viewed_by_admin = True
        db.commit()

    return render_admin(request, "admin/order_detail.html", {"active_nav": "orders", "order": order, "statuses": ORDER_STATUSES}, db)


@router.post("/orders/{order_number}/status")
def order_update_status(
    order_number: str,
    status: str = Form(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if order is not None and status in ORDER_STATUSES and status != order.order_status:
        order.order_status = status
        db.add(OrderStatusHistory(order_id=order.id, status=status))
        db.commit()
    return RedirectResponse(url=f"/admin/orders/{order_number}", status_code=303)


@router.post("/orders/{order_number}/courier")
def order_update_courier(
    order_number: str,
    courier_name: str = Form(""),
    tracking_id: str = Form(""),
    tracking_url: str = Form(""),
    estimated_delivery: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if order is not None:
        order.courier_name = courier_name.strip() or None
        order.tracking_id = tracking_id.strip() or None
        order.tracking_url = tracking_url.strip() or None
        order.estimated_delivery = estimated_delivery.strip() or None
        order.notes = notes.strip() or None
        db.commit()
    return RedirectResponse(url=f"/admin/orders/{order_number}", status_code=303)
