"""Deterministic SVG schematic: a labeled 2-D diagram generated purely from a sized design.
No ML, no network — the same inputs always produce the same SVG (snapshot-stable), and the
numbers on the diagram match the design's numbers.
"""

import xml.dom.minidom as minidom

import pytest

from aqua_model import (
    size_hydroponic_system,
    size_system,
    validate_design_input,
    validate_hydroponic_input,
)
from aqua_model.schematic import to_svg


def _aqua():
    return size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000))


def _hydro():
    return size_hydroponic_system(validate_hydroponic_input("lettuce", 12, 22, 3000))


def test_svg_is_well_formed_xml():
    svg = to_svg(_aqua())
    doc = minidom.parseString(svg)                 # raises if malformed
    assert doc.documentElement.tagName == "svg"
    assert svg.strip().startswith("<?xml") or svg.strip().startswith("<svg")


def test_aquaponic_schematic_shows_fish_and_plant_components():
    svg = to_svg(_aqua())
    low = svg.lower()
    assert "fish" in low or "rearing" in low
    assert "grow" in low or "raft" in low
    assert "biofilter" in low
    assert "pump" in low


def test_hydroponic_schematic_shows_reservoir_not_fish():
    svg = to_svg(_hydro())
    low = svg.lower()
    assert "reservoir" in low or "nutrient" in low
    assert "rearing tank" not in low and "biofilter" not in low


def test_numbers_on_diagram_match_the_design():
    out = _aqua()
    svg = to_svg(out)
    # the planted grow area label must reflect the real number
    assert f"{out.grow_area_m2:g}" in svg
    # fish count appears somewhere on the diagram
    assert str(out.fish_count) in svg


def test_svg_is_deterministic():
    assert to_svg(_aqua()) == to_svg(_aqua())      # snapshot-stable, no timestamps/random


def test_svg_has_no_external_references():
    # self-contained: no remote images, fonts, or scripts (safe to embed/send anywhere).
    # The xmlns namespace URI is not a fetch, so we check for real external references.
    low = to_svg(_aqua()).lower()
    assert "<script" not in low
    assert "<image" not in low and "xlink:href" not in low
    assert "url(http" not in low and "@import" not in low


def test_to_png_returns_a_valid_png_image():
    import io

    from PIL import Image

    from aqua_model.schematic import to_png

    for out in (_aqua(), _hydro()):
        data = to_png(out)
        assert isinstance(data, (bytes, bytearray)) and data[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(data))
        img.load()                                  # raises if corrupt
        assert img.width > 200 and img.height > 100


def test_to_png_is_deterministic():
    from aqua_model.schematic import to_png
    assert to_png(_aqua()) == to_png(_aqua())


def test_font_prefers_a_font_the_base_system_actually_ships(monkeypatch):
    """DejaVu before Arial, or the PNG loses its type hierarchy off Windows.

    `fonts-dejavu-core` is a base package on Debian and Ubuntu, where the bot renders
    these schematics. Arial only exists there if someone installed msttcorefonts by
    hand. If every candidate misses, Pillow falls back to `load_default()`, which
    ignores the requested size — so 18/14/12/11 all come out the same bitmap size.
    """
    from PIL import ImageFont

    from aqua_model import schematic

    tried = []

    def _record(name, size=10, *args, **kwargs):
        tried.append(name)
        raise OSError("not here")

    monkeypatch.setattr(ImageFont, "truetype", _record)
    # load_default() reaches for truetype internally, so it needs stubbing too.
    monkeypatch.setattr(ImageFont, "load_default", lambda *a, **k: "fallback")
    for bold in (False, True):
        tried.clear()
        schematic._font(12, bold=bold)
        assert tried, "no font was attempted"
        assert "DejaVu" in tried[0], f"tried {tried[0]} before DejaVu (bold={bold})"


def test_font_sizes_stay_distinct_when_a_truetype_resolves():
    from aqua_model.schematic import _font

    big, small = _font(18), _font(11)
    if not hasattr(big, "size"):  # bitmap fallback: no fonts on this machine at all
        pytest.skip("no TrueType font available on this system")
    assert big.size == 18 and small.size == 11
