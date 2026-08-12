"""
Local-filesystem image storage, kept behind a small abstraction so a future
move to S3/Cloudinary only requires changing this module, not the routes
that call it.
"""

import io
import os
import uuid

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.config import get_settings

settings = get_settings()

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_DIMENSION = 2000  # px, longest side — large phone photos get downscaled


class UploadValidationError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=400, detail=detail)


def _ensure_upload_dir() -> str:
    path = os.path.abspath(settings.upload_dir)
    os.makedirs(path, exist_ok=True)
    return path


def save_product_image(upload: UploadFile) -> str:
    """Validates and saves an uploaded product image. Returns the public URL path."""
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
    upload_dir = _ensure_upload_dir()
    dest_path = os.path.join(upload_dir, filename)

    save_format = "JPEG" if ext in ("jpg", "jpeg") else ext.upper()
    if save_format == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")
    save_kwargs = {"quality": 85} if save_format == "JPEG" else {}
    try:
        image.save(dest_path, format=save_format, **save_kwargs)
    except (OSError, KeyError):
        raise UploadValidationError("We couldn't save this image. Please try a different file.")

    return f"/uploads/{filename}"


def delete_product_image(image_url: str) -> None:
    if not image_url or not image_url.startswith("/uploads/"):
        return
    filename = os.path.basename(image_url)
    upload_dir = _ensure_upload_dir()
    path = os.path.join(upload_dir, filename)
    if os.path.commonpath([upload_dir, os.path.abspath(path)]) == upload_dir:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
