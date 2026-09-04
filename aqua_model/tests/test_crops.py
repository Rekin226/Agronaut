"""Crop database invariants — every seed crop must be internally consistent and cited."""

import pytest

from aqua_model.crops import CROPS, get_crop


def test_strawberry_is_supported():
    c = get_crop("strawberry")
    assert c.category == "fruiting"
    assert c.source  # must carry a citation


def test_expanded_catalog_present():
    # A representative sample of the large expansion — herbs, greens, fruiting.
    for name in ("mint", "cilantro", "parsley", "arugula", "pak_choi",
                 "cabbage", "broccoli", "strawberry", "eggplant", "zucchini", "pea"):
        assert name in CROPS, name


def test_catalog_size():
    assert len(CROPS) >= 30


@pytest.mark.parametrize("name", sorted(CROPS))
def test_crop_invariants(name):
    c = CROPS[name]
    assert c.name == name
    assert c.category in ("leafy", "fruiting"), c.category
    # feeding-rate ratio is positive and the seed sits inside its own low/high band
    assert 0 < c.frr_low <= c.frr_g_per_m2_day <= c.frr_high
    assert c.n_uptake_g_per_m2_day > 0
    assert c.yield_kg_per_m2_year > 0
    assert 0 <= c.edible_protein_pct < 100
    assert 0 < c.ph_min < c.ph_max < 14
    assert c.temp_min_c < c.temp_max_c
    assert c.source, "every coefficient set must cite a source"


def test_get_crop_is_case_insensitive():
    assert get_crop("Strawberry").name == "strawberry"
    assert get_crop("  PEA ").name == "pea"


def test_unknown_crop_lists_known():
    with pytest.raises(KeyError) as e:
        get_crop("dragonfruit")
    assert "strawberry" in str(e.value)  # error names the known set


def test_amaranth_is_the_heat_tolerant_leafy_option():
    """#104: the database had 30 crops and exactly one (okra, fruiting) tolerated 32 C.

    A system simulated in the Sahel kept reporting temperature as the limiting factor —
    not because aquaponics fails there, but because every leafy crop on offer wilts at
    30 C. Amaranth is the answer to that, so its temperature band is the assertion that
    matters: if a future edit narrows it, the gap this crop was added to close reopens
    silently and the twin goes back to blaming the climate.
    """
    c = get_crop("amaranth")
    assert c.category == "leafy"
    assert c.temp_max_c >= 35.0, "amaranth is here for the heat; 35 C is the point"
    assert c.temp_min_c <= 18.0

    leafy_above_30 = [k for k, x in CROPS.items()
                      if x.category == "leafy" and x.temp_max_c > 30.0]
    assert leafy_above_30 == ["amaranth"], (
        "amaranth should be the leafy crop that carries hot climates; if another "
        f"joins it, widen this assertion deliberately. Found: {leafy_above_30}"
    )


def test_amaranth_sizes_a_system_without_error():
    """The acceptance criterion from #104: it has to actually run, not just parse."""
    from aqua_model import size_system, validate_design_input

    out = size_system(validate_design_input("tilapia", "amaranth", 12.0, 29.0, 3000.0))
    assert out.feed_g_per_day > 0
    assert out.fish_count > 0
    assert out.biofilter_media_m2 > 0


def test_amaranth_has_the_burkina_price_it_was_waiting_on():
    """The price existed before the crop did — #104 was what let them meet.

    `data/price_book.json` has carried a Burkinabe farm-gate amaranth price with nothing
    to attach it to. Now that the crop exists the two are connected, and this asserts the
    connection rather than leaving it to be noticed.
    """
    import json
    import pathlib

    book = json.loads(
        (pathlib.Path(__file__).resolve().parents[2] / "data" / "price_book.json")
        .read_text(encoding="utf-8")
    )
    revenue = book["regions"]["burkina_faso"]["revenue_items"]
    assert "crop_amaranth" in revenue
    assert get_crop("amaranth").name == "amaranth"
