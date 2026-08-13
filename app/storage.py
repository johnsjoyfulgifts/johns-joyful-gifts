"""
Supabase Storage-based image storage, kept behind a small abstraction so the
routes that call it never need to know where images actually live.
"""

import io
import uuid
from functools import lru_cache

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from supabase import create_client

from app.config import get_settings

settings = get_settings()

BUCKET = "product-images"

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
CONTENT_TYPE_FOR_EXT = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MAX_DIMENSION = 2000  # px, longest side — large phone photos get downscaled


class UploadValidationError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=400, detail=detail)


@lru_cache
def _client():
    return create_client(settings.supabase_url, settings.supabase_service_key)


def save_product_image(upload: UploadFile) -> str:
    """Validates and uploads a product image to Supabase Storage. Returns its public URL."""
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadValidationError("Please upload a JPG, PNG, or WEBP image.")

    raw = upload.file.read()
    if len(raw) == 0:
        raise UploadValidationError("The uploaded file is empty.")
    if len(raw) > settings.max_upload_size_bytes:
        max_mb = settings.max_upload_size_bytes / (1024 * 1024)
        raise UploadValidationError(f"Image is too large. Maximum size is {max_mb:.0f} MB.")

    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        # Re-open after verify() (which leaves the file unusable for further ops).
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGB") if image.mode not in ("RGB", "RGBA") else image
    except (UnidentifiedImageError, OSError):
        raise UploadValidationError("This file doesn't look like a valid image.")

    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    ext = ALLOWED_CONTENT_TYPES[upload.content_type]
    filename = f"{uuid.uuid4().hex}.{ext}"

    save_format = "JPEG" if ext == "jpg" else ext.upper()
    if save_format == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")
    save_kwargs = {"quality": 85} if save_format == "JPEG" else {}

    buffer = io.BytesIO()
    try:
        image.save(buffer, format=save_format, **save_kwargs)
    except (OSError, KeyError):
        raise UploadValidationError("We couldn't save this image. Please try a different file.")

    try:
        _client().storage.from_(BUCKET).upload(
            filename,
            buffer.getvalue(),
            {"content-type": CONTENT_TYPE_FOR_EXT[ext]},
        )
    except Exception:
        raise UploadValidationError("We couldn't upload this image. Please try again.")

    return _client().storage.from_(BUCKET).get_public_url(filename)


def delete_product_image(image_url: str) -> None:
    if not image_url or f"/{BUCKET}/" not in image_url:
        return
    filename = image_url.rsplit(f"/{BUCKET}/", 1)[-1]
    try:
        _client().storage.from_(BUCKET).remove([filename])
    except Exception:
        pass
