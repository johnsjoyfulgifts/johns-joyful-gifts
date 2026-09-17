"""Branded PDF generation for quotations and invoices, built with reportlab
(pure Python — no system-level dependencies, so it runs unmodified on
Render's slim container). Quotation and Invoice are structurally the same
document, so one builder handles both via a small field-name adapter."""

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BRAND_OLIVE = colors.HexColor("#6B7350")
BRAND_CREAM = colors.HexColor("#F5F0DC")
BORDER_GREY = colors.HexColor("#D8D2C0")


def _adapt(doc, doc_type: str) -> dict:
    if doc_type == "quotation":
        return {
            "number_label": "Quotation No.",
            "number": doc.quotation_number,
            "status_label": "Status",
            "status": doc.status,
            "extra_date_label": "Valid Until",
            "extra_date": doc.valid_until.strftime("%d %b %Y") if doc.valid_until else "—",
        }
    return {
        "number_label": "Invoice No.",
        "number": doc.invoice_number,
        "status_label": "Payment Status",
        "status": doc.payment_status,
        "extra_date_label": "Amount Paid",
        "extra_date": f"Rs. {doc.amount_paid:,.2f}",
    }


def build_document_pdf(doc, doc_type: str, store_values: dict) -> bytes:
    """doc_type is 'quotation' or 'invoice'. Returns PDF bytes."""
    meta = _adapt(doc, doc_type)
    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"{meta['number']} — {store_values.get('store_name', 'Store')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Brand", parent=styles["Title"], textColor=BRAND_OLIVE, fontSize=18, spaceAfter=2)
    doc_type_style = ParagraphStyle("DocType", parent=styles["Normal"], textColor=BRAND_OLIVE, fontSize=13, spaceAfter=10)
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    normal = styles["Normal"]
    right_normal = ParagraphStyle("RightNormal", parent=normal, alignment=TA_RIGHT)
    small = ParagraphStyle("Small", parent=normal, fontSize=8, textColor=colors.grey)

    elements = []

    # Header: store name + doc type, right-aligned meta
    header_table = Table(
        [[
            Paragraph(store_values.get("store_name", "Store"), title_style),
            Paragraph(f"{meta['number_label']}<br/><b>{meta['number']}</b>", right_normal),
        ]],
        colWidths=[110 * mm, 62 * mm],
    )
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_table)
    elements.append(Paragraph(doc_type.upper(), doc_type_style))

    if store_values.get("contact_address") or store_values.get("gst_number"):
        addr_lines = []
        if store_values.get("contact_address"):
            addr_lines.append(store_values["contact_address"])
        if store_values.get("gst_number"):
            addr_lines.append(f"GSTIN: {store_values['gst_number']}")
        elements.append(Paragraph("<br/>".join(addr_lines), small))
        elements.append(Spacer(1, 8))

    # Meta row: date / status / extra date
    meta_table = Table(
        [[
            Paragraph(f"Date<br/><b>{doc.created_at.strftime('%d %b %Y')}</b>", label_style),
            Paragraph(f"{meta['status_label']}<br/><b>{meta['status']}</b>", label_style),
            Paragraph(f"{meta['extra_date_label']}<br/><b>{meta['extra_date']}</b>", label_style),
        ]],
        colWidths=[57 * mm, 57 * mm, 58 * mm],
    )
    elements.append(meta_table)
    elements.append(Spacer(1, 12))

    # Customer block
    customer_lines = [f"<b>{doc.customer_name}</b>", doc.customer_phone]
    if doc.customer_email:
        customer_lines.append(doc.customer_email)
    if doc.customer_address:
        customer_lines.append(doc.customer_address.replace("\n", "<br/>"))
    elements.append(Paragraph("Billed To", label_style))
    elements.append(Paragraph("<br/>".join(customer_lines), normal))
    elements.append(Spacer(1, 14))

    # Line items
    item_rows = [["#", "Item", "SKU", "Qty", "Unit Price", "Discount", "Amount"]]
    for idx, item in enumerate(doc.items, start=1):
        item_rows.append([
            str(idx),
            item.product_name_snapshot,
            item.sku_snapshot or "—",
            str(item.quantity),
            f"Rs. {item.unit_price:,.2f}",
            f"{item.discount_percent:.0f}%" if item.discount_percent else "—",
            f"Rs. {item.subtotal:,.2f}",
        ])
    items_table = Table(item_rows, colWidths=[8 * mm, 55 * mm, 22 * mm, 12 * mm, 25 * mm, 20 * mm, 30 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_CREAM]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 6))

    for opt in doc.gift_options:
        elements.append(Paragraph(f"+ {opt.name_snapshot}: Rs. {opt.price_snapshot:,.2f}", small))

    elements.append(Spacer(1, 10))

    # Totals block
    totals_rows = [["Subtotal", f"Rs. {doc.subtotal:,.2f}"]]
    if doc.discount_amount:
        totals_rows.append(["Discount", f"− Rs. {doc.discount_amount:,.2f}"])
    if doc.gift_charges:
        totals_rows.append(["Gift Extras", f"Rs. {doc.gift_charges:,.2f}"])
    if doc.additional_charges:
        label = "Additional Charges" + (f" ({doc.additional_charges_note})" if doc.additional_charges_note else "")
        totals_rows.append([label, f"Rs. {doc.additional_charges:,.2f}"])
    if doc.gst_rate:
        if doc.is_interstate:
            totals_rows.append([f"IGST ({doc.gst_rate:.1f}%)", f"Rs. {doc.igst_amount:,.2f}"])
        else:
            half_rate = doc.gst_rate / 2
            totals_rows.append([f"CGST ({half_rate:.1f}%)", f"Rs. {doc.cgst_amount:,.2f}"])
            totals_rows.append([f"SGST ({half_rate:.1f}%)", f"Rs. {doc.sgst_amount:,.2f}"])
    totals_rows.append(["Grand Total", f"Rs. {doc.total:,.2f}"])

    totals_table = Table(totals_rows, colWidths=[50 * mm, 40 * mm], hAlign="RIGHT")
    style_commands = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, BRAND_OLIVE),
        ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("TEXTCOLOR", (0, -1), (-1, -1), BRAND_OLIVE),
    ]
    totals_table.setStyle(TableStyle(style_commands))
    elements.append(totals_table)

    if doc.notes:
        elements.append(Spacer(1, 16))
        elements.append(Paragraph("Notes", label_style))
        elements.append(Paragraph(doc.notes.replace("\n", "<br/>"), normal))

    elements.append(Spacer(1, 24))
    elements.append(Paragraph(f"{store_values.get('store_name', 'Store')} — generated {doc.created_at.strftime('%d %b %Y')}", small))

    pdf.build(elements)
    return buffer.getvalue()
