from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi import File as FastAPIFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.audit import log_activity
from app.auth import (
    ADMIN_ROLES,
    ROLE_LABELS,
    ROLE_ORDER_MANAGER,
    ROLE_PRODUCT_MANAGER,
    ROLE_SUPER_ADMIN,
    clear_session_cookie,
    get_current_admin,
    hash_password,
    require_admin,
    require_role,
    set_session_cookie,
    verify_password,
)
from app.backup_service import build_backup
from app.database import get_db
from app.i18n import SUPPORTED_LANGUAGES
from app.rate_limit import is_rate_limited
from app.models import (
    AbandonedCartLead,
    Admin,
    AuditLog,
    Category,
    Collection,
    Coupon,
    Customer,
    Enquiry,
    GiftOption,
    Invoice,
    Order,
    OrderStatus,
    OrderStatusHistory,
    Product,
    ProductBundle,
    ProductBundleItem,
    ProductEvent,
    ProductImage,
    Quotation,
    Review,
    StockNotifyRequest,
    now_utc,
    product_collections,
)
from app.settings_service import DEFAULTS, get_all_settings, get_setting, set_settings
from app.storage import UploadValidationError, delete_product_image, save_product_image
from app.templating import render_admin
from app.utils.slugs import unique_slug
from app.utils.whatsapp import DEFAULT_CART_TEMPLATE, DEFAULT_PRODUCT_TEMPLATE

router = APIRouter(prefix="/admin")

ORDER_STATUSES = [s.value for s in OrderStatus]
MIN_PRODUCT_IMAGES = 4
MAX_PRODUCT_IMAGES = 6

# Section-scoped access: Super Admin always passes require_role(...)
# regardless of which roles are listed. require_super_admin lists none, so
# only Super Admin gets through.
require_product_admin = require_role(ROLE_PRODUCT_MANAGER)
require_order_admin = require_role(ROLE_ORDER_MANAGER)
require_super_admin = require_role()


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


@router.get("/change-password")
def change_password_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    return render_admin(request, "admin/change_password.html", {"active_nav": "change-password"}, db)


@router.post("/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    error = None
    if not verify_password(current_password, admin.password_hash):
        error = "Current password is incorrect."
    elif len(new_password) < 8:
        error = "New password must be at least 8 characters."
    elif new_password != confirm_password:
        error = "New passwords didn't match."

    if error:
        return render_admin(
            request, "admin/change_password.html", {"active_nav": "change-password", "error": error}, db, status_code=400
        )

    admin.password_hash = hash_password(new_password)
    db.commit()
    return render_admin(
        request,
        "admin/change_password.html",
        {"active_nav": "change-password", "success": "Password updated successfully."},
        db,
    )


# ---------- Dashboard ----------

@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    counts_by_status = dict(db.query(Order.order_status, func.count(Order.id)).group_by(Order.order_status).all())
    total_orders = sum(counts_by_status.values())
    total_sales = db.query(func.coalesce(func.sum(Order.total), 0)).filter(Order.order_status != OrderStatus.CANCELLED.value).scalar()
    low_stock_threshold = int(get_setting(db, "low_stock_threshold") or 5)
    not_deleted = Product.deleted_at.is_(None)
    low_stock = (
        db.query(Product)
        .filter(Product.active.is_(True), not_deleted, Product.stock <= low_stock_threshold, Product.stock > 0)
        .order_by(Product.stock)
        .limit(8)
        .all()
    )
    low_stock_count = (
        db.query(Product)
        .filter(Product.active.is_(True), not_deleted, Product.stock <= low_stock_threshold, Product.stock > 0)
        .count()
    )
    out_of_stock_count = db.query(Product).filter(Product.active.is_(True), not_deleted, Product.stock <= 0).count()
    new_order_count = db.query(Order).filter(Order.viewed_by_admin.is_(False)).count()
    recent_orders = db.query(Order).options(joinedload(Order.customer)).order_by(Order.created_at.desc()).limit(10).all()

    total_products = db.query(Product).filter(not_deleted).count()
    active_products = db.query(Product).filter(Product.active.is_(True), not_deleted).count()
    featured_count = db.query(Product).filter(Product.active.is_(True), not_deleted, Product.featured.is_(True)).count()
    new_arrival_count = db.query(Product).filter(Product.active.is_(True), not_deleted, Product.new_arrival.is_(True)).count()
    sale_count = (
        db.query(Product)
        .filter(Product.active.is_(True), not_deleted, Product.original_price.isnot(None), Product.original_price > Product.price)
        .count()
    )
    pending_review_count = db.query(Review).filter(Review.approved.is_(False)).count()
    recent_products = db.query(Product).filter(not_deleted).order_by(Product.created_at.desc()).limit(5).all()

    new_enquiry_count = db.query(Enquiry).filter(Enquiry.status == "New").count()
    open_quotation_count = db.query(Quotation).filter(Quotation.status.in_(["Draft", "Sent"])).count()
    unpaid_invoice_count = db.query(Invoice).filter(Invoice.payment_status.in_(["Unpaid", "Partially Paid"])).count()
    unpaid_invoice_total = (
        db.query(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0))
        .filter(Invoice.payment_status.in_(["Unpaid", "Partially Paid"]))
        .scalar()
    )

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
            "low_stock_count": low_stock_count,
            "out_of_stock_count": out_of_stock_count,
            "recent_orders": recent_orders,
            "total_products": total_products,
            "active_products": active_products,
            "featured_count": featured_count,
            "new_arrival_count": new_arrival_count,
            "sale_count": sale_count,
            "pending_review_count": pending_review_count,
            "recent_products": recent_products,
            "new_enquiry_count": new_enquiry_count,
            "open_quotation_count": open_quotation_count,
            "unpaid_invoice_count": unpaid_invoice_count,
            "unpaid_invoice_total": unpaid_invoice_total,
        },
        db,
    )


# ---------- Categories ----------

@router.get("/categories")
def categories_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    categories = db.query(Category).order_by(Category.sort_order, Category.name).all()
    return render_admin(request, "admin/categories_list.html", {"active_nav": "categories", "categories": categories}, db)


@router.get("/categories/new")
def category_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    return render_admin(request, "admin/category_form.html", {"active_nav": "categories", "category": None}, db)


@router.post("/categories/new")
def category_new_submit(
    request: Request,
    name: str = Form(...),
    sort_order: int = Form(0),
    active: bool = Form(False),
    meta_title: str = Form(""),
    meta_description: str = Form(""),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    name = name.strip()
    if not name:
        return render_admin(
            request, "admin/category_form.html", {"active_nav": "categories", "category": None, "error": "Name is required."}, db, status_code=400
        )

    image_url = None
    if image is not None and image.filename:
        try:
            image_url, _ = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(
                request, "admin/category_form.html", {"active_nav": "categories", "category": None, "error": exc.detail}, db, status_code=400
            )

    category = Category(
        name=name,
        slug=unique_slug(db, Category, name),
        sort_order=sort_order,
        active=active,
        image=image_url,
        meta_title=meta_title.strip() or None,
        meta_description=meta_description.strip() or None,
    )
    db.add(category)
    db.commit()
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.get("/categories/{category_id}/edit")
def category_edit_page(category_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
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
    meta_title: str = Form(""),
    meta_description: str = Form(""),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
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
            new_image_url, _ = save_product_image(image)
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
    category.meta_title = meta_title.strip() or None
    category.meta_description = meta_description.strip() or None
    db.commit()
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.post("/categories/{category_id}/delete")
def category_delete(category_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    category = db.get(Category, category_id)
    if category is not None:
        db.delete(category)
        db.commit()
    return RedirectResponse(url="/admin/categories", status_code=303)


# ---------- Collections (Occasions & Festivals) ----------

@router.get("/collections")
def collections_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    collections = db.query(Collection).order_by(Collection.sort_order, Collection.name).all()
    counts = dict(
        db.query(product_collections.c.collection_id, func.count(product_collections.c.product_id))
        .group_by(product_collections.c.collection_id)
        .all()
    )
    return render_admin(
        request,
        "admin/collections_list.html",
        {"active_nav": "collections", "collections": collections, "counts": counts},
        db,
    )


@router.get("/collections/new")
def collection_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    return render_admin(request, "admin/collection_form.html", {"active_nav": "collections", "collection": None}, db)


@router.post("/collections/new")
def collection_new_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    sort_order: int = Form(0),
    active: bool = Form(False),
    kind: str = Form("occasion"),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    name = name.strip()
    kind = kind if kind in ("occasion", "age") else "occasion"
    if not name:
        return render_admin(
            request, "admin/collection_form.html", {"active_nav": "collections", "collection": None, "error": "Name is required."}, db, status_code=400
        )

    image_url = None
    if image is not None and image.filename:
        try:
            image_url, _ = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(
                request, "admin/collection_form.html", {"active_nav": "collections", "collection": None, "error": exc.detail}, db, status_code=400
            )

    collection = Collection(
        name=name,
        slug=unique_slug(db, Collection, name),
        description=description.strip() or None,
        sort_order=sort_order,
        active=active,
        kind=kind,
        image=image_url,
    )
    db.add(collection)
    log_activity(db, admin, "collection.created", f"Created collection '{name}'")
    db.commit()
    return RedirectResponse(url="/admin/collections", status_code=303)


@router.get("/collections/{collection_id}/edit")
def collection_edit_page(collection_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    collection = db.get(Collection, collection_id)
    if collection is None:
        return RedirectResponse(url="/admin/collections", status_code=303)
    return render_admin(request, "admin/collection_form.html", {"active_nav": "collections", "collection": collection}, db)


@router.post("/collections/{collection_id}/edit")
def collection_edit_submit(
    collection_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    sort_order: int = Form(0),
    active: bool = Form(False),
    kind: str = Form("occasion"),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    collection = db.get(Collection, collection_id)
    if collection is None:
        return RedirectResponse(url="/admin/collections", status_code=303)

    name = name.strip()
    kind = kind if kind in ("occasion", "age") else "occasion"
    if not name:
        return render_admin(
            request, "admin/collection_form.html", {"active_nav": "collections", "collection": collection, "error": "Name is required."}, db, status_code=400
        )

    if image is not None and image.filename:
        try:
            new_image_url, _ = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(
                request, "admin/collection_form.html", {"active_nav": "collections", "collection": collection, "error": exc.detail}, db, status_code=400
            )
        if collection.image:
            delete_product_image(collection.image)
        collection.image = new_image_url

    if name != collection.name:
        collection.slug = unique_slug(db, Collection, name, exclude_id=collection.id)
    collection.name = name
    collection.description = description.strip() or None
    collection.sort_order = sort_order
    collection.active = active
    collection.kind = kind
    log_activity(db, admin, "collection.updated", f"Updated collection '{name}'", "collection", collection.id)
    db.commit()
    return RedirectResponse(url="/admin/collections", status_code=303)


@router.post("/collections/{collection_id}/delete")
def collection_delete(collection_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    collection = db.get(Collection, collection_id)
    if collection is not None:
        name = collection.name
        db.delete(collection)
        log_activity(db, admin, "collection.deleted", f"Deleted collection '{name}'")
        db.commit()
    return RedirectResponse(url="/admin/collections", status_code=303)


# ---------- Products ----------

@router.get("/products")
def products_list(
    request: Request,
    q: str = "",
    category: int | None = None,
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    query = db.query(Product).filter(Product.deleted_at.is_(None))
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
        "collections": db.query(Collection).order_by(Collection.sort_order, Collection.name).all(),
        "error": error,
    }


@router.get("/products/new")
def product_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    return render_admin(request, "admin/product_form.html", _product_form_context(db), db)


@router.post("/products/new")
async def product_new_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    short_description: str = Form(""),
    meta_title: str = Form(""),
    meta_description: str = Form(""),
    category_id: str = Form(""),
    price: float = Form(...),
    original_price: str = Form(""),
    stock: int = Form(0),
    sku: str = Form(""),
    featured: bool = Form(False),
    bestseller: bool = Form(False),
    new_arrival: bool = Form(False),
    active: bool = Form(True),
    personalize_name: bool = Form(False),
    personalize_message: bool = Form(False),
    personalize_date: bool = Form(False),
    personalize_photo: bool = Form(False),
    collection_ids: list[int] = Form(default=[]),
    images: list[UploadFile] = FastAPIFile(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    name = name.strip()
    sku = sku.strip().upper() or None
    uploaded_images = [img for img in images if img is not None and img.filename]
    error = None
    if not name:
        error = "Product name is required."
    elif price < 0:
        error = "Price cannot be negative."
    elif stock < 0:
        error = "Stock cannot be negative."
    elif sku and db.query(Product).filter(Product.sku == sku).first() is not None:
        error = f"SKU '{sku}' is already used by another product."
    elif len(uploaded_images) < MIN_PRODUCT_IMAGES:
        error = f"Please upload at least {MIN_PRODUCT_IMAGES} product images (maximum {MAX_PRODUCT_IMAGES})."
    elif len(uploaded_images) > MAX_PRODUCT_IMAGES:
        error = f"You can upload a maximum of {MAX_PRODUCT_IMAGES} product images."

    if error:
        return render_admin(request, "admin/product_form.html", _product_form_context(db, error=error), db, status_code=400)

    saved_images = []
    for image in uploaded_images:
        try:
            saved_images.append(save_product_image(image))
        except UploadValidationError as exc:
            for url, thumb_url in saved_images:
                delete_product_image(url, thumb_url)
            return render_admin(request, "admin/product_form.html", _product_form_context(db, error=exc.detail), db, status_code=400)

    product = Product(
        name=name,
        slug=unique_slug(db, Product, name),
        description=description or None,
        short_description=short_description or None,
        meta_title=meta_title.strip() or None,
        meta_description=meta_description.strip() or None,
        category_id=int(category_id) if category_id else None,
        price=price,
        original_price=float(original_price) if original_price else None,
        stock=stock,
        sku=sku,
        featured=featured,
        bestseller=bestseller,
        new_arrival=new_arrival,
        active=active,
        personalize_name=personalize_name,
        personalize_message=personalize_message,
        personalize_date=personalize_date,
        personalize_photo=personalize_photo,
    )
    db.add(product)
    db.flush()

    # A blank SKU is auto-generated from the product's own (now-known) id,
    # so it's guaranteed unique without a race and never touches the id itself.
    if not product.sku:
        product.sku = f"JJG-{product.id:04d}"

    if collection_ids:
        product.collections = db.query(Collection).filter(Collection.id.in_(collection_ids)).all()

    for idx, (url, thumb_url) in enumerate(saved_images):
        db.add(ProductImage(product_id=product.id, image_url=url, thumbnail_url=thumb_url, sort_order=idx))

    log_activity(db, admin, "product.created", f"Created product '{product.name}' ({product.sku})", "product", product.id)
    db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/products/{product_id}/edit")
def product_edit_page(product_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
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
    meta_title: str = Form(""),
    meta_description: str = Form(""),
    category_id: str = Form(""),
    price: float = Form(...),
    original_price: str = Form(""),
    stock: int = Form(0),
    sku: str = Form(""),
    featured: bool = Form(False),
    bestseller: bool = Form(False),
    new_arrival: bool = Form(False),
    active: bool = Form(True),
    personalize_name: bool = Form(False),
    personalize_message: bool = Form(False),
    personalize_date: bool = Form(False),
    personalize_photo: bool = Form(False),
    collection_ids: list[int] = Form(default=[]),
    delete_image_ids: list[int] = Form(default=[]),
    images: list[UploadFile] = FastAPIFile(default=[]),
    image_order: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    product = db.query(Product).options(joinedload(Product.images), joinedload(Product.collections)).filter(Product.id == product_id).first()
    if product is None:
        return RedirectResponse(url="/admin/products", status_code=303)

    name = name.strip()
    sku = sku.strip().upper() or None
    uploaded_images = [img for img in images if img is not None and img.filename]
    remaining_existing = [img for img in product.images if img.id not in delete_image_ids]
    error = None
    if not name:
        error = "Product name is required."
    elif price < 0:
        error = "Price cannot be negative."
    elif stock < 0:
        error = "Stock cannot be negative."
    elif sku and db.query(Product).filter(Product.sku == sku, Product.id != product.id).first() is not None:
        error = f"SKU '{sku}' is already used by another product."
    elif len(remaining_existing) + len(uploaded_images) > MAX_PRODUCT_IMAGES:
        error = f"A product can have a maximum of {MAX_PRODUCT_IMAGES} images. Please remove some before adding more."

    if error:
        return render_admin(request, "admin/product_form.html", _product_form_context(db, product=product, error=error), db, status_code=400)

    for img in list(product.images):
        if img.id in delete_image_ids:
            delete_product_image(img.image_url, img.thumbnail_url)
            db.delete(img)

    # Reorder the images the admin kept, per drag-reorder / "Make Primary" in
    # the form (a hidden field listing existing image IDs in the new order).
    # Only IDs that are genuinely this product's remaining images are honored,
    # so a tampered field can't touch another product's rows.
    remaining_by_id = {img.id: img for img in remaining_existing}
    ordered_ids = [int(x) for x in image_order.split(",") if x.strip().isdigit()]
    ordered_ids = [i for i in ordered_ids if i in remaining_by_id]
    ordered_ids += [img.id for img in remaining_existing if img.id not in ordered_ids]
    for idx, img_id in enumerate(ordered_ids):
        remaining_by_id[img_id].sort_order = idx

    max_sort = len(ordered_ids) - 1
    for image in uploaded_images:
        try:
            url, thumb_url = save_product_image(image)
        except UploadValidationError as exc:
            db.rollback()
            return render_admin(request, "admin/product_form.html", _product_form_context(db, product=product, error=exc.detail), db, status_code=400)
        max_sort += 1
        db.add(ProductImage(product_id=product.id, image_url=url, thumbnail_url=thumb_url, sort_order=max_sort))

    if name != product.name:
        product.slug = unique_slug(db, Product, name, exclude_id=product.id)

    # Compare before overwriting, so the audit log records exactly which
    # fields actually changed rather than just "product updated".
    new_original_price = float(original_price) if original_price else None
    changes = []
    if stock != product.stock:
        changes.append(f"stock {product.stock} → {stock}")
    if price != product.price:
        changes.append(f"price ₹{product.price:.2f} → ₹{price:.2f}")
    if new_original_price != product.original_price:
        changes.append("discount changed")

    product.name = name
    product.description = description or None
    product.short_description = short_description or None
    product.meta_title = meta_title.strip() or None
    product.meta_description = meta_description.strip() or None
    product.category_id = int(category_id) if category_id else None
    product.price = price
    product.original_price = float(original_price) if original_price else None
    product.stock = stock
    if not product.sku:
        product.sku = sku or f"JJG-{product.id:04d}"
    else:
        product.sku = sku or product.sku
    product.featured = featured
    product.bestseller = bestseller
    product.new_arrival = new_arrival
    product.active = active
    product.personalize_name = personalize_name
    product.personalize_message = personalize_message
    product.personalize_date = personalize_date
    product.personalize_photo = personalize_photo
    product.collections = db.query(Collection).filter(Collection.id.in_(collection_ids)).all() if collection_ids else []

    description_text = f"Updated product '{product.name}'" + (f" ({'; '.join(changes)})" if changes else "")
    log_activity(db, admin, "product.updated", description_text, "product", product.id)

    db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
def product_delete(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    """Soft delete: images and the row itself are kept (so it can be
    restored exactly as it was) — only hidden from every customer-facing
    and normal admin view. Use /delete-permanent for a real, irreversible
    delete from the Deleted Products page."""
    product = db.get(Product, product_id)
    if product is not None and product.deleted_at is None:
        product.deleted_at = now_utc()
        product.active = False
        log_activity(db, admin, "product.deleted", f"Deleted product '{product.name}'", "product", product.id)
        db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/products/deleted")
def products_deleted_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    products = db.query(Product).filter(Product.deleted_at.isnot(None)).order_by(Product.deleted_at.desc()).all()
    return render_admin(request, "admin/products_deleted.html", {"active_nav": "products", "products": products}, db)


@router.post("/products/{product_id}/restore")
def product_restore(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    product = db.get(Product, product_id)
    if product is not None and product.deleted_at is not None:
        product.deleted_at = None
        product.active = True
        log_activity(db, admin, "product.restored", f"Restored product '{product.name}'", "product", product.id)
        db.commit()
    return RedirectResponse(url="/admin/products/deleted", status_code=303)


@router.post("/products/{product_id}/delete-permanent")
def product_delete_permanent(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    product = db.query(Product).options(joinedload(Product.images)).filter(Product.id == product_id).first()
    if product is not None and product.deleted_at is not None:
        name = product.name
        for img in product.images:
            delete_product_image(img.image_url, img.thumbnail_url)
        db.delete(product)
        log_activity(db, admin, "product.deleted_permanent", f"Permanently deleted product '{name}'")
        db.commit()
    return RedirectResponse(url="/admin/products/deleted", status_code=303)


@router.post("/products/{product_id}/duplicate")
def product_duplicate(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
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
    duplicate.sku = f"JJG-{duplicate.id:04d}"
    for img in original.images:
        db.add(ProductImage(product_id=duplicate.id, image_url=img.image_url, thumbnail_url=img.thumbnail_url, sort_order=img.sort_order))
    log_activity(db, admin, "product.created", f"Duplicated '{original.name}' as '{duplicate.name}' ({duplicate.sku})", "product", duplicate.id)
    db.commit()
    return RedirectResponse(url=f"/admin/products/{duplicate.id}/edit", status_code=303)


@router.post("/products/{product_id}/toggle-active")
def product_toggle_active(product_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    product = db.get(Product, product_id)
    if product is not None:
        product.active = not product.active
        db.commit()
    return RedirectResponse(url="/admin/products", status_code=303)


# ---------- Settings ----------

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    values = get_all_settings(db)
    return render_admin(
        request,
        "admin/settings.html",
        {
            "active_nav": "settings",
            "values": values,
            "default_product_template": DEFAULT_PRODUCT_TEMPLATE,
            "default_cart_template": DEFAULT_CART_TEMPLATE,
            "languages": SUPPORTED_LANGUAGES,
        },
        db,
    )


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
    announcement_enabled: bool = Form(False),
    announcement_text: str = Form(""),
    low_stock_threshold: str = Form("5"),
    whatsapp_product_template: str = Form(""),
    whatsapp_cart_template: str = Form(""),
    whatsapp_quotation_template: str = Form(""),
    gst_number: str = Form(""),
    default_gst_rate: str = Form("18"),
    site_language: str = Form("en"),
    manual_payment_enabled: bool = Form(False),
    upi_id: str = Form(""),
    bank_account_name: str = Form(""),
    bank_account_number: str = Form(""),
    bank_ifsc: str = Form(""),
    bank_name: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_super_admin),
):
    old_values = get_all_settings(db)
    new_values = {
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
        "announcement_enabled": "true" if announcement_enabled else "false",
        "announcement_text": announcement_text.strip(),
        "low_stock_threshold": str(max(int(low_stock_threshold or 5), 0)),
        "whatsapp_product_template": whatsapp_product_template.strip(),
        "whatsapp_cart_template": whatsapp_cart_template.strip(),
        "whatsapp_quotation_template": whatsapp_quotation_template.strip(),
        "gst_number": gst_number.strip(),
        "default_gst_rate": str(min(max(float(default_gst_rate or 0), 0), 100)),
        "site_language": site_language if site_language in SUPPORTED_LANGUAGES else "en",
        "manual_payment_enabled": "true" if manual_payment_enabled else "false",
        "upi_id": upi_id.strip(),
        "bank_account_name": bank_account_name.strip(),
        "bank_account_number": bank_account_number.strip(),
        "bank_ifsc": bank_ifsc.strip(),
        "bank_name": bank_name.strip(),
    }

    if old_values.get("whatsapp_number") != new_values["whatsapp_number"]:
        log_activity(db, admin, "settings.whatsapp_changed", f"WhatsApp number changed to {new_values['whatsapp_number']}")
    changed_keys = [k for k, v in new_values.items() if old_values.get(k) != v and k != "whatsapp_number"]
    if changed_keys:
        log_activity(db, admin, "settings.updated", f"Settings updated: {', '.join(changed_keys)}")

    # log_activity only stages the row (db.add) — it rides along inside the
    # same commit set_settings() issues, so it's never lost or duplicated.
    set_settings(db, new_values)
    return RedirectResponse(url="/admin/settings", status_code=303)


# ---------- Admin Users ----------

def _admin_form_context(admin_user=None, error=None) -> dict:
    return {"active_nav": "admins", "admin_user": admin_user, "error": error, "roles": ADMIN_ROLES, "role_labels": ROLE_LABELS}


@router.get("/admins")
def admins_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    admins = db.query(Admin).order_by(Admin.created_at).all()
    return render_admin(
        request, "admin/admins_list.html", {"active_nav": "admins", "admins": admins, "role_labels": ROLE_LABELS}, db
    )


@router.get("/admins/new")
def admin_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    return render_admin(request, "admin/admin_form.html", _admin_form_context(), db)


@router.post("/admins/new")
def admin_new_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(ROLE_PRODUCT_MANAGER),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_super_admin),
):
    name = name.strip()
    email = email.strip().lower()
    error = None
    if not name:
        error = "Name is required."
    elif not email:
        error = "Email is required."
    elif role not in ADMIN_ROLES:
        error = "Invalid role."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif db.query(Admin).filter(Admin.email == email).first() is not None:
        error = "An admin with that email already exists."

    if error:
        return render_admin(request, "admin/admin_form.html", _admin_form_context(error=error), db, status_code=400)

    new_admin = Admin(name=name, email=email, password_hash=hash_password(password), role=role)
    db.add(new_admin)
    db.flush()
    log_activity(db, admin, "admin.created", f"Created admin '{name}' ({email}) as {ROLE_LABELS[role]}", "admin", new_admin.id)
    db.commit()
    return RedirectResponse(url="/admin/admins", status_code=303)


@router.get("/admins/{admin_id}/edit")
def admin_edit_page(admin_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    admin_user = db.get(Admin, admin_id)
    if admin_user is None:
        return RedirectResponse(url="/admin/admins", status_code=303)
    return render_admin(request, "admin/admin_form.html", _admin_form_context(admin_user=admin_user), db)


@router.post("/admins/{admin_id}/edit")
def admin_edit_submit(
    admin_id: int,
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_super_admin),
):
    admin_user = db.get(Admin, admin_id)
    if admin_user is None:
        return RedirectResponse(url="/admin/admins", status_code=303)

    name = name.strip()
    error = None
    if not name:
        error = "Name is required."
    elif role not in ADMIN_ROLES:
        error = "Invalid role."
    elif admin_user.id == admin.id and role != ROLE_SUPER_ADMIN:
        error = "You can't demote your own account — ask another Super Admin to change it."
    elif admin_user.role == ROLE_SUPER_ADMIN and role != ROLE_SUPER_ADMIN:
        remaining_super_admins = db.query(Admin).filter(Admin.role == ROLE_SUPER_ADMIN, Admin.id != admin_user.id).count()
        if remaining_super_admins == 0:
            error = "There must always be at least one Super Admin."

    if error:
        return render_admin(request, "admin/admin_form.html", _admin_form_context(admin_user=admin_user, error=error), db, status_code=400)

    admin_user.name = name
    admin_user.role = role
    log_activity(db, admin, "admin.updated", f"Updated admin '{name}' to {ROLE_LABELS[role]}", "admin", admin_user.id)
    db.commit()
    return RedirectResponse(url="/admin/admins", status_code=303)


@router.post("/admins/{admin_id}/delete")
def admin_delete(admin_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    admin_user = db.get(Admin, admin_id)
    if admin_user is None:
        return RedirectResponse(url="/admin/admins", status_code=303)
    if admin_user.id == admin.id:
        return RedirectResponse(url="/admin/admins?error=self", status_code=303)
    if admin_user.role == ROLE_SUPER_ADMIN:
        remaining_super_admins = db.query(Admin).filter(Admin.role == ROLE_SUPER_ADMIN, Admin.id != admin_user.id).count()
        if remaining_super_admins == 0:
            return RedirectResponse(url="/admin/admins?error=last_super_admin", status_code=303)
    name = admin_user.name
    db.delete(admin_user)
    log_activity(db, admin, "admin.deleted", f"Deleted admin '{name}'")
    db.commit()
    return RedirectResponse(url="/admin/admins", status_code=303)


# ---------- Backup ----------

@router.get("/backup")
def backup_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    values = get_all_settings(db)
    return render_admin(request, "admin/backup.html", {"active_nav": "backup", "last_backup_at": values.get("last_backup_at", "")}, db)


@router.get("/backup/download")
def backup_download(db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    data = build_backup(db)
    set_settings(db, {"last_backup_at": data["exported_at"]})

    filename = f"jjg-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(content=data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- Activity Log ----------

ACTIVITY_LOG_PAGE_SIZE = 50


@router.get("/activity-log")
def activity_log_page(request: Request, page: int = 1, db: Session = Depends(get_db), admin: Admin = Depends(require_super_admin)):
    page = max(page, 1)
    query = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    total = query.count()
    entries = query.offset((page - 1) * ACTIVITY_LOG_PAGE_SIZE).limit(ACTIVITY_LOG_PAGE_SIZE).all()
    total_pages = max((total + ACTIVITY_LOG_PAGE_SIZE - 1) // ACTIVITY_LOG_PAGE_SIZE, 1)
    return render_admin(
        request,
        "admin/activity_log.html",
        {
            "active_nav": "activity-log",
            "entries": entries,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        },
        db,
    )


# ---------- Analytics ----------

ANALYTICS_PERIODS = (7, 30, 90)


@router.get("/analytics")
def analytics_page(request: Request, days: int = 30, db: Session = Depends(get_db), admin: Admin = Depends(require_admin)):
    days = days if days in ANALYTICS_PERIODS else 30
    cutoff = now_utc() - timedelta(days=days)

    rows = (
        db.query(ProductEvent.product_id, ProductEvent.event_type, func.count(ProductEvent.id))
        .filter(ProductEvent.created_at >= cutoff)
        .group_by(ProductEvent.product_id, ProductEvent.event_type)
        .all()
    )

    counts_by_product: dict[int, dict[str, int]] = {}
    for product_id, event_type, count in rows:
        counts_by_product.setdefault(product_id, {"view": 0, "enquiry": 0, "add_to_cart": 0})[event_type] = count

    products_by_id = {}
    if counts_by_product:
        products_by_id = {p.id: p for p in db.query(Product).filter(Product.id.in_(counts_by_product.keys())).all()}

    breakdown = []
    totals = {"view": 0, "enquiry": 0, "add_to_cart": 0}
    for product_id, counts in counts_by_product.items():
        for key in totals:
            totals[key] += counts[key]
        product = products_by_id.get(product_id)
        if product is None:
            continue  # events for a since-hard-deleted product — nothing left to show a name for
        breakdown.append({"product": product, **counts, "total": sum(counts.values())})
    breakdown.sort(key=lambda row: row["total"], reverse=True)

    # Bar widths are relative to the busiest product for that same metric,
    # so rows are visually comparable to each other rather than each bar
    # being self-normalized (which would make a 1-view product look as
    # "full" as a 100-view one).
    max_metric = max([max(row["view"], row["enquiry"], row["add_to_cart"]) for row in breakdown], default=0) or 1

    return render_admin(
        request,
        "admin/analytics.html",
        {
            "active_nav": "analytics",
            "days": days,
            "periods": ANALYTICS_PERIODS,
            "breakdown": breakdown,
            "totals": totals,
            "max_metric": max_metric,
        },
        db,
    )


# ---------- Coupons ----------

def _parse_expiry_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
    except ValueError:
        return None


def _coupon_form_context(coupon=None, error=None) -> dict:
    return {"active_nav": "coupons", "coupon": coupon, "error": error}


@router.get("/coupons")
def coupons_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()
    return render_admin(request, "admin/coupons_list.html", {"active_nav": "coupons", "coupons": coupons}, db)


@router.get("/coupons/new")
def coupon_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    return render_admin(request, "admin/coupon_form.html", _coupon_form_context(), db)


def _validate_coupon_form(db: Session, code: str, discount_type: str, discount_value: float, exclude_id: int | None) -> str | None:
    if not code:
        return "Coupon code is required."
    if discount_type not in ("flat", "percent"):
        return "Invalid discount type."
    if discount_value <= 0:
        return "Discount value must be greater than 0."
    if discount_type == "percent" and discount_value > 100:
        return "Percentage discount cannot exceed 100."
    query = db.query(Coupon).filter(Coupon.code == code)
    if exclude_id is not None:
        query = query.filter(Coupon.id != exclude_id)
    if query.first() is not None:
        return "A coupon with this code already exists."
    return None


@router.post("/coupons/new")
def coupon_new_submit(
    request: Request,
    code: str = Form(...),
    discount_type: str = Form("flat"),
    discount_value: float = Form(...),
    min_order_value: float = Form(0),
    usage_limit: str = Form(""),
    expires_at: str = Form(""),
    active: bool = Form(True),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    code = code.strip().upper()
    error = _validate_coupon_form(db, code, discount_type, discount_value, exclude_id=None)
    if error:
        return render_admin(request, "admin/coupon_form.html", _coupon_form_context(error=error), db, status_code=400)

    coupon = Coupon(
        code=code,
        discount_type=discount_type,
        discount_value=discount_value,
        min_order_value=min_order_value or 0,
        usage_limit=int(usage_limit) if usage_limit.strip() else None,
        expires_at=_parse_expiry_date(expires_at),
        active=active,
    )
    db.add(coupon)
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.get("/coupons/{coupon_id}/edit")
def coupon_edit_page(coupon_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        return RedirectResponse(url="/admin/coupons", status_code=303)
    return render_admin(request, "admin/coupon_form.html", _coupon_form_context(coupon=coupon), db)


@router.post("/coupons/{coupon_id}/edit")
def coupon_edit_submit(
    coupon_id: int,
    request: Request,
    code: str = Form(...),
    discount_type: str = Form("flat"),
    discount_value: float = Form(...),
    min_order_value: float = Form(0),
    usage_limit: str = Form(""),
    expires_at: str = Form(""),
    active: bool = Form(True),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        return RedirectResponse(url="/admin/coupons", status_code=303)

    code = code.strip().upper()
    error = _validate_coupon_form(db, code, discount_type, discount_value, exclude_id=coupon_id)
    if error:
        return render_admin(request, "admin/coupon_form.html", _coupon_form_context(coupon=coupon, error=error), db, status_code=400)

    coupon.code = code
    coupon.discount_type = discount_type
    coupon.discount_value = discount_value
    coupon.min_order_value = min_order_value or 0
    coupon.usage_limit = int(usage_limit) if usage_limit.strip() else None
    coupon.expires_at = _parse_expiry_date(expires_at)
    coupon.active = active
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.post("/coupons/{coupon_id}/delete")
def coupon_delete(coupon_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    coupon = db.get(Coupon, coupon_id)
    if coupon is not None:
        db.delete(coupon)
        db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


# ---------- Gift Options ----------

def _gift_option_form_context(gift_option=None, error=None) -> dict:
    return {"active_nav": "gift-options", "gift_option": gift_option, "error": error}


@router.get("/gift-options")
def gift_options_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    gift_options = db.query(GiftOption).order_by(GiftOption.sort_order, GiftOption.name).all()
    return render_admin(request, "admin/gift_options_list.html", {"active_nav": "gift-options", "gift_options": gift_options}, db)


@router.get("/gift-options/new")
def gift_option_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    return render_admin(request, "admin/gift_option_form.html", _gift_option_form_context(), db)


@router.post("/gift-options/new")
def gift_option_new_submit(
    request: Request,
    name: str = Form(...),
    price: float = Form(...),
    sort_order: int = Form(0),
    active: bool = Form(True),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    name = name.strip()
    error = None
    if not name:
        error = "Name is required."
    elif price < 0:
        error = "Price cannot be negative."

    if error:
        return render_admin(request, "admin/gift_option_form.html", _gift_option_form_context(error=error), db, status_code=400)

    gift_option = GiftOption(name=name, price=price, sort_order=sort_order, active=active)
    db.add(gift_option)
    log_activity(db, admin, "gift_option.created", f"Created gift option '{name}' (₹{price:.0f})")
    db.commit()
    return RedirectResponse(url="/admin/gift-options", status_code=303)


@router.get("/gift-options/{gift_option_id}/edit")
def gift_option_edit_page(gift_option_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    gift_option = db.get(GiftOption, gift_option_id)
    if gift_option is None:
        return RedirectResponse(url="/admin/gift-options", status_code=303)
    return render_admin(request, "admin/gift_option_form.html", _gift_option_form_context(gift_option=gift_option), db)


@router.post("/gift-options/{gift_option_id}/edit")
def gift_option_edit_submit(
    gift_option_id: int,
    request: Request,
    name: str = Form(...),
    price: float = Form(...),
    sort_order: int = Form(0),
    active: bool = Form(True),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    gift_option = db.get(GiftOption, gift_option_id)
    if gift_option is None:
        return RedirectResponse(url="/admin/gift-options", status_code=303)

    name = name.strip()
    error = None
    if not name:
        error = "Name is required."
    elif price < 0:
        error = "Price cannot be negative."

    if error:
        return render_admin(request, "admin/gift_option_form.html", _gift_option_form_context(gift_option=gift_option, error=error), db, status_code=400)

    gift_option.name = name
    gift_option.price = price
    gift_option.sort_order = sort_order
    gift_option.active = active
    log_activity(db, admin, "gift_option.updated", f"Updated gift option '{name}' (₹{price:.0f})", "gift_option", gift_option.id)
    db.commit()
    return RedirectResponse(url="/admin/gift-options", status_code=303)


@router.post("/gift-options/{gift_option_id}/delete")
def gift_option_delete(gift_option_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    gift_option = db.get(GiftOption, gift_option_id)
    if gift_option is not None:
        name = gift_option.name
        db.delete(gift_option)
        log_activity(db, admin, "gift_option.deleted", f"Deleted gift option '{name}'")
        db.commit()
    return RedirectResponse(url="/admin/gift-options", status_code=303)


# ---------- Gift Combos ----------

def _bundle_form_context(db: Session, bundle=None, error=None) -> dict:
    products = db.query(Product).filter(Product.active.is_(True), Product.deleted_at.is_(None)).order_by(Product.name).all()
    selected_qty = {item.product_id: item.quantity for item in bundle.items} if bundle else {}
    return {"active_nav": "bundles", "bundle": bundle, "products": products, "selected_qty": selected_qty, "error": error}


@router.get("/bundles")
def bundles_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    bundles = db.query(ProductBundle).options(joinedload(ProductBundle.items)).order_by(ProductBundle.sort_order, ProductBundle.name).all()
    return render_admin(request, "admin/bundles_list.html", {"active_nav": "bundles", "bundles": bundles}, db)


@router.get("/bundles/new")
def bundle_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    return render_admin(request, "admin/bundle_form.html", _bundle_form_context(db), db)


def _parse_bundle_items(form_items: dict) -> list[tuple[int, int]]:
    """form_items: {'product_qty_12': '2', ...} -> [(12, 2), ...], skipping blank/zero quantities."""
    items = []
    for key, value in form_items.items():
        if not key.startswith("product_qty_") or not value or not value.strip():
            continue
        try:
            qty = int(value)
            product_id = int(key[len("product_qty_"):])
        except ValueError:
            continue
        if qty > 0:
            items.append((product_id, qty))
    return items


@router.post("/bundles/new")
async def bundle_new_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    bundle_price: str = Form(""),
    sort_order: int = Form(0),
    active: bool = Form(False),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    name = name.strip()
    form = await request.form()
    items = _parse_bundle_items(dict(form))

    if not name:
        return render_admin(request, "admin/bundle_form.html", _bundle_form_context(db, error="Name is required."), db, status_code=400)
    if len(items) < 2:
        return render_admin(
            request, "admin/bundle_form.html", _bundle_form_context(db, error="Select at least 2 products (with a quantity) for a combo."), db, status_code=400
        )

    image_url = None
    if image is not None and image.filename:
        try:
            image_url, _ = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(request, "admin/bundle_form.html", _bundle_form_context(db, error=exc.detail), db, status_code=400)

    bundle = ProductBundle(
        name=name,
        slug=unique_slug(db, ProductBundle, name),
        description=description.strip() or None,
        bundle_price=float(bundle_price) if bundle_price else None,
        image=image_url,
        active=active,
        sort_order=sort_order,
    )
    db.add(bundle)
    db.flush()
    for product_id, qty in items:
        db.add(ProductBundleItem(bundle_id=bundle.id, product_id=product_id, quantity=qty))
    log_activity(db, admin, "bundle.created", f"Created gift combo '{bundle.name}'")
    db.commit()
    return RedirectResponse(url="/admin/bundles", status_code=303)


@router.get("/bundles/{bundle_id}/edit")
def bundle_edit_page(bundle_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    bundle = db.query(ProductBundle).options(joinedload(ProductBundle.items)).filter(ProductBundle.id == bundle_id).first()
    if bundle is None:
        return RedirectResponse(url="/admin/bundles", status_code=303)
    return render_admin(request, "admin/bundle_form.html", _bundle_form_context(db, bundle=bundle), db)


@router.post("/bundles/{bundle_id}/edit")
async def bundle_edit_submit(
    bundle_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    bundle_price: str = Form(""),
    sort_order: int = Form(0),
    active: bool = Form(False),
    image: UploadFile = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    bundle = db.query(ProductBundle).options(joinedload(ProductBundle.items)).filter(ProductBundle.id == bundle_id).first()
    if bundle is None:
        return RedirectResponse(url="/admin/bundles", status_code=303)

    name = name.strip()
    form = await request.form()
    items = _parse_bundle_items(dict(form))

    if not name:
        return render_admin(request, "admin/bundle_form.html", _bundle_form_context(db, bundle=bundle, error="Name is required."), db, status_code=400)
    if len(items) < 2:
        return render_admin(
            request, "admin/bundle_form.html", _bundle_form_context(db, bundle=bundle, error="Select at least 2 products (with a quantity) for a combo."), db, status_code=400
        )

    if image is not None and image.filename:
        try:
            new_image_url, _ = save_product_image(image)
        except UploadValidationError as exc:
            return render_admin(request, "admin/bundle_form.html", _bundle_form_context(db, bundle=bundle, error=exc.detail), db, status_code=400)
        if bundle.image:
            delete_product_image(bundle.image)
        bundle.image = new_image_url

    if name != bundle.name:
        bundle.slug = unique_slug(db, ProductBundle, name, exclude_id=bundle.id)
    bundle.name = name
    bundle.description = description.strip() or None
    bundle.bundle_price = float(bundle_price) if bundle_price else None
    bundle.sort_order = sort_order
    bundle.active = active

    for item in list(bundle.items):
        db.delete(item)
    db.flush()
    for product_id, qty in items:
        db.add(ProductBundleItem(bundle_id=bundle.id, product_id=product_id, quantity=qty))

    log_activity(db, admin, "bundle.updated", f"Updated gift combo '{bundle.name}'", "bundle", bundle.id)
    db.commit()
    return RedirectResponse(url="/admin/bundles", status_code=303)


@router.post("/bundles/{bundle_id}/delete")
def bundle_delete(bundle_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    bundle = db.get(ProductBundle, bundle_id)
    if bundle is not None:
        name = bundle.name
        if bundle.image:
            delete_product_image(bundle.image)
        db.delete(bundle)
        log_activity(db, admin, "bundle.deleted", f"Deleted gift combo '{name}'")
        db.commit()
    return RedirectResponse(url="/admin/bundles", status_code=303)


# ---------- Reviews ----------

@router.get("/reviews")
def reviews_list(
    request: Request,
    status: str = "",
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_product_admin),
):
    query = db.query(Review).options(joinedload(Review.product), joinedload(Review.customer))
    if status == "pending":
        query = query.filter(Review.approved.is_(False))
    elif status == "approved":
        query = query.filter(Review.approved.is_(True))
    reviews = query.order_by(Review.created_at.desc()).all()
    pending_count = db.query(Review).filter(Review.approved.is_(False)).count()
    return render_admin(
        request,
        "admin/reviews_list.html",
        {"active_nav": "reviews", "reviews": reviews, "status": status, "pending_count": pending_count},
        db,
    )


@router.post("/reviews/{review_id}/approve")
def review_approve(review_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    review = db.get(Review, review_id)
    if review is not None:
        review.approved = True
        db.commit()
    return RedirectResponse(url="/admin/reviews", status_code=303)


@router.post("/reviews/{review_id}/hide")
def review_hide(review_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    review = db.get(Review, review_id)
    if review is not None:
        review.approved = False
        db.commit()
    return RedirectResponse(url="/admin/reviews", status_code=303)


@router.post("/reviews/{review_id}/delete")
def review_delete(review_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_product_admin)):
    review = db.get(Review, review_id)
    if review is not None:
        db.delete(review)
        db.commit()
    return RedirectResponse(url="/admin/reviews", status_code=303)


# ---------- WhatsApp Enquiries ----------

ENQUIRY_STATUSES = ["New", "Contacted", "Confirmed", "Completed", "Cancelled"]


def _enquiry_form_context(db: Session, enquiry=None, error=None) -> dict:
    products = db.query(Product).filter(Product.deleted_at.is_(None)).order_by(Product.name).all()
    return {"active_nav": "enquiries", "enquiry": enquiry, "products": products, "statuses": ENQUIRY_STATUSES, "error": error}


@router.get("/enquiries")
def enquiries_list(
    request: Request,
    status: str = "",
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    query = db.query(Enquiry).options(joinedload(Enquiry.product))
    if status in ENQUIRY_STATUSES:
        query = query.filter(Enquiry.status == status)
    enquiries = query.order_by(Enquiry.created_at.desc()).all()
    return render_admin(
        request,
        "admin/enquiries_list.html",
        {"active_nav": "enquiries", "enquiries": enquiries, "status": status, "statuses": ENQUIRY_STATUSES},
        db,
    )


@router.get("/enquiries/new")
def enquiry_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    return render_admin(request, "admin/enquiry_form.html", _enquiry_form_context(db), db)


@router.post("/enquiries/new")
def enquiry_new_submit(
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    product_id: str = Form(""),
    quantity: str = Form(""),
    status: str = Form("New"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    error = None
    if not customer_name:
        error = "Customer name is required."
    elif not customer_phone:
        error = "Customer phone is required."

    if error:
        return render_admin(request, "admin/enquiry_form.html", _enquiry_form_context(db, error=error), db, status_code=400)

    enquiry = Enquiry(
        customer_name=customer_name,
        customer_phone=customer_phone,
        product_id=int(product_id) if product_id else None,
        quantity=int(quantity) if quantity.strip() else None,
        status=status if status in ENQUIRY_STATUSES else "New",
        notes=notes.strip() or None,
    )
    db.add(enquiry)
    log_activity(db, admin, "enquiry.created", f"Logged enquiry from {customer_name} ({customer_phone})")
    db.commit()
    return RedirectResponse(url="/admin/enquiries", status_code=303)


@router.get("/enquiries/{enquiry_id}/edit")
def enquiry_edit_page(enquiry_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    enquiry = db.get(Enquiry, enquiry_id)
    if enquiry is None:
        return RedirectResponse(url="/admin/enquiries", status_code=303)
    return render_admin(request, "admin/enquiry_form.html", _enquiry_form_context(db, enquiry=enquiry), db)


@router.post("/enquiries/{enquiry_id}/edit")
def enquiry_edit_submit(
    enquiry_id: int,
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    product_id: str = Form(""),
    quantity: str = Form(""),
    status: str = Form("New"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    enquiry = db.get(Enquiry, enquiry_id)
    if enquiry is None:
        return RedirectResponse(url="/admin/enquiries", status_code=303)

    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    error = None
    if not customer_name:
        error = "Customer name is required."
    elif not customer_phone:
        error = "Customer phone is required."

    if error:
        return render_admin(request, "admin/enquiry_form.html", _enquiry_form_context(db, enquiry=enquiry, error=error), db, status_code=400)

    status_changed = status != enquiry.status and status in ENQUIRY_STATUSES

    enquiry.customer_name = customer_name
    enquiry.customer_phone = customer_phone
    enquiry.product_id = int(product_id) if product_id else None
    enquiry.quantity = int(quantity) if quantity.strip() else None
    enquiry.status = status if status in ENQUIRY_STATUSES else enquiry.status
    enquiry.notes = notes.strip() or None

    if status_changed:
        log_activity(db, admin, "enquiry.status_changed", f"Enquiry from {customer_name} marked {enquiry.status}", "enquiry", enquiry.id)

    db.commit()
    return RedirectResponse(url="/admin/enquiries", status_code=303)


@router.post("/enquiries/{enquiry_id}/delete")
def enquiry_delete(enquiry_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    enquiry = db.get(Enquiry, enquiry_id)
    if enquiry is not None:
        db.delete(enquiry)
        db.commit()
    return RedirectResponse(url="/admin/enquiries", status_code=303)


# ---------- Orders ----------

@router.get("/orders")
def orders_list(
    request: Request,
    q: str = "",
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
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
def order_detail(order_number: str, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
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
    admin: Admin = Depends(require_order_admin),
):
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.order_number == order_number).first()
    if order is not None and status in ORDER_STATUSES and status != order.order_status:
        order.order_status = status
        db.add(OrderStatusHistory(order_id=order.id, status=status))

        # Cancelling releases any stock this order was holding — matters
        # most for abandoned online payments. Guarded by stock_restored so
        # re-saving/re-cancelling never double-restores the same units.
        if status == OrderStatus.CANCELLED.value and not order.stock_restored:
            for item in order.items:
                if item.product_id is not None:
                    product = db.get(Product, item.product_id)
                    if product is not None:
                        product.stock += item.quantity
            order.stock_restored = True

        db.commit()
    return RedirectResponse(url=f"/admin/orders/{order_number}", status_code=303)


@router.post("/orders/{order_number}/mark-paid")
def order_mark_paid(
    order_number: str,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    """Manual payment methods (UPI/bank transfer) have no automated
    verification — this is the admin confirming, after checking their own
    UPI/bank app, that money actually arrived."""
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if order is not None:
        order.payment_status = "Paid"
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
    admin: Admin = Depends(require_order_admin),
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


# ---------- Customer accounts ----------

@router.post("/customers/{customer_id}/reset-password")
def customer_reset_password(
    customer_id: int,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_order_admin),
):
    """
    There's no self-service password reset (would need a paid SMS/email
    service, contradicting the zero-cost requirement) — this is the manual
    fallback: admin sets a temporary password and relays it to the customer
    over WhatsApp/phone.
    """
    customer = db.get(Customer, customer_id)
    referer = request.headers.get("referer", "/admin/orders")
    if customer is not None and len(new_password) >= 8:
        customer.password_hash = hash_password(new_password)
        db.commit()
    return RedirectResponse(url=referer, status_code=303)


# ---------- Notify Requests ----------

@router.get("/notify-requests")
def notify_requests_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    requests_ = (
        db.query(StockNotifyRequest)
        .options(joinedload(StockNotifyRequest.product), joinedload(StockNotifyRequest.customer))
        .order_by(StockNotifyRequest.notified_at.is_not(None), StockNotifyRequest.created_at.desc())
        .all()
    )
    return render_admin(request, "admin/notify_requests_list.html", {"active_nav": "notify-requests", "requests": requests_}, db)


@router.post("/notify-requests/{request_id}/mark-notified")
def notify_request_mark_notified(request_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    notify_request = db.get(StockNotifyRequest, request_id)
    if notify_request is not None and notify_request.notified_at is None:
        notify_request.notified_at = now_utc()
        db.commit()
    return RedirectResponse(url="/admin/notify-requests", status_code=303)


# ---------- Abandoned Carts ----------

@router.get("/abandoned-carts")
def abandoned_carts_list(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_order_admin)):
    leads = (
        db.query(AbandonedCartLead)
        .options(joinedload(AbandonedCartLead.customer))
        .order_by(AbandonedCartLead.last_updated.desc())
        .all()
    )
    return render_admin(request, "admin/abandoned_carts_list.html", {"active_nav": "abandoned-carts", "leads": leads}, db)
