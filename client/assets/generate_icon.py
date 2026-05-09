"""
Generate a Windows-system-style shield icon (similar to Windows Security/UAC).
Produces client/assets/icon.ico with all required sizes.

Run once before building:
    python client/assets/generate_icon.py
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw_shield(size: int) -> Image.Image:
    """Draw a Windows-Security-style blue shield icon at the given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    p = size / 256  # scale factor relative to 256px base

    # ── Shield background (dark navy, like Windows UAC) ──────────────────
    def shield_poly(scale=1.0, offset_y=0):
        cx = size / 2
        w = size * 0.82 * scale
        h = size * 0.90 * scale
        top = size * 0.05 + offset_y
        left = cx - w / 2
        right = cx + w / 2
        mid_y = top + h * 0.45
        bottom = top + h

        # Shield shape: trapezoid top → pointed bottom
        return [
            (left,  top),
            (right, top),
            (right, mid_y),
            (cx,    bottom),
            (left,  mid_y),
        ]

    # Shadow / depth layer
    shadow = shield_poly(scale=0.98, offset_y=size * 0.025)
    d.polygon(shadow, fill=(10, 30, 80, 180))

    # Main shield body
    body = shield_poly()
    d.polygon(body, fill=(25, 80, 200, 255))

    # Lighter top half gradient illusion
    top_half = [
        body[0],
        body[1],
        (body[1][0], body[0][1] + (body[2][1] - body[0][1]) * 0.5),
        (body[4][0], body[0][1] + (body[4][1] - body[0][1]) * 0.5),
    ]
    d.polygon(top_half, fill=(50, 110, 230, 120))

    # Thin highlight on left edge
    if size >= 32:
        edge_w = max(1, int(size * 0.03))
        left_edge = [
            (body[0][0] + edge_w, body[0][1] + edge_w),
            (body[0][0] + edge_w * 2, body[0][1] + edge_w),
            (body[4][0] + edge_w * 2, body[4][1] - edge_w),
            (body[4][0] + edge_w, body[4][1]),
        ]
        d.polygon(left_edge, fill=(120, 170, 255, 90))

    # ── Checkmark / lock symbol ───────────────────────────────────────────
    if size >= 24:
        cx = size / 2
        cy = size * 0.50

        if size >= 48:
            # Draw a white checkmark
            stroke = max(2, int(size * 0.07))
            x1, y1 = cx - size * 0.22, cy + size * 0.00
            x2, y2 = cx - size * 0.04, cy + size * 0.18
            x3, y3 = cx + size * 0.24, cy - size * 0.18
            d.line([x1, y1, x2, y2], fill=(255, 255, 255, 255), width=stroke)
            d.line([x2, y2, x3, y3], fill=(255, 255, 255, 255), width=stroke)
        else:
            # Small sizes: just a white dot/square
            r = max(1, int(size * 0.15))
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 240))

    # ── Border ────────────────────────────────────────────────────────────
    if size >= 32:
        body2 = shield_poly(scale=0.995)
        d.polygon(body2, outline=(80, 130, 255, 180), fill=None)

    return img


def main() -> None:
    images = [draw_shield(s) for s in SIZES]
    images[0].save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:],
    )
    print(f"Icon saved → {OUT}")
    print(f"Sizes: {SIZES}")


if __name__ == "__main__":
    main()
