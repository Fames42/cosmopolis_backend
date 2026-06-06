"""Shared image handling for WhatsApp and manual ticket uploads."""

import base64
import logging

import httpx

logger = logging.getLogger("uvicorn.error")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ImageProcessingError(Exception):
    """Raised when an image cannot be downloaded or converted."""


class ImageValidationError(ImageProcessingError):
    """Raised when uploaded image content violates validation rules."""


def normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def bytes_to_data_uri(content: bytes, content_type: str | None) -> str:
    """Validate image bytes and return a base64 data URI."""
    media_type = normalize_content_type(content_type)
    if media_type not in ALLOWED_IMAGE_TYPES:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_TYPES))
        raise ImageValidationError(f"Unsupported image type. Allowed: {allowed}")
    if not content:
        raise ImageValidationError("Image file is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageValidationError("Image file is too large. Maximum size is 5 MB")

    encoded = base64.b64encode(content).decode("utf-8")
    return f"data:{media_type};base64,{encoded}"


def download_url_to_data_uri(download_url: str) -> str:
    """Download an image URL and return a validated base64 data URI."""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(download_url)
            resp.raise_for_status()
        return bytes_to_data_uri(resp.content, resp.headers.get("content-type", "image/jpeg"))
    except ImageProcessingError:
        raise
    except Exception as exc:
        logger.exception("Failed to download image from %s", download_url)
        raise ImageProcessingError("Failed to download image") from exc
