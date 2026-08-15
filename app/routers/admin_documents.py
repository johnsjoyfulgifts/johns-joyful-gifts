from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session, joinedload

from app.audit import log_activity
from app.auth import require_role, ROLE_ORDER_MANAGER
from app.database import get_db
from app.document_totals import compute_document_totals, line_subtotal
from app.models import (
    Admin,
    GiftOption,
    Invoice,
    InvoiceGiftOption,
    InvoiceItem,
    PaymentStatusFull,
    Product,
    Quotation,
    QuotationGiftOption,
    QuotationItem,
    QuotationStatus,
)
from app.pdf_service import build_document_pdf
from app.settings_service import get_all_settings
from app.templating import render_admin
from app.utils.document_number import generate_document_number
from app.utils.whatsapp import render_whatsapp_template, whatsapp_chat_link

router = APIRouter(prefix="/admin")

require_billing_admin = require_role(ROLE_ORDER_MANAGER)

QUOTATION_STATUSES = [s.value for s in QuotationStatus]
INVOICE_PAYMENT_STATUSES = [s.value for s in PaymentStatusFull]

DEFAULT_QUOTATION_TEMPLATE = (
    "Hi [CUSTOMER_NAME], here is your quotation [QUOTATION_NUMBER] for a total of "
    "Rs. [TOTAL]. Valid until [VALID_UNTIL]. Please let us know if you'd like to proceed!"
)


def _parse_line_items(
    product_ids: list[str], names: list[str], skus: list[str], quantities: list[str], prices: list[str], discounts: list[str]
) -> list[dict]:
    items = []
    for i in range(len(names)):
        name = (names[i] if i < len(names) else "").strip()
        if not name:
            continue
        qty = max(int(quantities[i]) if i < len(quantities) and quantities[i] else 1, 1)
        price = max(float(prices[i]) if i < len(prices) and prices[i] else 0.0, 0.0)
        discount = min(max(float(discounts[i]) if i < len(discounts) and discounts[i] else 0.0, 0.0), 100.0)
        product_id_raw = product_ids[i] if i < len(product_ids) else ""
        items.append(
            {
                "product_id": int(product_id_raw) if product_id_raw else None,
                "name": name,
                "sku": (skus[i] if i < len(skus) else "").strip() or None,
                "unit_price": price,
                "quantity": qty,
                "discount_percent": discount,
                "subtotal": line_subtotal(price, qty, discount),
            }
        )
    return items


def _parse_gift_option_ids(raw_ids: list[str]) -> list[int]:
    return [int(v) for v in raw_ids if v]


def _parse_valid_until(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
    except ValueError:
        return None


def _document_form_context(db: Session, doc=None, doc_type: str = "quotation", error: str | None = None) -> dict:
    store_values = get_all_settings(db)
    return {
        "active_nav": "quotations" if doc_type == "quotation" else "invoices",
        "doc": doc,
        "doc_type": doc_type,
        "products": db.query(Product).filter(Product.deleted_at.is_(None)).order_by(Product.name).all(),
        "gift_options": db.query(GiftOption).filter(GiftOption.active.is_(True)).order_by(GiftOption.sort_order).all(),
        "default_gst_rate": store_values.get("default_gst_rate", "18"),
        "statuses": QUOTATION_STATUSES if doc_type == "quotation" else INVOICE_PAYMENT_STATUSES,
        "error": error,
    }


# ---------- Quotations ----------

@router.get("/quotations")
def quotations_list(
    request: Request, status: str = "", db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)
):
    query = db.query(Quotation)
    if status in QUOTATION_STATUSES:
        query = query.filter(Quotation.status == status)
    quotations = query.order_by(Quotation.created_at.desc()).all()
    return render_admin(
        request,
        "admin/quotations_list.html",
        {"active_nav": "quotations", "quotations": quotations, "status": status, "statuses": QUOTATION_STATUSES},
        db,
    )


@router.get("/quotations/new")
def quotation_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    return render_admin(request, "admin/quotation_form.html", _document_form_context(db), db)


@router.post("/quotations/new")
def quotation_new_submit(
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    customer_email: str = Form(""),
    customer_address: str = Form(""),
    valid_until: str = Form(""),
    discount_amount: float = Form(0),
    additional_charges: float = Form(0),
    additional_charges_note: str = Form(""),
    gst_rate: float = Form(0),
    is_interstate: bool = Form(False),
    notes: str = Form(""),
    gift_option_ids: list[str] = Form(default=[]),
    item_product_id: list[str] = Form(default=[]),
    item_name: list[str] = Form(default=[]),
    item_sku: list[str] = Form(default=[]),
    item_qty: list[str] = Form(default=[]),
    item_price: list[str] = Form(default=[]),
    item_discount: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_billing_admin),
):
    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    items = _parse_line_items(item_product_id, item_name, item_sku, item_qty, item_price, item_discount)
    error = None
    if not customer_name:
        error = "Customer name is required."
    elif not customer_phone:
        error = "Customer phone is required."
    elif not items:
        error = "Add at least one line item."

    if error:
        return render_admin(request, "admin/quotation_form.html", _document_form_context(db, error=error), db, status_code=400)

    selected_gift_options = (
        db.query(GiftOption).filter(GiftOption.id.in_(_parse_gift_option_ids(gift_option_ids))).all()
        if gift_option_ids
        else []
    )
    gift_charges = round(sum(o.price for o in selected_gift_options), 2)
    totals = compute_document_totals([i["subtotal"] for i in items], discount_amount, gift_charges, additional_charges, gst_rate)

    quotation = Quotation(
        quotation_number=generate_document_number(db, Quotation, Quotation.quotation_number, "QT"),
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email.strip() or None,
        customer_address=customer_address.strip() or None,
        discount_amount=max(discount_amount, 0),
        additional_charges=max(additional_charges, 0),
        additional_charges_note=additional_charges_note.strip() or None,
        gift_charges=gift_charges,
        gst_rate=min(max(gst_rate, 0), 100),
        is_interstate=is_interstate,
        notes=notes.strip() or None,
        valid_until=_parse_valid_until(valid_until),
        admin_id=admin.id,
        **totals,
    )
    db.add(quotation)
    db.flush()

    for idx, item in enumerate(items):
        db.add(QuotationItem(quotation_id=quotation.id, sort_order=idx, product_id=item["product_id"],
                              product_name_snapshot=item["name"], sku_snapshot=item["sku"],
                              unit_price=item["unit_price"], quantity=item["quantity"],
                              discount_percent=item["discount_percent"], subtotal=item["subtotal"]))
    for opt in selected_gift_options:
        db.add(QuotationGiftOption(quotation_id=quotation.id, name_snapshot=opt.name, price_snapshot=opt.price))

    log_activity(db, admin, "quotation.created", f"Created quotation {quotation.quotation_number} for {customer_name} (Rs. {quotation.total:.2f})", "quotation", quotation.id)
    db.commit()
    return RedirectResponse(url=f"/admin/quotations/{quotation.id}", status_code=303)


@router.get("/quotations/{quotation_id}")
def quotation_view(quotation_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    quotation = (
        db.query(Quotation)
        .options(joinedload(Quotation.items), joinedload(Quotation.gift_options))
        .filter(Quotation.id == quotation_id)
        .first()
    )
    if quotation is None:
        return RedirectResponse(url="/admin/quotations", status_code=303)

    store_values = get_all_settings(db)
    whatsapp_link = None
    if quotation.customer_phone:
        message = render_whatsapp_template(
            store_values.get("whatsapp_quotation_template", "") or DEFAULT_QUOTATION_TEMPLATE,
            CUSTOMER_NAME=quotation.customer_name,
            QUOTATION_NUMBER=quotation.quotation_number,
            TOTAL=f"{quotation.total:.2f}",
            VALID_UNTIL=quotation.valid_until.strftime("%d %b %Y") if quotation.valid_until else "N/A",
        )
        whatsapp_link = whatsapp_chat_link(quotation.customer_phone, message)

    return render_admin(
        request,
        "admin/quotation_view.html",
        {"active_nav": "quotations", "doc": quotation, "doc_type": "quotation", "whatsapp_link": whatsapp_link, "statuses": QUOTATION_STATUSES},
        db,
    )


@router.get("/quotations/{quotation_id}/edit")
def quotation_edit_page(quotation_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    quotation = db.query(Quotation).options(joinedload(Quotation.items), joinedload(Quotation.gift_options)).filter(Quotation.id == quotation_id).first()
    if quotation is None:
        return RedirectResponse(url="/admin/quotations", status_code=303)
    return render_admin(request, "admin/quotation_form.html", _document_form_context(db, doc=quotation), db)


@router.post("/quotations/{quotation_id}/edit")
def quotation_edit_submit(
    quotation_id: int,
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    customer_email: str = Form(""),
    customer_address: str = Form(""),
    valid_until: str = Form(""),
    status: str = Form(QuotationStatus.DRAFT.value),
    discount_amount: float = Form(0),
    additional_charges: float = Form(0),
    additional_charges_note: str = Form(""),
    gst_rate: float = Form(0),
    is_interstate: bool = Form(False),
    notes: str = Form(""),
    gift_option_ids: list[str] = Form(default=[]),
    item_product_id: list[str] = Form(default=[]),
    item_name: list[str] = Form(default=[]),
    item_sku: list[str] = Form(default=[]),
    item_qty: list[str] = Form(default=[]),
    item_price: list[str] = Form(default=[]),
    item_discount: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_billing_admin),
):
    quotation = db.query(Quotation).options(joinedload(Quotation.items), joinedload(Quotation.gift_options)).filter(Quotation.id == quotation_id).first()
    if quotation is None:
        return RedirectResponse(url="/admin/quotations", status_code=303)

    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    items = _parse_line_items(item_product_id, item_name, item_sku, item_qty, item_price, item_discount)
    error = None
    if not customer_name:
        error = "Customer name is required."
    elif not customer_phone:
        error = "Customer phone is required."
    elif not items:
        error = "Add at least one line item."
    elif status not in QUOTATION_STATUSES:
        error = "Invalid status."

    if error:
        return render_admin(request, "admin/quotation_form.html", _document_form_context(db, doc=quotation, error=error), db, status_code=400)

    selected_gift_options = (
        db.query(GiftOption).filter(GiftOption.id.in_(_parse_gift_option_ids(gift_option_ids))).all()
        if gift_option_ids
        else []
    )
    gift_charges = round(sum(o.price for o in selected_gift_options), 2)
    totals = compute_document_totals([i["subtotal"] for i in items], discount_amount, gift_charges, additional_charges, gst_rate)

    quotation.customer_name = customer_name
    quotation.customer_phone = customer_phone
    quotation.customer_email = customer_email.strip() or None
    quotation.customer_address = customer_address.strip() or None
    quotation.status = status
    quotation.discount_amount = max(discount_amount, 0)
    quotation.additional_charges = max(additional_charges, 0)
    quotation.additional_charges_note = additional_charges_note.strip() or None
    quotation.gift_charges = gift_charges
    quotation.gst_rate = min(max(gst_rate, 0), 100)
    quotation.is_interstate = is_interstate
    quotation.notes = notes.strip() or None
    quotation.valid_until = _parse_valid_until(valid_until)
    for key, value in totals.items():
        setattr(quotation, key, value)

    for existing in list(quotation.items):
        db.delete(existing)
    for existing in list(quotation.gift_options):
        db.delete(existing)
    db.flush()
    for idx, item in enumerate(items):
        db.add(QuotationItem(quotation_id=quotation.id, sort_order=idx, product_id=item["product_id"],
                              product_name_snapshot=item["name"], sku_snapshot=item["sku"],
                              unit_price=item["unit_price"], quantity=item["quantity"],
                              discount_percent=item["discount_percent"], subtotal=item["subtotal"]))
    for opt in selected_gift_options:
        db.add(QuotationGiftOption(quotation_id=quotation.id, name_snapshot=opt.name, price_snapshot=opt.price))

    log_activity(db, admin, "quotation.updated", f"Updated quotation {quotation.quotation_number} (Rs. {quotation.total:.2f})", "quotation", quotation.id)
    db.commit()
    return RedirectResponse(url=f"/admin/quotations/{quotation.id}", status_code=303)


@router.post("/quotations/{quotation_id}/status")
def quotation_status_update(quotation_id: int, status: str = Form(...), db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    quotation = db.get(Quotation, quotation_id)
    if quotation is not None and status in QUOTATION_STATUSES:
        quotation.status = status
        log_activity(db, admin, "quotation.status_changed", f"Quotation {quotation.quotation_number} marked {status}", "quotation", quotation.id)
        db.commit()
    return RedirectResponse(url=f"/admin/quotations/{quotation_id}", status_code=303)


@router.post("/quotations/{quotation_id}/delete")
def quotation_delete(quotation_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    quotation = db.get(Quotation, quotation_id)
    if quotation is not None:
        number = quotation.quotation_number
        db.delete(quotation)
        log_activity(db, admin, "quotation.deleted", f"Deleted quotation {number}")
        db.commit()
    return RedirectResponse(url="/admin/quotations", status_code=303)


@router.get("/quotations/{quotation_id}/pdf")
def quotation_pdf(quotation_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    quotation = db.query(Quotation).options(joinedload(Quotation.items), joinedload(Quotation.gift_options)).filter(Quotation.id == quotation_id).first()
    if quotation is None:
        return RedirectResponse(url="/admin/quotations", status_code=303)
    pdf_bytes = build_document_pdf(quotation, "quotation", get_all_settings(db))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{quotation.quotation_number}.pdf"'},
    )


@router.post("/quotations/{quotation_id}/convert-to-invoice")
def quotation_convert_to_invoice(quotation_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    quotation = db.query(Quotation).options(joinedload(Quotation.items), joinedload(Quotation.gift_options)).filter(Quotation.id == quotation_id).first()
    if quotation is None:
        return RedirectResponse(url="/admin/quotations", status_code=303)

    invoice = Invoice(
        invoice_number=generate_document_number(db, Invoice, Invoice.invoice_number, "INV"),
        quotation_id=quotation.id,
        customer_name=quotation.customer_name,
        customer_phone=quotation.customer_phone,
        customer_email=quotation.customer_email,
        customer_address=quotation.customer_address,
        customer_id=quotation.customer_id,
        payment_status=PaymentStatusFull.UNPAID.value,
        subtotal=quotation.subtotal,
        discount_amount=quotation.discount_amount,
        additional_charges=quotation.additional_charges,
        additional_charges_note=quotation.additional_charges_note,
        gift_charges=quotation.gift_charges,
        gst_rate=quotation.gst_rate,
        is_interstate=quotation.is_interstate,
        tax_amount=quotation.tax_amount,
        total=quotation.total,
        notes=quotation.notes,
        admin_id=admin.id,
    )
    db.add(invoice)
    db.flush()
    for idx, item in enumerate(quotation.items):
        db.add(InvoiceItem(invoice_id=invoice.id, sort_order=idx, product_id=item.product_id,
                            product_name_snapshot=item.product_name_snapshot, sku_snapshot=item.sku_snapshot,
                            unit_price=item.unit_price, quantity=item.quantity,
                            discount_percent=item.discount_percent, subtotal=item.subtotal))
    for opt in quotation.gift_options:
        db.add(InvoiceGiftOption(invoice_id=invoice.id, name_snapshot=opt.name_snapshot, price_snapshot=opt.price_snapshot))

    quotation.status = QuotationStatus.CONVERTED.value
    log_activity(db, admin, "invoice.created", f"Converted quotation {quotation.quotation_number} to invoice {invoice.invoice_number}", "invoice", invoice.id)
    db.commit()
    return RedirectResponse(url=f"/admin/invoices/{invoice.id}", status_code=303)


# ---------- Invoices ----------

@router.get("/invoices")
def invoices_list(
    request: Request, status: str = "", db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)
):
    query = db.query(Invoice)
    if status in INVOICE_PAYMENT_STATUSES:
        query = query.filter(Invoice.payment_status == status)
    invoices = query.order_by(Invoice.created_at.desc()).all()
    return render_admin(
        request,
        "admin/invoices_list.html",
        {"active_nav": "invoices", "invoices": invoices, "status": status, "statuses": INVOICE_PAYMENT_STATUSES},
        db,
    )


@router.get("/invoices/new")
def invoice_new_page(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    return render_admin(request, "admin/invoice_form.html", _document_form_context(db, doc_type="invoice"), db)


@router.post("/invoices/new")
def invoice_new_submit(
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    customer_email: str = Form(""),
    customer_address: str = Form(""),
    discount_amount: float = Form(0),
    additional_charges: float = Form(0),
    additional_charges_note: str = Form(""),
    gst_rate: float = Form(0),
    is_interstate: bool = Form(False),
    notes: str = Form(""),
    gift_option_ids: list[str] = Form(default=[]),
    item_product_id: list[str] = Form(default=[]),
    item_name: list[str] = Form(default=[]),
    item_sku: list[str] = Form(default=[]),
    item_qty: list[str] = Form(default=[]),
    item_price: list[str] = Form(default=[]),
    item_discount: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_billing_admin),
):
    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    items = _parse_line_items(item_product_id, item_name, item_sku, item_qty, item_price, item_discount)
    error = None
    if not customer_name:
        error = "Customer name is required."
    elif not customer_phone:
        error = "Customer phone is required."
    elif not items:
        error = "Add at least one line item."

    if error:
        return render_admin(request, "admin/invoice_form.html", _document_form_context(db, doc_type="invoice", error=error), db, status_code=400)

    selected_gift_options = (
        db.query(GiftOption).filter(GiftOption.id.in_(_parse_gift_option_ids(gift_option_ids))).all()
        if gift_option_ids
        else []
    )
    gift_charges = round(sum(o.price for o in selected_gift_options), 2)
    totals = compute_document_totals([i["subtotal"] for i in items], discount_amount, gift_charges, additional_charges, gst_rate)

    invoice = Invoice(
        invoice_number=generate_document_number(db, Invoice, Invoice.invoice_number, "INV"),
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email.strip() or None,
        customer_address=customer_address.strip() or None,
        payment_status=PaymentStatusFull.UNPAID.value,
        discount_amount=max(discount_amount, 0),
        additional_charges=max(additional_charges, 0),
        additional_charges_note=additional_charges_note.strip() or None,
        gift_charges=gift_charges,
        gst_rate=min(max(gst_rate, 0), 100),
        is_interstate=is_interstate,
        notes=notes.strip() or None,
        admin_id=admin.id,
        **totals,
    )
    db.add(invoice)
    db.flush()
    for idx, item in enumerate(items):
        db.add(InvoiceItem(invoice_id=invoice.id, sort_order=idx, product_id=item["product_id"],
                            product_name_snapshot=item["name"], sku_snapshot=item["sku"],
                            unit_price=item["unit_price"], quantity=item["quantity"],
                            discount_percent=item["discount_percent"], subtotal=item["subtotal"]))
    for opt in selected_gift_options:
        db.add(InvoiceGiftOption(invoice_id=invoice.id, name_snapshot=opt.name, price_snapshot=opt.price))

    log_activity(db, admin, "invoice.created", f"Created invoice {invoice.invoice_number} for {customer_name} (Rs. {invoice.total:.2f})", "invoice", invoice.id)
    db.commit()
    return RedirectResponse(url=f"/admin/invoices/{invoice.id}", status_code=303)


@router.get("/invoices/{invoice_id}")
def invoice_view(invoice_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.gift_options))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if invoice is None:
        return RedirectResponse(url="/admin/invoices", status_code=303)
    return render_admin(
        request,
        "admin/invoice_view.html",
        {"active_nav": "invoices", "doc": invoice, "doc_type": "invoice", "statuses": INVOICE_PAYMENT_STATUSES},
        db,
    )


@router.get("/invoices/{invoice_id}/edit")
def invoice_edit_page(invoice_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    invoice = db.query(Invoice).options(joinedload(Invoice.items), joinedload(Invoice.gift_options)).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        return RedirectResponse(url="/admin/invoices", status_code=303)
    return render_admin(request, "admin/invoice_form.html", _document_form_context(db, doc=invoice, doc_type="invoice"), db)


@router.post("/invoices/{invoice_id}/edit")
def invoice_edit_submit(
    invoice_id: int,
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    customer_email: str = Form(""),
    customer_address: str = Form(""),
    discount_amount: float = Form(0),
    additional_charges: float = Form(0),
    additional_charges_note: str = Form(""),
    gst_rate: float = Form(0),
    is_interstate: bool = Form(False),
    notes: str = Form(""),
    gift_option_ids: list[str] = Form(default=[]),
    item_product_id: list[str] = Form(default=[]),
    item_name: list[str] = Form(default=[]),
    item_sku: list[str] = Form(default=[]),
    item_qty: list[str] = Form(default=[]),
    item_price: list[str] = Form(default=[]),
    item_discount: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_billing_admin),
):
    invoice = db.query(Invoice).options(joinedload(Invoice.items), joinedload(Invoice.gift_options)).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        return RedirectResponse(url="/admin/invoices", status_code=303)

    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    items = _parse_line_items(item_product_id, item_name, item_sku, item_qty, item_price, item_discount)
    error = None
    if not customer_name:
        error = "Customer name is required."
    elif not customer_phone:
        error = "Customer phone is required."
    elif not items:
        error = "Add at least one line item."

    if error:
        return render_admin(request, "admin/invoice_form.html", _document_form_context(db, doc=invoice, doc_type="invoice", error=error), db, status_code=400)

    selected_gift_options = (
        db.query(GiftOption).filter(GiftOption.id.in_(_parse_gift_option_ids(gift_option_ids))).all()
        if gift_option_ids
        else []
    )
    gift_charges = round(sum(o.price for o in selected_gift_options), 2)
    totals = compute_document_totals([i["subtotal"] for i in items], discount_amount, gift_charges, additional_charges, gst_rate)

    invoice.customer_name = customer_name
    invoice.customer_phone = customer_phone
    invoice.customer_email = customer_email.strip() or None
    invoice.customer_address = customer_address.strip() or None
    invoice.discount_amount = max(discount_amount, 0)
    invoice.additional_charges = max(additional_charges, 0)
    invoice.additional_charges_note = additional_charges_note.strip() or None
    invoice.gift_charges = gift_charges
    invoice.gst_rate = min(max(gst_rate, 0), 100)
    invoice.is_interstate = is_interstate
    invoice.notes = notes.strip() or None
    for key, value in totals.items():
        setattr(invoice, key, value)

    for existing in list(invoice.items):
        db.delete(existing)
    for existing in list(invoice.gift_options):
        db.delete(existing)
    db.flush()
    for idx, item in enumerate(items):
        db.add(InvoiceItem(invoice_id=invoice.id, sort_order=idx, product_id=item["product_id"],
                            product_name_snapshot=item["name"], sku_snapshot=item["sku"],
                            unit_price=item["unit_price"], quantity=item["quantity"],
                            discount_percent=item["discount_percent"], subtotal=item["subtotal"]))
    for opt in selected_gift_options:
        db.add(InvoiceGiftOption(invoice_id=invoice.id, name_snapshot=opt.name, price_snapshot=opt.price))

    log_activity(db, admin, "invoice.updated", f"Updated invoice {invoice.invoice_number} (Rs. {invoice.total:.2f})", "invoice", invoice.id)
    db.commit()
    return RedirectResponse(url=f"/admin/invoices/{invoice.id}", status_code=303)


@router.post("/invoices/{invoice_id}/payment-status")
def invoice_payment_status_update(
    invoice_id: int,
    payment_status: str = Form(...),
    amount_paid: float = Form(0),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_billing_admin),
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is not None and payment_status in INVOICE_PAYMENT_STATUSES:
        invoice.payment_status = payment_status
        invoice.amount_paid = max(min(amount_paid, invoice.total), 0)
        log_activity(db, admin, "invoice.payment_updated", f"Invoice {invoice.invoice_number} marked {payment_status} (Rs. {invoice.amount_paid:.2f} paid)", "invoice", invoice.id)
        db.commit()
    return RedirectResponse(url=f"/admin/invoices/{invoice_id}", status_code=303)


@router.post("/invoices/{invoice_id}/delete")
def invoice_delete(invoice_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is not None:
        number = invoice.invoice_number
        db.delete(invoice)
        log_activity(db, admin, "invoice.deleted", f"Deleted invoice {number}")
        db.commit()
    return RedirectResponse(url="/admin/invoices", status_code=303)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_billing_admin)):
    invoice = db.query(Invoice).options(joinedload(Invoice.items), joinedload(Invoice.gift_options)).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        return RedirectResponse(url="/admin/invoices", status_code=303)
    pdf_bytes = build_document_pdf(invoice, "invoice", get_all_settings(db))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{invoice.invoice_number}.pdf"'},
    )
