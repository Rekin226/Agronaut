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
    assert sorted(leafy_above_30) == ["amaranth", "malabar_spinach", "moringa",
                                      "water_spinach"], (
        "amaranth, water_spinach, malabar_spinach (#125) and moringa are the leafy "
        f"crops that carry hot climates; if another joins them, widen this assertion "
        f"deliberately. Found: {leafy_above_30}"
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


def test_water_spinach_is_heat_tolerant_and_semi_aquatic():
    """Water spinach (kangkong) is heat-tolerant and ideal for raft culture."""
    ws = get_crop("water_spinach")
    assert ws.temp_max_c >= 35.0
    assert ws.temp_min_c >= 18.0
    assert ws.category == "leafy"
    assert "FRR placed" in ws.source, "FRR placement must be clearly stated"
    assert ws.yield_kg_per_m2_year > 10.0
    assert ws.yield_kg_per_m2_year < 25.0


def test_malabar_spinach_is_heat_tolerant_and_cold_sensitive():
    """#125: malabar spinach (Basella alba), the sixth of the six wanted crops.

    Its identity in the database is the pairing of a 35 °C ceiling with a 20 °C
    floor: it carries hot climates the way amaranth and water spinach do, but it
    is also the leafy entry that most refuses cool weather (poor growth below
    ~27 °C, UF/IFAS HS1371). If a future edit narrows the band, the reason this
    crop was added reopens silently.
    """
    ms = get_crop("malabar_spinach")
    assert ms.category == "leafy"
    assert ms.temp_max_c >= 35.0
    assert ms.temp_min_c >= 20.0
    assert "FRR placed" in ms.source, "FRR placement must be clearly stated"
    assert ms.yield_kg_per_m2_year > 10.0
    assert ms.yield_kg_per_m2_year < 25.0


def test_malabar_spinach_sizes_a_system_without_error():
    """The acceptance criterion from #125: it has to actually run, not just parse."""
    from aqua_model import size_system, validate_design_input

    out = size_system(validate_design_input("tilapia", "malabar_spinach", 12.0, 29.0, 3000.0))
    assert out.feed_g_per_day > 0
    assert out.fish_count > 0
    assert out.biofilter_media_m2 > 0


def test_moringa_is_heat_tolerant_and_drought_hardy():
    """#125: moringa (Moringa oleifera), the seventh wanted heat-tolerant leafy.

    Its identity in the database is the pairing of the 35 °C ceiling with the
    warmest floor of the group (20 °C): a chilling-sensitive tropical species whose
    cited optimum (25-35 °C, Trigo et al. 2021) sits entirely in the heat. It is
    also the protein outlier — 9.4 g/100 g fresh against 1.4-2.6 for the rest of
    the leafies — so an edit that quietly lowers either number breaks the reason
    this crop was added.
    """
    mo = get_crop("moringa")
    assert mo.category == "leafy"
    assert mo.temp_max_c >= 35.0
    assert mo.temp_min_c >= 20.0
    assert "FRR placed" in mo.source, "FRR placement must be clearly stated"
    assert mo.edible_protein_pct >= 9.0, (
        "moringa is here for the protein too; 9.4 g/100 g fresh (USDA FDC) is the point"
    )
    assert mo.yield_kg_per_m2_year > 10.0
    assert mo.yield_kg_per_m2_year < 25.0


def test_moringa_sizes_a_system_without_error():
    """The acceptance criterion from #125: it has to actually run, not just parse."""
    from aqua_model import size_system, validate_design_input

    out = size_system(validate_design_input("tilapia", "moringa", 12.0, 29.0, 3000.0))

    assert out.feed_g_per_day > 0
    assert out.fish_count > 0
    assert out.biofilter_media_m2 > 0


def test_ethiopian_kale_is_the_heat_tolerant_brassica():
    """#125: Ethiopian kale (Brassica carinata), a wanted heat-tolerant leafy.

    Its identity in the database is the pairing of a 30 °C ceiling with a 15 °C
    floor and no vernalization requirement: the leafy brassica slot (kale,
    collards, mustard greens all stop at 24-27 °C) filled for warm climates.
    Both endpoints are sourced, not one: ECHO's Mutarda carinata sheet states
    "Temperature range: 15-20° C" and OMAFRA's Specialty Cropportunities states
    "Optimal Temperature Range: 20-30˚C" — the 30 is the species optimum, placed
    on the leafy use. If a future edit narrows the band, the reason this crop was
    added reopens silently.
    """
    ek = get_crop("ethiopian_kale")
    assert ek.category == "leafy"
    assert ek.temp_max_c >= 30.0
    assert ek.temp_min_c >= 15.0
    assert ek.temp_max_c <= 32.0  # sourced ceiling, not a copy of amaranth's 35
    assert "FRR placed" in ek.source, "FRR placement must be clearly stated"
    assert "juncea" in ek.source, "protein figure must name its B. juncea inheritance"
    assert "Mnzava & Schippers 2007" in ek.source, "PROTA citation must name its authors"
    assert "0.40 x 4x" in ek.source, (
        "yield chain must state the real leaf fraction and multiplier, "
        "not 'a conservative fraction of' an unstated size")
    assert ek.yield_kg_per_m2_year > 10.0
    assert ek.yield_kg_per_m2_year < 25.0


def test_ethiopian_kale_sizes_a_system_without_error():
    """The acceptance criterion from #125: it has to actually run, not just parse."""
    from aqua_model import size_system, validate_design_input

    out = size_system(validate_design_input("tilapia", "ethiopian_kale", 12.0, 29.0, 3000.0))

    assert out.feed_g_per_day > 0
    assert out.fish_count > 0
    assert out.biofilter_media_m2 > 0

