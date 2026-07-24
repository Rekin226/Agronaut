"""to_svg() — a deterministic, self-contained SVG schematic of a sized system.

Pure code, no ML and no dependencies: the same DesignOutput/HydroponicOutput always renders
the identical SVG (snapshot-stable — no timestamps, no randomness), and every number on the
diagram comes straight from the design. Works for both aquaponic (fish + plants + biofilter)
and hydroponic (reservoir + plants, no fish) systems. The SVG is self-contained (inline
styles, no external images/fonts/scripts) so it can be embedded in a report or sent in chat.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .types import DesignOutput, HydroponicOutput

_W, _H = 720, 420
_FONT = "font-family='sans-serif'"


def _g(x) -> str:
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return str(x)


def _box(x, y, w, h, title, lines, fill) -> str:
    parts = [
        f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='8' "
        f"fill='{fill}' stroke='#33475b' stroke-width='2'/>",
        f"<text x='{x + w / 2}' y='{y + 20}' text-anchor='middle' {_FONT} "
        f"font-size='14' font-weight='bold' fill='#0b1b2b'>{escape(title)}</text>",
    ]
    for i, ln in enumerate(lines):
        parts.append(
            f"<text x='{x + w / 2}' y='{y + 40 + i * 16}' text-anchor='middle' {_FONT} "
            f"font-size='12' fill='#22303c'>{escape(ln)}</text>")
    return "".join(parts)


def _arrow(x1, y1, x2, y2, label="") -> str:
    line = (f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='#2b6cb0' "
            f"stroke-width='2' marker-end='url(#arrow)'/>")
    text = ""
    if label:
        text = (f"<text x='{(x1 + x2) / 2}' y='{(y1 + y2) / 2 - 6}' text-anchor='middle' "
                f"{_FONT} font-size='11' fill='#2b6cb0'>{escape(label)}</text>")
    return line + text


def _header(title: str, status: str) -> str:
    return (
        f"<text x='{_W / 2}' y='28' text-anchor='middle' {_FONT} font-size='18' "
        f"font-weight='bold' fill='#0b1b2b'>{escape(title)}</text>"
        f"<text x='{_W / 2}' y='48' text-anchor='middle' {_FONT} font-size='12' "
        f"fill='{'#1a7f37' if status.startswith('FEASIBLE') else '#b42318'}'>{escape(status)}</text>"
    )


def _wrap(body: str) -> str:
    defs = ("<defs><marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' "
            "markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
            "<path d='M0,0 L10,5 L0,10 z' fill='#2b6cb0'/></marker></defs>")
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{_W}' height='{_H}' "
        f"viewBox='0 0 {_W} {_H}'>"
        f"<rect width='{_W}' height='{_H}' fill='#f7fafc'/>{defs}{body}</svg>"
    )


def _aquaponic_svg(out: DesignOutput) -> str:
    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE ({out.binding_constraint})"
    body = [_header("Aquaponics System", status)]
    # rearing tank (fish) -> biofilter -> grow beds -> sump/pump -> back to tank
    body.append(_box(40, 90, 180, 90, "Rearing tank (fish)", [
        f"{out.fish_count} fish, {_g(out.fish_biomass_kg)} kg",
        f"~{_g(out.rearing_tank_volume_l)} L",
        f"feed {_g(out.feed_g_per_day)} g/day",
    ], "#dbeafe"))
    body.append(_box(270, 90, 170, 90, "Biofilter", [
        f"~{_g(out.biofilter_media_m2)} m2 media",
        "nitrification",
    ], "#e6f4ea"))
    body.append(_box(490, 90, 190, 90, "Grow beds (raft/DWC)", [
        f"{_g(out.grow_area_m2)} m2 planted",
        f"system {_g(out.system_volume_l)} L",
    ], "#e8f5e9"))
    body.append(_box(270, 250, 170, 80, "Sump + pump", [
        f"pump ≥{_g(out.pump_turnover_lph)} L/h",
        f"makeup {_g(out.makeup_water_lpd)} L/day",
    ], "#fff7ed"))
    body.append(_arrow(220, 135, 270, 135, "water"))
    body.append(_arrow(440, 135, 490, 135, "nitrate"))
    body.append(_arrow(585, 180, 400, 250, "drain"))
    body.append(_arrow(270, 285, 130, 180, "return"))
    return _wrap("".join(body))


def _hydroponic_svg(out: HydroponicOutput) -> str:
    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE ({out.binding_constraint})"
    ec = out.nutrient_target.get("ec_mS_cm", {})
    body = [_header("Hydroponic System (no fish)", status)]
    body.append(_box(60, 100, 200, 100, "Nutrient reservoir", [
        f"~{_g(out.reservoir_volume_l)} L solution",
        f"EC {_g(ec.get('low'))}-{_g(ec.get('high'))} mS/cm",
        f"N {_g(out.nutrient_target.get('elemental_n_g_per_day'))} g/day",
    ], "#e0f2fe"))
    body.append(_box(460, 100, 200, 100, "Grow beds (DWC/NFT)", [
        f"{_g(out.grow_area_m2)} m2 planted",
        f"water use {_g(out.daily_water_use_lpd)} L/day",
    ], "#e8f5e9"))
    body.append(_box(260, 270, 200, 80, "Pump", [
        f"≥{_g(out.pump_turnover_lph)} L/h",
        f"makeup {_g(out.makeup_water_lpd)} L/day",
    ], "#fff7ed"))
    body.append(_arrow(260, 150, 460, 150, "dosed solution"))
    body.append(_arrow(560, 200, 400, 270, "return"))
    body.append(_arrow(260, 300, 160, 200, "circulate"))
    return _wrap("".join(body))


def to_svg(out) -> str:
    """Render a sized design to a self-contained SVG string. Accepts either a DesignOutput
    (aquaponic) or a HydroponicOutput."""
    if isinstance(out, HydroponicOutput):
        return _hydroponic_svg(out)
    if isinstance(out, DesignOutput):
        return _aquaponic_svg(out)
    raise TypeError(f"to_svg expects a DesignOutput or HydroponicOutput, got {type(out)!r}")
