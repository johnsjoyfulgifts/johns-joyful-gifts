from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_admin_api
from app.database import get_db
from app.models import Admin, Order

router = APIRouter(prefix="/admin/api")


@router.get("/new-order-count")
def new_order_count(db: Session = Depends(get_db), admin: Admin = Depends(require_admin_api)):
    count = db.query(Order).filter(Order.viewed_by_admin.is_(False)).count()
    return {"count": count}
