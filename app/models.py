import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class OrderStatus(str, enum.Enum):
    PLACED = "Order Placed"
    CONFIRMED = "Order Confirmed"
    PROCESSING = "Processing"
    PACKED = "Packed"
    SHIPPED = "Shipped"
    OUT_FOR_DELIVERY = "Out for Delivery"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "Pending"
    PAID = "Paid"


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="super_admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


product_collections = Table(
    "product_collections",
    Base.metadata,
    Column("product_id", ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    price: Mapped[float] = mapped_column(Float)
    original_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stock: Mapped[int] = mapped_column(Integer, default=0)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    bestseller: Mapped[bool] = mapped_column(Boolean, default=False)
    new_arrival: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True, index=True)
    # Which personalization fields (if any) this product accepts. Each is its
    # own flag rather than one "personalizable" bool + a config blob, so the
    # admin form stays a plain checkbox group and the product page only ever
    # renders the fields that actually apply to this product.
    personalize_name: Mapped[bool] = mapped_column(Boolean, default=False)
    personalize_message: Mapped[bool] = mapped_column(Boolean, default=False)
    personalize_date: Mapped[bool] = mapped_column(Boolean, default=False)
    personalize_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    # Soft delete: NULL = not deleted (the common case). A separate concept
    # from `active` (which just hides a product from customers while keeping
    # it in normal admin views) — a deleted product is filtered out of every
    # admin view too except the dedicated "Deleted Products" page.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    category: Mapped["Category | None"] = relationship(back_populates="products")
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductImage.sort_order"
    )
    collections: Mapped[list["Collection"]] = relationship(secondary=product_collections, back_populates="products")

    @property
    def discount_percent(self) -> int | None:
        if self.original_price and self.original_price > self.price:
            return round((1 - self.price / self.original_price) * 100)
        return None

    @property
    def in_stock(self) -> bool:
        return self.active and self.stock > 0

    @property
    def primary_image(self) -> "ProductImage | None":
        return self.images[0] if self.images else None

    @property
    def is_personalizable(self) -> bool:
        return self.personalize_name or self.personalize_message or self.personalize_date or self.personalize_photo


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    image_url: Mapped[str] = mapped_column(String(500))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="images")

    @property
    def display_thumbnail(self) -> str:
        """Older rows uploaded before thumbnails existed have none — falling
        back to the full image keeps them rendering exactly as before."""
        return self.thumbnail_url or self.image_url


class Customer(Base):
    """
    A customer account, identified by mobile number (the login). One row per
    mobile — reused across every order that customer places, unlike orders
    themselves, which snapshot delivery details independently (see Order
    below) so editing a saved address here never rewrites past orders.
    """

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    mobile: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Saved/default address, used only to pre-fill checkout — not the source
    # of truth for any past order (Order.delivery_* fields are).
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(12), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # Delivery snapshot at the time THIS order was placed — independent of
    # the customer's saved address, so a later profile edit (or a different
    # shipping address on a future order) never changes this order's record.
    delivery_name: Mapped[str] = mapped_column(String(150))
    delivery_mobile: Mapped[str] = mapped_column(String(20))
    delivery_address: Mapped[str] = mapped_column(Text)
    delivery_city: Mapped[str] = mapped_column(String(120))
    delivery_state: Mapped[str] = mapped_column(String(120))
    delivery_pincode: Mapped[str] = mapped_column(String(12))
    subtotal: Mapped[float] = mapped_column(Float)
    delivery_charge: Mapped[float] = mapped_column(Float, default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    discount_amount: Mapped[float] = mapped_column(Float, default=0)
    total: Mapped[float] = mapped_column(Float)
    payment_method: Mapped[str] = mapped_column(String(40), default="UPI / Bank Transfer")
    payment_status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PENDING.value)
    order_status: Mapped[str] = mapped_column(String(30), default=OrderStatus.PLACED.value)
    courier_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    estimated_delivery: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    viewed_by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    stock_restored: Mapped[bool] = mapped_column(Boolean, default=False)
    gift_wrap: Mapped[bool] = mapped_column(Boolean, default=False)
    gift_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Sum of the priced OrderGiftOption rows below — kept as its own column
    # (rather than summed on read) so it's included directly in the order
    # total the same way subtotal/delivery_charge/discount_amount are.
    gift_charges: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    gift_options: Mapped[list["OrderGiftOption"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.created_at"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(200))
    price_snapshot: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[float] = mapped_column(Float)
    # What the customer entered in the product's "Personalize This Gift"
    # panel, captured at add-to-cart time and snapshotted here at checkout —
    # never read back from the product, so it survives even if the product's
    # personalization options change or are turned off later.
    personalization_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    personalization_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    personalization_date: Mapped[str | None] = mapped_column(String(60), nullable=True)
    personalization_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")

    @property
    def has_personalization(self) -> bool:
        return bool(
            self.personalization_name
            or self.personalization_message
            or self.personalization_date
            or self.personalization_photo_url
        )


class OrderGiftOption(Base):
    """Snapshot of one gift extra the customer added at checkout — its own
    name/price, independent of the GiftOption row (which the admin may later
    edit, deactivate, or delete), so past orders always show what was
    actually charged."""

    __tablename__ = "order_gift_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    name_snapshot: Mapped[str] = mapped_column(String(80))
    price_snapshot: Mapped[float] = mapped_column(Float)

    order: Mapped["Order"] = relationship(back_populates="gift_options")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    order: Mapped["Order"] = relationship(back_populates="status_history")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    review_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Admin moderates every review before it's public — a submission (new or
    # edited) always starts unapproved, even if a prior version of the same
    # customer's review for this product had already been approved.
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    product: Mapped["Product"] = relationship()
    customer: Mapped["Customer"] = relationship()

    __table_args__ = (UniqueConstraint("product_id", "customer_id", name="uq_review_product_customer"),)


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(10), default="flat")  # "flat" | "percent"
    discount_value: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    min_order_value: Mapped[float] = mapped_column(Float, default=0)
    # None = unlimited uses. used_count is only ever incremented inside the
    # same row-locked transaction as order creation (see checkout.py), so two
    # customers racing for the last use of a limited coupon can't both win.
    usage_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Collection(Base):
    """Admin-created product groupings that products can be tagged into —
    separate from Category, which is the product's one structural department
    (Toys, Stationery...). A product can belong to any number of collections.

    `kind` distinguishes what a collection groups by: "occasion" (Diwali,
    Birthday, Wedding...) or "age" (0-2 Years, Teens...) — same underlying
    model, tagging UI, and detail-page rendering for both, just filtered and
    labeled differently on the customer-facing pages."""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20), default="occasion")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    products: Mapped[list["Product"]] = relationship(secondary=product_collections, back_populates="collections")


class ProductEvent(Base):
    """One row per lightweight engagement signal — product page view,
    WhatsApp enquiry click, add-to-cart. No customer/session identifier is
    stored (privacy-conscious: this is aggregate interest, not a visitor
    profile), so events can only ever be counted, never tied back to a
    person."""

    __tablename__ = "product_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)  # "view" | "enquiry" | "add_to_cart"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class GiftOption(Base):
    """Admin-managed catalog of paid add-ons offered at checkout (gift wrap,
    greeting card, etc). Deleting one doesn't touch past orders — those keep
    their own OrderGiftOption snapshot."""

    __tablename__ = "gift_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    price: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Wishlist(Base):
    __tablename__ = "wishlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    customer: Mapped["Customer"] = relationship()
    product: Mapped["Product"] = relationship()

    __table_args__ = (UniqueConstraint("customer_id", "product_id", name="uq_wishlist_customer_product"),)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AuditLog(Base):
    """Admin activity trail — who did what, when. admin_id is nullable so a
    log entry can outlive the admin account that created it (rather than
    cascade-deleting history if an admin account is ever removed)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), nullable=True, index=True)
    admin_name: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(60), index=True)
    description: Mapped[str] = mapped_column(String(500))
    related_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    related_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

    admin: Mapped["Admin | None"] = relationship()


class Enquiry(Base):
    __tablename__ = "enquiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(150))
    customer_phone: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="New", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    product: Mapped["Product | None"] = relationship()


class QuotationStatus(str, enum.Enum):
    DRAFT = "Draft"
    SENT = "Sent"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    EXPIRED = "Expired"
    CONVERTED = "Converted"


class Quotation(Base):
    """A priced proposal sent to a prospective or existing customer before
    any money changes hands — not an order. GST here is India's standard
    dual-rate split: CGST+SGST when the sale is within the same state as
    the store, IGST (the same total rate, undivided) across state lines."""

    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(150))
    customer_phone: Mapped[str] = mapped_column(String(20))
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=QuotationStatus.DRAFT.value, index=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0)
    additional_charges: Mapped[float] = mapped_column(Float, default=0)
    additional_charges_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gift_charges: Mapped[float] = mapped_column(Float, default=0)
    gst_rate: Mapped[float] = mapped_column(Float, default=0)  # percent, e.g. 18 for 18%
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False)  # IGST vs CGST+SGST split
    tax_amount: Mapped[float] = mapped_column(Float, default=0)
    total: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    items: Mapped[list["QuotationItem"]] = relationship(back_populates="quotation", cascade="all, delete-orphan", order_by="QuotationItem.sort_order")
    gift_options: Mapped[list["QuotationGiftOption"]] = relationship(back_populates="quotation", cascade="all, delete-orphan")
    customer: Mapped["Customer | None"] = relationship()
    admin: Mapped["Admin | None"] = relationship()

    @property
    def cgst_amount(self) -> float:
        return 0 if self.is_interstate else round(self.tax_amount / 2, 2)

    @property
    def sgst_amount(self) -> float:
        return 0 if self.is_interstate else round(self.tax_amount / 2, 2)

    @property
    def igst_amount(self) -> float:
        return round(self.tax_amount, 2) if self.is_interstate else 0


class QuotationItem(Base):
    __tablename__ = "quotation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(200))
    sku_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    unit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    discount_percent: Mapped[float] = mapped_column(Float, default=0)
    subtotal: Mapped[float] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    quotation: Mapped["Quotation"] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()


class QuotationGiftOption(Base):
    __tablename__ = "quotation_gift_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    name_snapshot: Mapped[str] = mapped_column(String(80))
    price_snapshot: Mapped[float] = mapped_column(Float)

    quotation: Mapped["Quotation"] = relationship(back_populates="gift_options")


class PaymentStatusFull(str, enum.Enum):
    DRAFT = "Draft"
    UNPAID = "Unpaid"
    PARTIALLY_PAID = "Partially Paid"
    PAID = "Paid"
    CANCELLED = "Cancelled"


class Invoice(Base):
    """Structurally the same document as a Quotation (customer, line items,
    GST, charges) but represents a committed bill rather than a proposal —
    it carries its own payment_status and can optionally trace back to the
    quotation it was converted from."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    quotation_id: Mapped[int | None] = mapped_column(ForeignKey("quotations.id", ondelete="SET NULL"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(150))
    customer_phone: Mapped[str] = mapped_column(String(20))
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    payment_status: Mapped[str] = mapped_column(String(20), default=PaymentStatusFull.DRAFT.value, index=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0)
    additional_charges: Mapped[float] = mapped_column(Float, default=0)
    additional_charges_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gift_charges: Mapped[float] = mapped_column(Float, default=0)
    gst_rate: Mapped[float] = mapped_column(Float, default=0)
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_amount: Mapped[float] = mapped_column(Float, default=0)
    total: Mapped[float] = mapped_column(Float, default=0)
    amount_paid: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    items: Mapped[list["InvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.sort_order")
    gift_options: Mapped[list["InvoiceGiftOption"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    customer: Mapped["Customer | None"] = relationship()
    admin: Mapped["Admin | None"] = relationship()
    quotation: Mapped["Quotation | None"] = relationship()

    @property
    def cgst_amount(self) -> float:
        return 0 if self.is_interstate else round(self.tax_amount / 2, 2)

    @property
    def sgst_amount(self) -> float:
        return 0 if self.is_interstate else round(self.tax_amount / 2, 2)

    @property
    def igst_amount(self) -> float:
        return round(self.tax_amount, 2) if self.is_interstate else 0

    @property
    def balance_due(self) -> float:
        return round(self.total - self.amount_paid, 2)


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(200))
    sku_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    unit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    discount_percent: Mapped[float] = mapped_column(Float, default=0)
    subtotal: Mapped[float] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    invoice: Mapped["Invoice"] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()


class InvoiceGiftOption(Base):
    __tablename__ = "invoice_gift_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    name_snapshot: Mapped[str] = mapped_column(String(80))
    price_snapshot: Mapped[float] = mapped_column(Float)

    invoice: Mapped["Invoice"] = relationship(back_populates="gift_options")


Index("ix_orders_status_created", Order.order_status, Order.created_at)
# Every customer-facing product listing (home, shop, category, search,
# product detail) filters on exactly this pair — see base_active_query().
Index("ix_products_active_deleted", Product.active, Product.deleted_at)
