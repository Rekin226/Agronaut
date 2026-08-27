"""Deterministic SVG schematic: a labeled 2-D diagram generated purely from a sized design.
No ML, no network — the same inputs always produce the same SVG (snapshot-stable), and the
numbers on the diagram match the design's numbers.
"""

import xml.dom.minidom as minidom

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
