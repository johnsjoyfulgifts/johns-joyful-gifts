from urllib.parse import quote

from app.models import Order


def _digits_only(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def whatsapp_chat_link(number: str, message: str = "") -> str:
    digits = _digits_only(number)
    if message:
        return f"https://wa.me/{digits}?text={quote(message)}"
    return f"https://wa.me/{digits}"


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
