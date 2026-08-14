import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
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
    role: Mapped[str] = mapped_column(String(30), default="owner")
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
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    category: Mapped["Category | None"] = relationship(back_populates="products")
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductImage.sort_order"
    )

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


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    image_url: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="images")


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
    payment_method: Mapped[str] = mapped_column(String(40), default="Cash on Delivery")
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
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

    order: Mapped["Order"] = relationship(back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    order: Mapped["Order"] = relationship(back_populates="status_history")


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


Index("ix_orders_status_created", Order.order_status, Order.created_at)
