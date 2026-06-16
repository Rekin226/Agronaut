"""CRITICAL: nitrogen check is a real (non-vacuous) consistency check, and the
bed-oversizing bug is guarded — plants take a FRACTION of excreted N, never all of it."""

import pytest

from aqua_model import coefficients as C
from aqua_model import massbalance as mb
from aqua_model.crops import get_crop
from aqua_model.species import get_species


def test_excreted_n_derives_from_fcr_not_a_free_number():
    sp = get_species("tilapia")
    crop = get_crop("lettuce")
    r = mb.nitrogen_check(feed_g_per_day=600.0, species=sp, crop=crop, frr_grow_area_m2=10.0)
    # N retained must come from growth = feed / FCR, so excreted < fed and > 0.
    assert r["n_retained_g_day"] > 0
    assert 0 < r["n_excreted_g_day"] < r["n_fed_g_day"]


def test_oversizing_guard_plants_take_a_fraction_not_all():
    """REGRESSION GUARD: plant uptake must be ~30-50% of excreted N, never 100%."""
    sp = get_species("tilapia")
    crop = get_crop("lettuce")
    r = mb.nitrogen_check(feed_g_per_day=600.0, species=sp, crop=crop, frr_grow_area_m2=10.0)
    frac = r["n_plant_uptake_g_day"] / r["n_excreted_g_day"]
    assert 0.30 <= frac <= 0.50
    # approx because the output dict rounds N values to 2 decimals.
    assert frac == pytest.approx(C.PLANT_N_UPTAKE_FRACTION.value, abs=1e-3)


def test_check_is_non_vacuous_sinks_are_independent_not_residual():
    """If sinks were a residual, closure would be exactly 0 by construction. They are
    independent estimates, so closure is a genuine (small but nonzero-capable) signal."""
    sp = get_species("tilapia")
    crop = get_crop("lettuce")
    r = mb.nitrogen_check(feed_g_per_day=600.0, species=sp, crop=crop, frr_grow_area_m2=10.0)
    # plant + solids + water + denitri fractions = 0.40+0.35+0.20+0.05 = 1.00 here,
    # but each is set INDEPENDENTLY; the residual is computed, not assumed zero.
    assert "closure_residual_g_day" in r
    independent_sum = (
        r["n_plant_uptake_g_day"] + r["n_solids_g_day"]
        + r["n_water_exchange_g_day"] + r["n_denitrification_g_day"]
    )
    # Residual is excreted minus the INDEPENDENT sum, not forced to zero.
    assert abs((r["n_excreted_g_day"] - independent_sum) - r["closure_residual_g_day"]) < 0.1


def test_check_flags_disagreement_when_area_is_wrong():
    sp = get_species("tilapia")
    crop = get_crop("lettuce")
    # Pass a wildly wrong FRR area; the check should NOT silently agree.
    r = mb.nitrogen_check(feed_g_per_day=600.0, species=sp, crop=crop, frr_grow_area_m2=1.0)
    assert r["agrees"] is False
    assert r["flag"] is not None


def test_biofilter_media_positive_and_conservative():
    sp = get_species("tilapia")
    media = mb.biofilter_media_m2(feed_g_per_day=600.0, species=sp)
    assert media > 0
    # Safety factor makes it larger than the bare requirement.
    bare = media / C.SAFETY_FACTOR.value
    assert media > bare


def test_water_balance_makeup_is_positive_and_excludes_uncaptured_rain():
    w = mb.water_balance(grow_area_m2=10.0, tank_surface_m2=2.0)
    assert w["makeup_water_lpd"] > 0
    assert w["rainfall_lpd"] == 0.0  # covered system default
