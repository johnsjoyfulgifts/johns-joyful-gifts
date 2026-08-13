from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.customer_auth import (
    clear_session_cookie,
    get_current_customer,
    hash_password,
    require_customer,
    set_session_cookie,
    verify_password,
)
from app.database import get_db
from app.models import Customer, Order
from app.rate_limit import is_rate_limited
from app.schemas import MOBILE_RE
from app.templating import render

router = APIRouter()


def _safe_next(next_url: str | None) -> str:
    """Only ever redirect within this site — an attacker-supplied ?next=
    pointing off-site would otherwise be an open-redirect phishing vector."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/account"


@router.get("/register")
def register_page(request: Request, next: str = "", db: Session = Depends(get_db)):
    if get_current_customer(request, db) is not None:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return render(request, "customer/register.html", {"next": next}, db)


@router.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    mobile: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    email: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    name = name.strip()
    mobile = mobile.strip()
    error = None

    if not name:
        error = "Please enter your name."
    elif not MOBILE_RE.match(mobile):
        error = "Please enter a valid mobile number."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != confirm_password:
        error = "Passwords didn't match."
    elif db.query(Customer).filter(Customer.mobile == mobile).first() is not None:
        error = "An account with this mobile number already exists. Please log in instead."

    if error:
        return render(
            request,
            "customer/register.html",
            {"error": error, "name": name, "mobile": mobile, "email": email, "next": next},
            db,
            status_code=400,
        )

    customer = Customer(name=name, mobile=mobile, password_hash=hash_password(password), email=email.strip() or None)
    db.add(customer)
    db.commit()

    response = RedirectResponse(url=_safe_next(next), status_code=303)
    set_session_cookie(response, customer.id)
    return response


@router.get("/login")
def login_page(request: Request, next: str = "", db: Session = Depends(get_db)):
    if get_current_customer(request, db) is not None:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return render(request, "customer/login.html", {"next": next}, db)


@router.post("/login")
def login_submit(
    request: Request,
    mobile: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(f"customer-login:{client_ip}"):
        return render(
            request,
            "customer/login.html",
            {"error": "Too many login attempts. Please wait a few minutes and try again.", "mobile": mobile, "next": next},
            db,
            status_code=429,
        )

    customer = db.query(Customer).filter(Customer.mobile == mobile.strip()).first()
    if customer is None or not verify_password(password, customer.password_hash):
        return render(
            request,
            "customer/login.html",
            {"error": "Incorrect mobile number or password.", "mobile": mobile, "next": next},
            db,
            status_code=401,
        )

    response = RedirectResponse(url=_safe_next(next), status_code=303)
    set_session_cookie(response, customer.id)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/account")
def account_page(request: Request, db: Session = Depends(get_db), customer: Customer = Depends(require_customer)):
    orders = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return render(request, "customer/account.html", {"customer": customer, "orders": orders}, db)
