"""
Command: screenshot
Capture the primary screen and return as base64-encoded JPEG/PNG.
Auto-scales down large screenshots to stay well under the 20MB WS limit.
"""
from __future__ import annotations
import base64
import io
import logging

from client.commands.registry import register

log = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024   # target max size: 8 MB after base64
MAX_DIMENSION = 1920           # resize if wider or taller than this


@register("screenshot")
async def screenshot(payload: dict) -> dict:
    """
    payload:
        quality (int): JPEG quality 1-95 (default 75)
        format  (str): "jpeg" or "png" (default "jpeg" — smaller)
        max_dim (int): max width/height before downscaling (default 1920)
    """
    fmt = payload.get("format", "jpeg").lower()
    quality = int(payload.get("quality", 75))
    max_dim = int(payload.get("max_dim", MAX_DIMENSION))

    img = _grab_screen()
    if img is None:
        return {"error": "screenshot failed: no suitable library available"}

    # Downscale if too large
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
        log.debug("Screenshot resized: %dx%d → %dx%d", w, h, img.width, img.height)

    # Encode — try requested format, fall back to JPEG if PNG is too big
    data = _encode(img, fmt, quality)

    if len(data) > MAX_BYTES and fmt == "png":
        log.debug("PNG too large (%d bytes), re-encoding as JPEG", len(data))
        fmt = "jpeg"
        data = _encode(img, "jpeg", quality)

    # If still too big, progressively reduce quality
    q = quality
    while len(data) > MAX_BYTES and q > 30:
        q -= 15
        data = _encode(img, "jpeg", q)
        log.debug("Re-encoded with quality=%d → %d bytes", q, len(data))

    encoded = base64.b64encode(data).decode()
    return {
        "image": encoded,
        "format": fmt,
        "size": len(data),
        "width": img.width,
        "height": img.height,
    }


def _grab_screen():
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            sct_img = sct.grab(monitor)
            return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
    except ImportError:
        pass
    except Exception as exc:
        log.warning("mss grab failed: %s", exc)

    try:
        from PIL import ImageGrab
        return ImageGrab.grab()
    except Exception as exc:
        log.warning("ImageGrab failed: %s", exc)

    return None


def _encode(img, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
