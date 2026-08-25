"""Saving uploaded files to the local media dir (payment / address proofs)."""

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings


def save_upload(file: UploadFile, subdir: str) -> str:
    """Persist an UploadFile under MEDIA_ROOT/<subdir>/ and return the stored
    relative path (e.g. "payment_proofs/<uuid>.png")."""
    dest_dir = settings.MEDIA_ROOT / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix
    name = f"{uuid.uuid4().hex}{suffix}"
    rel = f"{subdir}/{name}"
    with open(settings.MEDIA_ROOT / rel, "wb") as out:
        out.write(file.file.read())
    return rel


# Skill evidence: an image or a PDF, capped so a stray upload can't fill the
# media volume. Enforced by reading the stream in chunks — trusting the
# Content-Length header would let a client understate the real size.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    # "image/gif",
    "image/webp",
}
ATTACHMENT_CONTENT_TYPES = IMAGE_CONTENT_TYPES | {"application/pdf"}


class UploadTooLarge(ValueError):
    pass


class UnsupportedUpload(ValueError):
    pass


def save_attachment(
    file: UploadFile,
    subdir: str,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
    content_types: set[str] = ATTACHMENT_CONTENT_TYPES,
) -> str:
    """Like :func:`save_upload`, but validates type and size first.

    Raises :class:`UnsupportedUpload` or :class:`UploadTooLarge`. A file that
    trips the cap mid-stream is removed, so a rejected upload leaves nothing on
    disk.
    """
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in content_types:
        raise UnsupportedUpload(
            "Attach an image (PNG, JPEG, WebP)"
            if "application/pdf" not in content_types
            else "Attach an image (PNG, JPEG, WebP) or a PDF"
        )

    dest_dir = settings.MEDIA_ROOT / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix[:10]
    rel = f"{subdir}/{uuid.uuid4().hex}{suffix}"
    path = settings.MEDIA_ROOT / rel

    written = 0
    try:
        with open(path, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge("Attachments are limited to 10MB")
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return rel


def delete_media(rel_path: str | None) -> None:
    """Best-effort removal of a stored media file."""
    if not rel_path:
        return
    (settings.MEDIA_ROOT / rel_path).unlink(missing_ok=True)


def media_url(rel_path: str | None) -> str | None:
    """Root-relative URL for a stored media path, e.g. "/media/x.png".

    Deliberately *not* absolute. Callers used to join `request.base_url`, but
    the browser reaches this API through the frontend's /api rewrite, so that
    host is the internal container (http://backend:8000) and any URL built from
    it 404s in a tab. The client prefixes its own API base instead, which is
    correct both behind the dev rewrite and against an absolute API origin.
    """
    if not rel_path:
        return None
    return f"{settings.MEDIA_URL}{rel_path}"


def media_absolute_url(request, rel_path: str | None) -> str | None:
    """Absolute URL for a stored media path.

    Uses the request's base URL so the returned URL is resolvable from a browser
    tab or PDF renderer, even when the CV HTML is embedded in a frontend iframe.
    Root-relative URLs would resolve against the iframe origin, so an absolute
    URL is required here.
    """
    if not rel_path:
        return None

    root = settings.ROOT_PATH.strip("/")
    media = settings.MEDIA_URL.strip("/")
    rel = rel_path.lstrip("/")

    path = f"/{media}/{rel}" if media else f"/{rel}"
    if root:
        path = f"/{root}{path}"

    if request is None:
        return path

    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"
