"""
Command: screenshot
Capture the primary screen and return as base64-encoded PNG.
Uses mss (fastest) with Pillow fallback.
"""
from __future__ import annotations
import base64
import io
import logging

from client.commands.registry import register

log = logging.getLogger(__name__)


@register("screenshot")
async def screenshot(payload: dict) -> dict:
    """
    payload:
        quality (int): JPEG quality 1-95 if format=jpeg (default 85)
        format  (str): "png" or "jpeg" (default "png")
    """
    fmt = payload.get("format", "png").lower()
    quality = int(payload.get("quality", 85))

    try:
        import mss
        import mss.tools
        from PIL import Image

        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
    except ImportError:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
        except Exception as exc:
            return {"error": f"screenshot failed: {exc}"}
    except Exception as exc:
        log.exception("mss screenshot error")
        return {"error": str(exc)}

    buf = io.BytesIO()
    if fmt == "jpeg":
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
    else:
        img.save(buf, format="PNG")

    encoded = base64.b64encode(buf.getvalue()).decode()
    return {"image": encoded, "format": fmt, "size": len(buf.getvalue())}
