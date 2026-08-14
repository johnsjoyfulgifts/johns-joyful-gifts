from urllib.parse import quote

from app.models import Order, Product


def _digits_only(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def whatsapp_chat_link(number: str, message: str = "") -> str:
    digits = _digits_only(number)
    if message:
        return f"https://wa.me/{digits}?text={quote(message)}"
    return f"https://wa.me/{digits}"


def product_enquiry_message(product: Product) -> str:
    return (
        f"Hi, I'm interested in this product: {product.name}.\n"
        f"Product Price: ₹{product.price:.0f}\n"
        "Please share more details."
    )


def cart_enquiry_message(lines, subtotal: float, total: float) -> str:
    parts = ["Hi, I'd like to order the following:", ""]
    for line in lines:
        parts.append(f"- {line.product.name} x{line.quantity} @ ₹{line.product.price:.0f} = ₹{line.subtotal:.0f}")
    parts.append("")
    parts.append(f"Subtotal: ₹{subtotal:.0f}")
    parts.append(f"Cart Total: ₹{total:.0f}")
    parts.append("")
    parts.append("Please confirm availability and delivery details.")
    return "\n".join(parts)


def order_confirmation_message(order: Order) -> str:
    lines = [
        f"Hi! I just placed order {order.order_number} on John's Joyful Gifts.",
        "",
        "Items:",
    ]
    for item in order.items:
        lines.append(f"- {item.product_name_snapshot} x{item.quantity} = Rs. {item.subtotal:.2f}")
    lines.append("")
    lines.append(f"Total: Rs. {order.total:.2f}")
    lines.append(f"Payment: {order.payment_method}")
    return "\n".join(lines)
