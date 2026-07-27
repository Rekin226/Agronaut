"""Deterministic system schematics — one scene, rendered to SVG or PNG.

Pure code, no ML and no network dependency: the same DesignOutput/HydroponicOutput always
produces the identical image (snapshot-stable — no timestamps, no randomness), and every
number on the diagram comes straight from the design. A single `_scene()` describes the
boxes, arrows, and labels; `to_svg()` renders it to a self-contained SVG string and
`to_png()` rasterizes it with Pillow (so it can be sent as an inline photo in chat). Works
for both aquaponic (fish + biofilter + beds) and hydroponic (reservoir + beds, no fish)
systems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .types import DesignOutput, HydroponicOutput

_W, _H = 720, 420


def _g(x) -> str:
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return str(x)


@dataclass
class _Box:
    x: int
    y: int
    w: int
    h: int
    title: str
    lines: list
    fill: str


@dataclass
class _Arrow:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""


@dataclass
class _Scene:
    title: str
    status: str
    boxes: list = field(default_factory=list)
    arrows: list = field(default_factory=list)


def _grow_bed_lines(out: DesignOutput) -> list:
    """The grow-bed box body: a mixed bed lists each crop and its area; a single bed just
    shows the planted area. System volume is always shown."""
    if out.crop_plan:
        lines = [f"{p['crop']} {_g(p['area_m2'])} m2" for p in out.crop_plan[:3]]
        if len(out.crop_plan) > 3:
            lines.append(f"+{len(out.crop_plan) - 3} more")
        lines.append(f"system {_g(out.system_volume_l)} L")
        return lines
    return [f"{_g(out.grow_area_m2)} m2 planted", f"system {_g(out.system_volume_l)} L"]


def _aqua_scene(out: DesignOutput) -> _Scene:
    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE ({out.binding_constraint})"
    method = (out.system_type or "raft").upper().replace("_", " ")
    s = _Scene(f"Aquaponics System — {method}", status)
    s.boxes = [
        _Box(40, 90, 180, 90, "Rearing tank (fish)", [
            f"{out.fish_count} fish, {_g(out.fish_biomass_kg)} kg",
            f"~{_g(out.rearing_tank_volume_l)} L", f"feed {_g(out.feed_g_per_day)} g/day"], "#dbeafe"),
        _Box(270, 90, 170, 90, "Biofilter", [
            f"~{_g(out.biofilter_media_m2)} m2 media", "nitrification"], "#e6f4ea"),
        _Box(490, 90, 190, 90, out.grow_bed_label,
             _grow_bed_lines(out), "#e8f5e9"),
        _Box(270, 250, 170, 80, "Sump + pump", [
            f"pump >={_g(out.pump_turnover_lph)} L/h", f"makeup {_g(out.makeup_water_lpd)} L/day"], "#fff7ed"),
    ]
    s.arrows = [
        _Arrow(220, 135, 270, 135, "water"), _Arrow(440, 135, 490, 135, "nitrate"),
        _Arrow(585, 180, 400, 250, "drain"), _Arrow(270, 285, 130, 180, "return"),
    ]
    return s


def _hydro_scene(out: HydroponicOutput) -> _Scene:
    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE ({out.binding_constraint})"
    ec = out.nutrient_target.get("ec_mS_cm", {})
    method = (out.system_type or "raft").upper().replace("_", " ")
    s = _Scene(f"Hydroponic System (no fish) — {method}", status)
    s.boxes = [
        _Box(60, 100, 200, 100, "Nutrient reservoir", [
            f"~{_g(out.reservoir_volume_l)} L solution",
            f"EC {_g(ec.get('low'))}-{_g(ec.get('high'))} mS/cm",
            f"N {_g(out.nutrient_target.get('elemental_n_g_per_day'))} g/day"], "#e0f2fe"),
        _Box(460, 100, 200, 100, out.grow_bed_label, [
            f"{_g(out.grow_area_m2)} m2 planted",
            f"water use {_g(out.daily_water_use_lpd)} L/day"], "#e8f5e9"),
        _Box(260, 270, 200, 80, "Pump", [
            f">={_g(out.pump_turnover_lph)} L/h", f"makeup {_g(out.makeup_water_lpd)} L/day"], "#fff7ed"),
    ]
    s.arrows = [
        _Arrow(260, 150, 460, 150, "dosed solution"),
        _Arrow(560, 200, 400, 270, "return"), _Arrow(260, 300, 160, 200, "circulate"),
    ]
    return s


def _scene(out) -> _Scene:
    if isinstance(out, HydroponicOutput):
        return _hydro_scene(out)
    if isinstance(out, DesignOutput):
        return _aqua_scene(out)
    raise TypeError(f"expected a DesignOutput or HydroponicOutput, got {type(out)!r}")


# --- SVG renderer -----------------------------------------------------------
_FONT = "font-family='sans-serif'"


def _svg_box(b: _Box) -> str:
    parts = [
        f"<rect x='{b.x}' y='{b.y}' width='{b.w}' height='{b.h}' rx='8' fill='{b.fill}' "
        f"stroke='#33475b' stroke-width='2'/>",
        f"<text x='{b.x + b.w / 2}' y='{b.y + 20}' text-anchor='middle' {_FONT} font-size='14' "
        f"font-weight='bold' fill='#0b1b2b'>{escape(b.title)}</text>",
    ]
    for i, ln in enumerate(b.lines):
        parts.append(f"<text x='{b.x + b.w / 2}' y='{b.y + 40 + i * 16}' text-anchor='middle' "
                     f"{_FONT} font-size='12' fill='#22303c'>{escape(ln)}</text>")
    return "".join(parts)


def _svg_arrow(a: _Arrow) -> str:
    line = (f"<line x1='{a.x1}' y1='{a.y1}' x2='{a.x2}' y2='{a.y2}' stroke='#2b6cb0' "
            f"stroke-width='2' marker-end='url(#arrow)'/>")
    text = ""
    if a.label:
        text = (f"<text x='{(a.x1 + a.x2) / 2}' y='{(a.y1 + a.y2) / 2 - 6}' text-anchor='middle' "
                f"{_FONT} font-size='11' fill='#2b6cb0'>{escape(a.label)}</text>")
    return line + text


def to_svg(out) -> str:
    """Render a sized design to a self-contained SVG string (DesignOutput or HydroponicOutput)."""
    s = _scene(out)
    defs = ("<defs><marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' "
            "markerHeight='7' orient='auto-start-reverse'>"
            "<path d='M0,0 L10,5 L0,10 z' fill='#2b6cb0'/></marker></defs>")
    status_color = "#1a7f37" if s.status.startswith("FEASIBLE") else "#b42318"
    header = (
        f"<text x='{_W / 2}' y='28' text-anchor='middle' {_FONT} font-size='18' "
        f"font-weight='bold' fill='#0b1b2b'>{escape(s.title)}</text>"
        f"<text x='{_W / 2}' y='48' text-anchor='middle' {_FONT} font-size='12' "
        f"fill='{status_color}'>{escape(s.status)}</text>")
    body = header + "".join(_svg_arrow(a) for a in s.arrows) + "".join(_svg_box(b) for b in s.boxes)
    return ("<?xml version='1.0' encoding='UTF-8'?>"
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{_W}' height='{_H}' "
            f"viewBox='0 0 {_W} {_H}'><rect width='{_W}' height='{_H}' fill='#f7fafc'/>"
            f"{defs}{body}</svg>")


# --- PNG renderer (Pillow) --------------------------------------------------
def _font(size: int):
    from PIL import ImageFont
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _center_text(draw, cx, y, text, font, fill):
    try:
        w = draw.textlength(text, font=font)
    except AttributeError:  # very old Pillow
        w = font.getsize(text)[0]
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def to_png(out) -> bytes:
    """Rasterize the schematic to PNG bytes (for an inline chat photo). Deterministic."""
    import io
    from PIL import Image, ImageDraw

    s = _scene(out)
    img = Image.new("RGB", (_W, _H), "#f7fafc")
    d = ImageDraw.Draw(img)
    f_title, f_box_title, f_line, f_status, f_arrow = (
        _font(18), _font(14), _font(12), _font(12), _font(11))

    _center_text(d, _W / 2, 14, s.title, f_title, "#0b1b2b")
    _center_text(d, _W / 2, 38, s.status, f_status,
                 "#1a7f37" if s.status.startswith("FEASIBLE") else "#b42318")

    for a in s.arrows:
        d.line((a.x1, a.y1, a.x2, a.y2), fill="#2b6cb0", width=2)
        if a.label:
            _center_text(d, (a.x1 + a.x2) / 2, (a.y1 + a.y2) / 2 - 14, a.label, f_arrow, "#2b6cb0")

    for b in s.boxes:
        d.rounded_rectangle((b.x, b.y, b.x + b.w, b.y + b.h), radius=8,
                            fill=b.fill, outline="#33475b", width=2)
        _center_text(d, b.x + b.w / 2, b.y + 8, b.title, f_box_title, "#0b1b2b")
        for i, ln in enumerate(b.lines):
            _center_text(d, b.x + b.w / 2, b.y + 30 + i * 16, ln, f_line, "#22303c")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
