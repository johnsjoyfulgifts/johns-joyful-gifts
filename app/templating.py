import json
import os
from datetime import datetime, timezone
from markupsafe import Markup

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.i18n import t
from app.models import Category
from app.settings_service import get_all_settings

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _tojson_filter(value) -> Markup:
    """Safe to embed inside a <script> block, e.g. var x = {{ value|tojson }};"""
    return Markup(json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


templates.env.filters["tojson"] = _tojson_filter
# {{ t('add_to_cart', current_language) }} — see app/i18n.py. Language is
# passed explicitly (not read from a global) since Jinja globals aren't
# request-scoped and this needs to vary per store setting.
templates.env.globals["t"] = t


def _common_context(request, db: Session) -> dict:
    from app.cart_service import cart_totals, get_cart_lines
    from app.customer_auth import get_current_customer
    from app.utils.whatsapp import whatsapp_chat_link

    store = get_all_settings(db)
    nav_categories = (
        db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()
    )
    _, cart_count = cart_totals(get_cart_lines(request, db))
    return {
        "request": request,
        "store": store,
        "nav_categories": nav_categories,
        "cart_count": cart_count,
        "whatsapp_chat_link": whatsapp_chat_link(store.get("whatsapp_number", "")),
        "current_year": datetime.now(timezone.utc).year,
        "current_customer": get_current_customer(request, db),
        "current_language": store.get("site_language", "en"),
    }


def render(request, template_name: str, context: dict, db: Session, status_code: int = 200):
    full_context = {**_common_context(request, db), **context}
    return templates.TemplateResponse(template_name, full_context, status_code=status_code)


def render_admin(request, template_name: str, context: dict, db: Session, status_code: int = 200):
    from app.auth import get_current_admin

    store = get_all_settings(db)
    full_context = {
        "request": request,
        "store": store,
        "current_year": datetime.now(timezone.utc).year,
        "current_admin": get_current_admin(request, db),
        **context,
    }
    return templates.TemplateResponse(template_name, full_context, status_code=status_code)
