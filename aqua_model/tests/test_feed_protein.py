"""The feeding-rate ratio was measured on 32%-protein feed, so sizing scales it by protein.

Found 2026-10-04: barramundi on 45%-protein feed was sized with the same grams of feed per m2
as tilapia on 32%, about 40% more nitrogen than the beds could take up, and the design's own
nitrogen check flagged it 62% off. These pin the correction and what it must not disturb.
"""

import pytest

from aqua_model import OptimizeInput, optimize, size_system
from aqua_model import coefficients as C
from aqua_model.crops import get_crop
from aqua_model.species import SPECIES, get_species
from aqua_model.types import DesignInput


def _design(fish, crop="lettuce", area=6.0):
    return DesignInput(fish_species=fish, crop=crop, grow_area_m2=area, temperature_c=26.0,
                       water_budget_lpd=1_000_000.0)


def test_the_reference_feed_is_cited_and_in_the_registry():
    c = C.FRR_REFERENCE_FEED_PROTEIN_PCT
    assert c.value == 32.0 and "FAO589" in c.source
    assert C.registry()["frr_reference_feed_protein_pct"] is c


def test_the_factor_is_one_at_the_reference_and_falls_with_richer_feed():
    assert C.frr_protein_factor(32.0) == 1.0
    assert C.frr_protein_factor(45.0) == pytest.approx(32 / 45)
    assert C.frr_protein_factor(28.0) > 1.0
    with pytest.raises(ValueError):
        C.frr_protein_factor(0.0)


def test_a_32_percent_feed_is_sized_exactly_as_before():
    out = size_system(_design("tilapia"))
    assert out.feed_g_per_day == pytest.approx(6.0 * get_crop("lettuce").frr_g_per_m2_day)


def test_barramundi_feed_is_scaled_and_the_nitrogen_check_now_agrees():
    out = size_system(_design("barramundi"))
    assert out.feed_g_per_day == pytest.approx(6.0 * 60.0 * 32 / 45, abs=0.1)
    assert out.nitrogen_check["agrees"] is True
    assert not any("disagree" in w for w in out.warnings)


@pytest.mark.parametrize("fish", sorted(SPECIES))
def test_every_species_now_passes_its_own_nitrogen_check_on_lettuce(fish):
    """Before the fix barramundi failed this; every other species was already within the 35%
    tolerance. Holding nitrogen per m2 constant brings them all to the same footing."""
    assert size_system(_design(fish)).nitrogen_check["agrees"] is True


def test_the_design_says_what_it_did_and_cites_it():
    out = size_system(_design("barramundi"))
    assert any("scaled x0.71" in a and "45%" in a and "32%" in a for a in out.assumptions)
    assert any(c.name == "frr_reference_feed_protein_pct" for c in out.coefficients_used)
    assert any("not 32% protein" in n for n in out.not_modeled)


def test_the_optimizer_feeds_exactly_what_the_calculator_sizes():
    inp = OptimizeInput(grow_area_m2=10.0, temperature_c=26.0, water_budget_lpd=1_000_000.0,
                        objective="food", fish_palette=("barramundi",),
                        crop_palette=("lettuce",))
    best = optimize(inp).best
    assert best.feed_g_per_day == pytest.approx(
        size_system(_design("barramundi", area=10.0)).feed_g_per_day, abs=0.1)
    assert get_species("barramundi").feed_protein_pct == 45.0
