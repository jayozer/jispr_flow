"""Pillow-based tray icon generation (optional extra: ``uv sync --extra tray``).

PIL is imported lazily inside :func:`draw_icon` so importing this module
never requires the ``tray`` extra to be installed — only calling the
function does.
"""

from __future__ import annotations

_COLORS = {
    "idle": "#8a8a8a",
    "recording": "#e5484d",
    "processing": "#f5a524",
    "error": "#7d0b0b",
}


def draw_icon(kind: str, size: int = 64):
    """Render a filled circle on a transparent background for ``kind``.

    Colors: idle=#8a8a8a, recording=#e5484d, processing=#f5a524,
    error=#7d0b0b. An unrecognized ``kind`` falls back to the idle color so
    a bad/unknown state never renders a broken or empty icon.

    ``error`` additionally draws a white "!" as a rectangle bar + dot
    (never a rendered font glyph — no external font files, so the result
    is deterministic across platforms and CI).
    """
    from PIL import Image, ImageDraw

    color = _COLORS.get(kind, _COLORS["idle"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = size * 0.08
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color)

    if kind == "error":
        white = (255, 255, 255, 255)
        cx = size / 2
        bar_width = max(2, round(size * 0.09))
        bar_top = size * 0.28
        bar_bottom = size * 0.58
        draw.rectangle(
            (cx - bar_width / 2, bar_top, cx + bar_width / 2, bar_bottom),
            fill=white,
        )
        dot_radius = bar_width * 0.75
        dot_top = bar_bottom + size * 0.06
        draw.ellipse(
            (cx - dot_radius, dot_top, cx + dot_radius, dot_top + dot_radius * 2),
            fill=white,
        )

    return image
