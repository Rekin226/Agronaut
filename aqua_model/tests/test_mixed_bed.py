"""Mixed beds — the design is not locked to one crop. An operator can grow several crops in
the same water (lettuce + basil + tomato), and the deterministic model sizes the shared
system by SUMMING each crop's feed demand over its own area (FRR is per-crop, per-m²).

Two invariants make this trustworthy rather than just flexible:
  1. A single-crop plan reproduces the single-crop design EXACTLY (no silent drift).
  2. Crops that cannot share one water chemistry are FLAGGED, never quietly averaged.
"""

import pytest

from aqua_model import ValidationError, size_system, validate_design_input


def test_single_entry_plan_matches_single_crop_exactly():
    # A one-crop plan is just the old single-crop design — byte-identical, no regression.
    plan = size_system(validate_design_input(
        "tilapia", None, None, 27, 5000, crop_plan=[{"crop": "lettuce", "area_m2": 20}]))
    single = size_system(validate_design_input("tilapia", "lettuce", 20, 27, 5000))
    assert plan.feed_g_per_day == single.feed_g_per_day
    assert plan.fish_count == single.fish_count
    assert plan.system_volume_l == single.system_volume_l
    assert plan.grow_area_m2 == single.grow_area_m2


def test_feed_is_the_sum_of_per_crop_demand():
    # Feed must equal each crop's own FRR × its own area, summed — not one crop over the total.
    mix = size_system(validate_design_input(
        "tilapia", None, None, 27, 8000,
        crop_plan=[{"crop": "lettuce", "area_m2": 10}, {"crop": "basil", "area_m2": 10}]))
    lettuce = size_system(validate_design_input("tilapia", "lettuce", 10, 27, 8000))
    basil = size_system(validate_design_input("tilapia", "basil", 10, 27, 8000))
    assert mix.feed_g_per_day == pytest.approx(lettuce.feed_g_per_day + basil.feed_g_per_day)
    assert mix.grow_area_m2 == 20


def test_mixing_leafy_and_fruiting_lands_between_the_monocultures():
    # Basil (leafy, lower FRR) + tomato (fruiting, higher FRR) → feed between the two extremes.
    mix = size_system(validate_design_input(
        "tilapia", None, None, 27, 12000,
        crop_plan=[{"crop": "basil", "area_m2": 10}, {"crop": "tomato", "area_m2": 10}]))
    all_basil = size_system(validate_design_input("tilapia", "basil", 20, 27, 12000))
    all_tomato = size_system(validate_design_input("tilapia", "tomato", 20, 27, 12000))
    assert all_basil.feed_g_per_day < mix.feed_g_per_day < all_tomato.feed_g_per_day


def test_crop_plan_appears_in_output_and_assumptions():
    out = size_system(validate_design_input(
        "tilapia", None, None, 27, 8000,
        crop_plan=[{"crop": "lettuce", "area_m2": 6}, {"crop": "basil", "area_m2": 4}]))
    assert len(out.crop_plan) == 2
    names = {p["crop"] for p in out.crop_plan}
    assert names == {"lettuce", "basil"}
    text = " ".join(out.assumptions).lower()
    assert "lettuce" in text and "basil" in text


def test_incompatible_ph_bands_are_flagged_not_averaged():
    # watercress wants an alkaline band (6.5–7.5); strawberry an acidic one (5.5–6.5).
    # Their shared pH window is razor-thin/nonexistent — the model must WARN, not silently pick one.
    out = size_system(validate_design_input(
        "tilapia", None, None, 26, 8000,
        crop_plan=[{"crop": "watercress", "area_m2": 10}, {"crop": "strawberry", "area_m2": 10}]))
    warned = " ".join(out.warnings).lower()
    assert "ph" in warned and ("share" in warned or "compat" in warned or "overlap" in warned)


def test_compatible_crops_do_not_falsely_warn_about_ph():
    out = size_system(validate_design_input(
        "tilapia", None, None, 26, 8000,
        crop_plan=[{"crop": "lettuce", "area_m2": 10}, {"crop": "basil", "area_m2": 10}]))
    assert not any("ph" in w.lower() and "share" in w.lower() for w in out.warnings)


def test_operating_envelope_ph_is_the_intersection():
    # The shared pH band must be the INTERSECTION of both crops, never a widening.
    out = size_system(validate_design_input(
        "tilapia", None, None, 26, 8000,
        crop_plan=[{"crop": "lettuce", "area_m2": 10}, {"crop": "tomato", "area_m2": 10}]))
    lo, hi = out.operating_envelope["ph_do_not_exceed"]
    # lettuce 5.5–7.0, tomato 5.5–6.5 → intersection 5.5–6.5
    assert lo == 5.5 and hi == 6.5


def test_empty_plan_is_rejected():
    with pytest.raises(ValidationError):
        validate_design_input("tilapia", None, None, 27, 5000, crop_plan=[])


def test_unknown_crop_in_plan_is_rejected():
    with pytest.raises(ValidationError):
        validate_design_input(
            "tilapia", None, None, 27, 5000,
            crop_plan=[{"crop": "lettuce", "area_m2": 5}, {"crop": "dragonfruit", "area_m2": 5}])


def test_nonpositive_area_in_plan_is_rejected():
    with pytest.raises(ValidationError):
        validate_design_input(
            "tilapia", None, None, 27, 5000,
            crop_plan=[{"crop": "lettuce", "area_m2": 0}])
