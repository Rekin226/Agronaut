"""Candidate palettes must preserve the supplied growing area and named choices."""

from dataclasses import asdict

import pytest

from aqua_model import OptimizeInput, optimize
from aqua_model.validate import ValidationError


def settings(**changes):
    return OptimizeInput(**(dict(grow_area_m2=10, temperature_c=28, water_budget_lpd=5000,
                                fish_palette=("tilapia",), crop_palette=("lettuce", "basil")) | changes))


@pytest.mark.parametrize("palette", ["fish_palette", "crop_palette"])
@pytest.mark.parametrize("value", [None, [], (), "lettuce", {}, [None], [True], ["unknown"], [""]])
def test_invalid_palette_is_rejected_before_search(monkeypatch, palette, value):
    from aqua_model import optimizer

    def unexpected(*args, **kwargs):
        raise AssertionError("invalid input reached enumeration")
    monkeypatch.setattr(optimizer, "_allocations", unexpected)
    with pytest.raises(ValidationError, match=palette):
        optimize(settings(**{palette: value}))


@pytest.mark.parametrize("palette,choices", [
    ("crop_palette", ("lettuce", "lettuce")),
    ("crop_palette", ("lettuce", " LETTUCE ")),
    ("fish_palette", ("tilapia", "tilapia")),
    ("fish_palette", ("Tilapia", " tilapia ")),
])
def test_duplicates_cannot_produce_partial_area_candidates(monkeypatch, palette, choices):
    from aqua_model import optimizer

    def unexpected(*args, **kwargs):
        raise AssertionError("duplicate palette reached enumeration")
    monkeypatch.setattr(optimizer, "_allocations", unexpected)
    with pytest.raises(ValidationError, match=f"{palette} contains duplicate"):
        optimize(settings(**{palette: choices}))


def test_normalized_list_choices_match_canonical_search_without_mutating_input():
    proposed = settings(fish_palette=[" TILAPIA "], crop_palette=["Lettuce", " BASIL "])
    original = asdict(proposed)
    result = optimize(proposed)
    canonical = optimize(settings())
    assert asdict(result) == asdict(canonical)
    assert asdict(proposed) == original
    assert result.searched == 5
    for candidate in [*result.ranked, result.baseline]:
        assert sum(candidate.crop_allocation.values()) == pytest.approx(1)
        assert set(candidate.crop_allocation) <= {"lettuce", "basil"}
        assert candidate.fish_species == "tilapia"


def test_optimizer_uses_numeric_values_returned_by_the_shared_validator():
    proposed = settings(grow_area_m2="10", temperature_c="28", water_budget_lpd="5000")
    assert asdict(optimize(proposed)) == asdict(optimize(settings()))
    assert proposed.grow_area_m2 == "10"


def test_reported_allocations_retain_the_area_used_for_even_split_calculations():
    from aqua_model.crops import get_crop

    result = optimize(settings(crop_palette=("lettuce", "basil", "tomato")))
    for candidate in [*result.ranked, result.baseline]:
        assert sum(candidate.crop_allocation.values()) == pytest.approx(1, abs=1e-12)
        feed = sum(10 * fraction * get_crop(crop).frr_g_per_m2_day
                   for crop, fraction in candidate.crop_allocation.items())
        assert candidate.feed_g_per_day == round(feed, 1)
    assert result.baseline.crop_allocation == {crop: 1 / 3 for crop in ("lettuce", "basil", "tomato")}
