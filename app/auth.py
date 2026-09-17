from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Admin

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE_NAME = "jjg_admin_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8  # 8 hours

# Super Admin can always do everything (checked explicitly below, not just
# listed per-route) — Product Manager and Order/Enquiry Manager are scoped
# to their area. Every admin created before roles existed keeps working:
# see the "owner" -> "super_admin" data migration.
ROLE_SUPER_ADMIN = "super_admin"
ROLE_PRODUCT_MANAGER = "product_manager"
ROLE_ORDER_MANAGER = "order_enquiry_manager"
ADMIN_ROLES = (ROLE_SUPER_ADMIN, ROLE_PRODUCT_MANAGER, ROLE_ORDER_MANAGER)
ROLE_LABELS = {
    ROLE_SUPER_ADMIN: "Super Admin",
    ROLE_PRODUCT_MANAGER: "Product Manager",
    ROLE_ORDER_MANAGER: "Order & Enquiry Manager",
}

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="admin-session")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def create_session_token(admin_id: int) -> str:
    return _serializer.dumps({"admin_id": admin_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("admin_id")


def set_session_cookie(response, admin_id: int) -> None:
    token = create_session_token(admin_id)
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


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> Admin | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    admin_id = read_session_token(token)
    if admin_id is None:
        return None
    return db.get(Admin, admin_id)


def require_admin(request: Request, db: Session = Depends(get_db)) -> Admin:
    admin = get_current_admin(request, db)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return admin


def require_admin_api(request: Request, db: Session = Depends(get_db)) -> Admin:
    """Same check for JSON admin_api endpoints: expired/missing session -> 401, not a redirect."""
    admin = get_current_admin(request, db)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Please log in again.")
    return admin


def require_role(*allowed_roles: str):
    """Dependency factory: any of `allowed_roles` may proceed, and Super
    Admin can always proceed regardless of what's listed — it's the one
    role with no ceiling. Insufficient permission still requires being
    logged in first (this wraps require_admin's own check via the same
    session lookup), so it never leaks *whether* a page exists to a logged
    -out visitor — they're sent to /admin/login exactly as before."""

    def dependency(request: Request, db: Session = Depends(get_db)) -> Admin:
        admin = require_admin(request, db)
        if admin.role != ROLE_SUPER_ADMIN and admin.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin?denied=1"})
        return admin

    return dependency
