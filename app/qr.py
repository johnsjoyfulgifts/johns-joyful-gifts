"""
Generates UPI payment QR codes locally with the `qrcode` package (pure
Python + Pillow, both already dependencies) -- no external API, no network
call, no cost. The QR just encodes a standard `upi://pay` deep link that
any UPI app can scan.
"""

import io
from urllib.parse import quote

import qrcode


def upi_payment_uri(upi_id: str, payee_name: str, amount: float, note: str) -> str:
    params = (
        f"pa={quote(upi_id)}&pn={quote(payee_name)}"
        f"&am={amount:.2f}&cu=INR&tn={quote(note)}"
    )
    return f"upi://pay?{params}"


def generate_qr_png_bytes(data: str) -> bytes:
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
