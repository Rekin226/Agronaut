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
