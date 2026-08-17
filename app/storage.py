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
MAX_DIMENSION = 2000  # px, longest side — large phone photos get downscaled
THUMBNAIL_DIMENSION = 400  # px, longest side — used everywhere but the product detail hero/gallery
AVATAR_DIMENSION = 500  # px, longest side — profile pictures are only ever shown small
PERSONALIZATION_DIMENSION = 1200  # px, longest side — may end up printed on the product, so kept larger than an avatar

# Every upload is re-encoded to WebP regardless of the input format (Pillow
# already supports it — no new dependency). At equal visual quality WebP
# runs meaningfully smaller than JPEG/PNG, which is the whole point of an
# "optimize once, serve many times" pipeline. Existing images already
# stored as .jpg/.png are untouched — this only affects new uploads.
OUTPUT_FORMAT = "WEBP"
OUTPUT_EXT = "webp"
OUTPUT_CONTENT_TYPE = "image/webp"
FULL_QUALITY = 88  # "visually-lossless" territory for WebP; within the requested 85-90% band
THUMBNAIL_QUALITY = 80  # thumbnails are viewed small, so a bit more headroom to save bytes is invisible


class UploadValidationError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=400, detail=detail)


@lru_cache
def _client():
    return create_client(settings.supabase_url, settings.supabase_service_key)


def _encode(image: Image.Image, quality: int) -> bytes:
    # WebP handles RGB and RGBA natively, so — unlike the old JPEG path —
    # there's no need to flatten transparency first.
    buffer = io.BytesIO()
    try:
        image.save(buffer, format=OUTPUT_FORMAT, quality=quality)
    except (OSError, KeyError):
        raise UploadValidationError("We couldn't save this image. Please try a different file.")
    return buffer.getvalue()


def _upload(filename: str, data: bytes, content_type: str) -> str:
    try:
        _client().storage.from_(BUCKET).upload(filename, data, {"content-type": content_type})
    except Exception:
        raise UploadValidationError("We couldn't upload this image. Please try again.")
    return _client().storage.from_(BUCKET).get_public_url(filename)


def _validate_and_load_image(upload: UploadFile) -> Image.Image:
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
        return image.convert("RGB") if image.mode not in ("RGB", "RGBA") else image
    except (UnidentifiedImageError, OSError):
        raise UploadValidationError("This file doesn't look like a valid image.")


def save_product_image(upload: UploadFile) -> tuple[str, str]:
    """Validates and uploads a product image (plus a smaller thumbnail, used
    everywhere except the product detail hero/gallery) to Supabase Storage.
    Returns (image_url, thumbnail_url)."""
    image = _validate_and_load_image(upload)

    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    stem = uuid.uuid4().hex
    image_url = _upload(
        f"{stem}.{OUTPUT_EXT}", _encode(image, FULL_QUALITY), OUTPUT_CONTENT_TYPE
    )

    thumb = image.copy()
    thumb.thumbnail((THUMBNAIL_DIMENSION, THUMBNAIL_DIMENSION))
    thumbnail_url = _upload(
        f"{stem}-thumb.{OUTPUT_EXT}", _encode(thumb, THUMBNAIL_QUALITY), OUTPUT_CONTENT_TYPE
    )

    return image_url, thumbnail_url


def save_avatar_image(upload: UploadFile) -> str:
    """Validates and uploads a customer profile picture to Supabase Storage
    (same bucket as product images, under an avatars/ prefix — one small
    object per customer, no separate thumbnail needed since it's never shown
    larger than a small circle anywhere in the UI). Returns the image URL."""
    image = _validate_and_load_image(upload)

    if max(image.size) > AVATAR_DIMENSION:
        image.thumbnail((AVATAR_DIMENSION, AVATAR_DIMENSION))

    filename = f"avatars/{uuid.uuid4().hex}.{OUTPUT_EXT}"
    return _upload(filename, _encode(image, THUMBNAIL_QUALITY), OUTPUT_CONTENT_TYPE)


def save_personalization_photo(upload: UploadFile) -> str:
    """Validates and uploads a customer-supplied personalization photo (e.g.
    a face photo for a custom mug/frame) to Supabase Storage. Same bucket,
    under a personalization/ prefix. Returns the image URL."""
    image = _validate_and_load_image(upload)

    if max(image.size) > PERSONALIZATION_DIMENSION:
        image.thumbnail((PERSONALIZATION_DIMENSION, PERSONALIZATION_DIMENSION))

    filename = f"personalization/{uuid.uuid4().hex}.{OUTPUT_EXT}"
    return _upload(filename, _encode(image, FULL_QUALITY), OUTPUT_CONTENT_TYPE)


def delete_product_image(image_url: str, thumbnail_url: str | None = None) -> None:
    filenames = []
    for url in (image_url, thumbnail_url):
        if url and f"/{BUCKET}/" in url:
            filenames.append(url.rsplit(f"/{BUCKET}/", 1)[-1])
    if not filenames:
        return
    try:
        _client().storage.from_(BUCKET).remove(filenames)
    except Exception:
        pass
