import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import SessionLocal
from app.templating import render

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jjg")

settings = get_settings()

app = FastAPI(title="John's Joyful Gifts")

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

upload_dir = os.path.abspath(settings.upload_dir)
os.makedirs(upload_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")


def _safe_db_render(request: Request, template: str, context: dict, status_code: int):
    """Render an error page without ever raising a second error onto the user."""
    try:
        db = SessionLocal()
        try:
            return render(request, template, context, db, status_code=status_code)
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to render error page %s", template)
        return PlainTextResponse("Something went wrong. Please try again.", status_code=status_code)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Preserve redirects (e.g. admin-auth 303) and JSON API error responses untouched.
    if exc.headers and "location" in {k.lower() for k in exc.headers.keys()}:
        return PlainTextResponse("", status_code=exc.status_code, headers=exc.headers)
    if request.url.path.startswith("/admin/api") or request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    if exc.status_code == 404:
        return _safe_db_render(request, "errors/404.html", {}, 404)
    if exc.status_code >= 500:
        logger.error("Server error on %s: %s", request.url.path, exc.detail)
        return _safe_db_render(request, "errors/500.html", {}, exc.status_code)

    # Other 4xx (400/401/403/etc.) — friendly generic message, real detail in logs.
    return _safe_db_render(request, "errors/404.html", {}, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/admin/api") or request.url.path.startswith("/api"):
        first_error = exc.errors()[0] if exc.errors() else {}
        return JSONResponse(
            {"detail": first_error.get("msg", "Please check the information you entered.")},
            status_code=422,
        )
    return _safe_db_render(request, "errors/404.html", {}, 422)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    if request.url.path.startswith("/admin/api") or request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)
    return _safe_db_render(request, "errors/500.html", {}, 500)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /admin\nSitemap: /sitemap.xml\n")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    path = os.path.join(BASE_DIR, "static", "icons", "favicon.svg")
    return FileResponse(path, media_type="image/svg+xml")


from app.routers import (  # noqa: E402
    admin_api,
    admin_pages,
    cart,
    checkout,
    customer_pages,
    tracking,
)

app.include_router(customer_pages.router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(tracking.router)
app.include_router(admin_pages.router)
app.include_router(admin_api.router)
