"""Shared money math for quotations and invoices — every total is computed
here, server-side, from validated inputs. Never trust a client-submitted
total; this is the one place the arithmetic happens."""


def line_subtotal(unit_price: float, quantity: int, discount_percent: float) -> float:
    return round(unit_price * quantity * (1 - discount_percent / 100), 2)


def compute_document_totals(
    item_subtotals: list[float],
    discount_amount: float,
    gift_charges: float,
    additional_charges: float,
    gst_rate: float,
) -> dict:
    subtotal = round(sum(item_subtotals), 2)
    taxable = round(max(subtotal - discount_amount, 0) + gift_charges + additional_charges, 2)
    tax_amount = round(taxable * gst_rate / 100, 2)
    total = round(taxable + tax_amount, 2)
    return {"subtotal": subtotal, "tax_amount": tax_amount, "total": total}
