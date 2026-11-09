"""Image optimization utilities for fanart_viewer.

Downscales oversized images and compresses them to modern WebP format
to keep SQLite database size and memory usage lean.
"""

import io
import logging
import os
from typing import Optional, Tuple
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

DEFAULT_MAX_DIM = int(os.environ.get("IMAGE_OPTIMIZE_MAX_DIM", "2048"))
DEFAULT_QUALITY = int(os.environ.get("IMAGE_OPTIMIZE_QUALITY", "82"))
OPTIMIZE_ENABLED = os.environ.get("IMAGE_OPTIMIZE_ENABLED", "true").lower() not in ("0", "false", "no", "off")


def optimize_image_data(
    data: bytes,
    content_type: Optional[str] = None,
    max_dim: int = DEFAULT_MAX_DIM,
    quality: int = DEFAULT_QUALITY,
) -> Tuple[bytes, str]:
    """Optimize raw image bytes.

    - Returns (optimized_bytes, new_content_type).
    - If already WebP and dimensions <= max_dim, returns original data.
    - If animated, SVG, video, or non-image, returns original data unchanged.
    - If compression fails for any reason, falls back safely to original data.
    - If optimized data is not smaller than original data, keeps original data.
    """
    if not data or not OPTIMIZE_ENABLED:
        return data, content_type or "application/octet-stream"

    ctype = (content_type or "").lower().strip()
    if ctype.startswith("video/") or ctype == "image/svg+xml":
        return data, content_type or "application/octet-stream"

    try:
        with Image.open(io.BytesIO(data)) as im:
            # Preserve animated GIFs / WebP animations
            if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                return data, content_type or "image/gif"

            # Auto-orient based on EXIF tags
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass

            orig_w, orig_h = im.size

            # If it's already WebP and fits within max_dim, avoid re-encoding loss
            if im.format == "WEBP" and orig_w <= max_dim and orig_h <= max_dim:
                return data, "image/webp"

            # Downscale if exceeding max dimension
            if orig_w > max_dim or orig_h > max_dim:
                im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            # Handle color modes
            mode = im.mode
            if mode == "P":
                # Palette image: convert to RGBA if transparency exists, else RGB
                if "transparency" in im.info:
                    im = im.convert("RGBA")
                else:
                    im = im.convert("RGB")
            elif mode == "CMYK":
                im = im.convert("RGB")
            elif mode not in ("RGB", "RGBA", "L", "LA"):
                im = im.convert("RGBA" if "A" in mode else "RGB")

            out_buf = io.BytesIO()
            im.save(out_buf, format="WEBP", quality=quality, method=4)
            compressed_bytes = out_buf.getvalue()

            # Only use compressed version if it actually saves bytes
            if len(compressed_bytes) < len(data):
                return compressed_bytes, "image/webp"

            return data, content_type or "image/jpeg"

    except (UnidentifiedImageError, OSError, ValueError):
        # Not a valid decodable image or truncated — return original safely
        return data, content_type or "application/octet-stream"
    except Exception as e:
        logger.debug("Image optimization skipped due to error: %s", e)
        return data, content_type or "application/octet-stream"
