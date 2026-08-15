from urllib.parse import quote

from app.models import Order, Product


def _digits_only(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def whatsapp_chat_link(number: str, message: str = "") -> str:
    digits = _digits_only(number)
    if message:
        return f"https://wa.me/{digits}?text={quote(message)}"
    return f"https://wa.me/{digits}"


DEFAULT_PRODUCT_TEMPLATE = (
    "Hi, I'm interested in this product: [PRODUCT_NAME] (SKU: [SKU]).\n"
    "Price: ₹[PRICE]\n"
    "Quantity: [QUANTITY]\n"
    "Link: [PRODUCT_LINK]\n"
    "Please share more details."
)

DEFAULT_CART_TEMPLATE = (
    "Hi, I'd like to order the following:\n\n"
    "[CART_ITEMS]\n\n"
    "Cart Total: ₹[CART_TOTAL]\n\n"
    "Please confirm availability and delivery details."
)


def render_whatsapp_template(template: str, **values) -> str:
    """Fills [PLACEHOLDER] tokens with the given values (missing ones become
    blank). The result still lands as pre-filled, editable text in the
    customer's WhatsApp compose box — nothing here is ever sent automatically."""
    result = template
    for key, value in values.items():
        result = result.replace(f"[{key}]", str(value) if value is not None else "")
    return result


def product_enquiry_message(
    product: Product, template: str = "", quantity: int = 1, customer_name: str = "", product_url: str = ""
) -> str:
    return render_whatsapp_template(
        template or DEFAULT_PRODUCT_TEMPLATE,
        PRODUCT_NAME=product.name,
        SKU=product.sku or "",
        PRICE=f"{product.price:.0f}",
        QUANTITY=quantity,
        CART_TOTAL=f"{product.price * quantity:.0f}",
        CUSTOMER_NAME=customer_name,
        PRODUCT_LINK=product_url,
    )


def cart_enquiry_message(lines, subtotal: float, total: float, template: str = "", customer_name: str = "") -> str:
    items_text = "\n".join(f"- {line.product.name} x{line.quantity} @ ₹{line.product.price:.0f} = ₹{line.subtotal:.0f}" for line in lines)
    total_quantity = sum(line.quantity for line in lines)
    return render_whatsapp_template(
        template or DEFAULT_CART_TEMPLATE,
        CART_ITEMS=items_text,
        QUANTITY=total_quantity,
        CART_TOTAL=f"{total:.0f}",
        CUSTOMER_NAME=customer_name,
    )


def order_confirmation_message(order: Order) -> str:
    lines = [
        f"Hi! I just placed order {order.order_number} on John's Joyful Gifts.",
        "",
        "Items:",
    ]
    for item in order.items:
        lines.append(f"- {item.product_name_snapshot} x{item.quantity} = Rs. {item.subtotal:.2f}")
    if order.gift_options:
        lines.append("")
        lines.append("Gift extras:")
        for opt in order.gift_options:
            lines.append(f"- {opt.name_snapshot} = Rs. {opt.price_snapshot:.2f}")
    lines.append("")
    lines.append(f"Total: Rs. {order.total:.2f}")
    lines.append(f"Payment: {order.payment_method}")
    return "\n".join(lines)
