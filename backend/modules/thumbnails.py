"""
thumbnails.py – Cached thumbnail generation for document photos.

Thumbnails are stored in THUMBNAILS_DIR keyed by sha256, so a rotated photo
(which gets a new sha256) automatically uses a new cache entry. Old entries
are orphaned and harmless until cleaned up.
"""

import logging
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def thumbnail_path(sha256: str) -> Path:
    """Return the on-disk path for a thumbnail keyed by content hash."""
    from config import THUMBNAILS_DIR
    return THUMBNAILS_DIR / f"{sha256}.jpg"


def ensure_thumbnail(source_path: Path, sha256: str) -> Path | None:
    """
    Return a path to the cached thumbnail for `source_path`, generating it
    on demand if missing. Returns None if generation fails.
    """
    out_path = thumbnail_path(sha256)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    if not source_path.exists():
        logger.warning("Cannot generate thumbnail; source missing: %s", source_path)
        return None

    try:
        from config import THUMBNAIL_MAX_DIM, THUMBNAIL_QUALITY
        with Image.open(source_path) as img:
            # Honour EXIF orientation so portrait iPhone photos render upright.
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((THUMBNAIL_MAX_DIM, THUMBNAIL_MAX_DIM), Image.LANCZOS)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
        return out_path
    except Exception:
        logger.exception("Thumbnail generation failed for %s", source_path)
        return None


def delete_thumbnail(sha256: str) -> None:
    """Remove the cached thumbnail for a sha256, if present."""
    try:
        thumbnail_path(sha256).unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to delete thumbnail for %s", sha256)
