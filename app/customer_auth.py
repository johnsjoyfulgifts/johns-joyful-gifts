from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password  # reuse the same bcrypt hashing as admin auth
from app.config import get_settings
from app.database import get_db
from app.models import Customer

settings = get_settings()

SESSION_COOKIE_NAME = "jjg_customer_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days — customers, unlike admins, shouldn't be logged out often

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="customer-session")

__all__ = ["hash_password", "verify_password"]  # re-exported for convenience


def create_session_token(customer_id: int) -> str:
    return _serializer.dumps({"customer_id": customer_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("customer_id")


def set_session_cookie(response, customer_id: int) -> None:
    token = create_session_token(customer_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def get_current_customer(request: Request, db: Session = Depends(get_db)) -> Customer | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    customer_id = read_session_token(token)
    if customer_id is None:
        return None
    return db.get(Customer, customer_id)


def require_customer(request: Request, db: Session = Depends(get_db)) -> Customer:
    """For page routes: redirects to /login (preserving the page they wanted via ?next=)."""
    customer = get_current_customer(request, db)
    if customer is None:
        next_url = request.url.path
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={next_url}"},
        )
    return customer


def require_customer_api(request: Request, db: Session = Depends(get_db)) -> Customer:
    """Same check for JSON endpoints: missing/expired session -> 401, not a redirect."""
    customer = get_current_customer(request, db)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in to continue.")
    return customer
