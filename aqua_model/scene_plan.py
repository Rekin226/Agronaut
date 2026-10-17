"""Deterministic top-down LAYOUT PLAN of a 3D scene — one scene, one still PNG.

`scene3d.to_scene` states where every component stands. The bundled three.js viewer draws
that as an interactive page a browser opens; this module draws the same arrangement as a
flat, offline-ready still, for the channels that cannot carry the HTML at all (WhatsApp has
no browser and no file picker). The two views share their only source of truth: the scene's
own `(x, y)` positions and footprints. Nothing here re-derives a dimension.

It deliberately does NOT go through `schematic.to_png`. That renders the process schematic —
boxes and arrows describing the flowsheet (rearing tank -> biofilter -> beds) — which is a
different artefact about a different question. The layout plan answers "where does everything
stand on the floor"; the schematic answers "what happens to the water". Sending the schematic
where the 3D layout was asked for would be the wrong picture (#167).

Pure and deterministic like the rest of `aqua_model/`: the same scene always rasterises to
the byte-identical PNG (no timestamps, no randomness), so it is snapshot-stable and testable.
"""

from __future__ import annotations

import math

# Rendering uses the schematic's text plumbing (fonts and centred labels) rather than a
# second copy of it: the DejaVu-first candidate list there exists so a Linux host draws a
# legible plan instead of one flat size, and duplicating it would let the two drawings drift.
from .schematic import _center_text, _font

PAD = 40                 # px of clear space around the plan
HEADER = 84              # px band above the plan for the title and subtitle
MAX_PLOT = 820           # px on the plan's longest side
LEGEND_ROW_H = 20
LEGEND_SWATCH = 12

_FLOOR = "#f7fafc"
_WALL = "#7d8b98"
_OUTLINE = "#33475b"
_PIPE = "#2b6cb0"
_TEXT = "#0b1b2b"
_MUTED = "#44515e"

# Fill by component role. A plan reads by ZONE — fish, treatment, beds, sump — and a colour is
# a drawing convention, not a claim about a pond, so an unfamiliar role falls to a neutral
# grey rather than being dropped: a component with no colour must still be located.
ROLE_FILL = {
    "fish_tank": "#bfe3f7",
    "clarifier": "#d7eeda",
    "settling": "#d7eeda",
    "biofilter": "#cfe6d6",
    "degasser": "#cfe6d6",
    "mineraliser": "#e8dcf3",
    "sump": "#fde9cf",
    "dwc_bed": "#cdeccd",
    "media_bed": "#cdeccd",
    "nft_channel": "#cdeccd",
    "vertical_tower": "#cdeccd",
}
_FILL_DEFAULT = "#e3e8ee"


def _role_label(role: str) -> str:
    return str(role or "component").replace("_", " ").strip().title()


def _fit_scale(width_m: float, length_m: float) -> float:
    """Pixels per metre so the whole greenhouse fits MAX_PLOT, longest side first."""
    w = max(float(width_m), 1e-6)
    ln = max(float(length_m), 1e-6)
    return min(MAX_PLOT / w, MAX_PLOT / ln)


def _shrink(draw, text: str, font_fn, max_px: float, size: int):
    """The largest requested size at which `text` fits `max_px`, else the text truncated."""
    for s in (size, size - 2, size - 4):
        f = font_fn(s)
        try:
            w = draw.textlength(text, font=f)
        except AttributeError:  # very old Pillow
            w = f.getsize(text)[0]
        if w <= max_px:
            return text, f
    f = font_fn(max(8, size - 6))
    cut = text
    while cut and draw.textlength(cut + "…", font=f) > max_px:
        cut = cut[:-1]
    return (cut + "…" if cut else ""), f


def to_png(scene: dict) -> bytes:
    """Rasterize a `scene3d.to_scene` dict to PNG bytes. Deterministic.

    The scene's greenhouse is the canvas: x runs across the page, y runs down it, both in
    metres from the corner, exactly as the scene states them. Boxes draw their footprint
    (w x l), cylinders their diameter, and the plumbing its `(x, y)` waypoints.
    """
    import io

    from PIL import Image, ImageDraw

    gh = scene.get("greenhouse") or {}
    width_m = float(gh.get("width") or 0.0)
    length_m = float(gh.get("length") or 0.0)
    scale = _fit_scale(width_m, length_m)

    objects = list(scene.get("objects") or [])
    pipes = list(scene.get("pipes") or [])

    # Legend: one row per distinct role, in the order the components appear on the floor.
    roles: list[str] = []
    for o in objects:
        r = str(o.get("role") or "")
        if r and r not in roles:
            roles.append(r)

    plot_w = int(round(width_m * scale))
    plot_h = int(round(length_m * scale))
    cols = max(1, min(len(roles) or 1, max(1, plot_w // 190)))
    rows = max(1, math.ceil(len(roles) / cols)) if roles else 0
    legend_h = rows * LEGEND_ROW_H
    note_h = 22

    img_w = plot_w + 2 * PAD
    img_h = HEADER + plot_h + PAD + legend_h + note_h

    ox, oy = PAD, HEADER          # plan origin (greenhouse corner 0,0), in px

    def px(x: float) -> float:
        return ox + float(x) * scale

    def py(y: float) -> float:
        return oy + float(y) * scale

    img = Image.new("RGB", (max(img_w, 1), max(img_h, 1)), _FLOOR)
    d = ImageDraw.Draw(img)

    f_title = _font(20, bold=True)
    f_sub = _font(12)
    f_note = _font(12, bold=True)
    f_leg = _font(11)

    # Header: the scene's own name and subtitle, so the still says which design it is.
    _center_text(d, img_w / 2, 16, str(scene.get("name") or "Aquaponic system"),
                 f_title, _TEXT)
    subtitle, f_sub = _shrink(d, str(scene.get("subtitle") or ""), lambda s: _font(s),
                              img_w - 2 * PAD, 12)
    if subtitle:
        _center_text(d, img_w / 2, 52, subtitle, f_sub, _MUTED)

    # The greenhouse floor.
    d.rectangle((px(0), py(0), px(width_m), py(length_m)),
                fill="#eef4f8", outline=_WALL, width=2)

    # Plumbing runs, drawn under the components so a tank is never hidden by its own pipe.
    for run in pipes:
        pts = [(px(q[0]), py(q[1])) for q in (run.get("path") or []) if len(q) >= 2]
        if len(pts) >= 2:
            d.line(pts, fill=_PIPE, width=2)
        elif pts:
            d.ellipse((pts[0][0] - 2, pts[0][1] - 2, pts[0][0] + 2, pts[0][1] + 2),
                      fill=_PIPE)

    # Component footprints at their real positions.
    for o in objects:
        fill = ROLE_FILL.get(str(o.get("role") or ""), _FILL_DEFAULT)
        x, y = float(o.get("x") or 0.0), float(o.get("y") or 0.0)
        if o.get("kind") == "cyl":
            r = float(o.get("d") or 0.0) / 2.0
            d.ellipse((px(x - r), py(y - r), px(x + r), py(y + r)),
                      fill=fill, outline=_OUTLINE, width=2)
        else:
            hw = float(o.get("w") or 0.0) / 2.0
            hl = float(o.get("l") or 0.0) / 2.0
            d.rectangle((px(x - hw), py(y - hl), px(x + hw), py(y + hl)),
                        fill=fill, outline=_OUTLINE, width=2)

    # Legend along the bottom.
    if roles:
        lx, ly = PAD, HEADER + plot_h + PAD
        col_w = max(1, plot_w // cols)
        for i, role in enumerate(roles):
            r, c = divmod(i, cols)
            sx = lx + c * col_w
            sy = ly + r * LEGEND_ROW_H
            d.rectangle((sx, sy + 2, sx + LEGEND_SWATCH, sy + 2 + LEGEND_SWATCH),
                        fill=ROLE_FILL.get(role, _FILL_DEFAULT), outline=_OUTLINE)
            d.text((sx + LEGEND_SWATCH + 6, sy), _role_label(role), font=f_leg, fill=_MUTED)

    # A plan is a proposal, not a survey — say so where the viewer can see it, exactly as the
    # scene's own geometry_note does for the twin.
    note = "top-down layout plan — positions are a proposed arrangement, not a site survey"
    d.text((PAD, HEADER + plot_h + PAD + legend_h + 4), note, font=f_note, fill=_MUTED)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
