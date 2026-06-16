"""M2 optimizer: feasibility, beats naive baseline, respects the binding constraint."""

import pytest

from aqua_model import optimize, OptimizeInput
from aqua_model.crops import get_crop
from aqua_model.species import get_species


def _inp(**over):
    base = dict(grow_area_m2=10.0, temperature_c=28.0, water_budget_lpd=5000.0,
               objective="water_efficiency")
    base.update(over)
    return OptimizeInput(**base)


def test_returns_a_feasible_best_with_normalized_allocation():
    res = optimize(_inp())
    assert res.best is not None
    assert res.best.feasible
    # Allocation fractions sum to ~1.0.
    assert abs(sum(res.best.crop_allocation.values()) - 1.0) < 1e-6


def test_searched_and_feasible_counts_make_sense():
    res = optimize(_inp())
    assert res.searched > 0
    assert 0 < res.feasible_count <= res.searched


def test_best_is_never_worse_than_naive_even_split():
    # The even split is inside the search space, so best >= baseline by construction.
    res = optimize(_inp(objective="water_efficiency"))
    assert res.improvement_vs_baseline_pct is not None
    assert res.improvement_vs_baseline_pct >= 0.0


def test_water_efficiency_optimizer_actually_improves_on_even_split():
    # With a tomato in the palette (high FRR -> more feed -> more makeup water), an
    # all-leafy mix should give strictly better food-per-water than the even split.
    res = optimize(_inp(objective="water_efficiency"))
    assert res.improvement_vs_baseline_pct > 0.0


def test_objective_changes_the_winner_or_score():
    food = optimize(_inp(objective="food")).best
    water = optimize(_inp(objective="water_efficiency")).best
    # Different objectives should not generally yield identical full designs.
    assert (food.crop_allocation, food.fish_species) != (water.crop_allocation, water.fish_species) \
        or food.score != water.score


def test_binding_constraint_is_respected():
    res = optimize(_inp(water_budget_lpd=5000.0))
    for cand in res.ranked:
        assert cand.makeup_water_lpd <= 5000.0


def test_infeasible_budget_yields_no_best_and_no_improvement():
    res = optimize(_inp(grow_area_m2=80.0, water_budget_lpd=1.0))
    assert res.best is None
    assert res.feasible_count == 0
    assert res.improvement_vs_baseline_pct is None


def test_single_crop_palette_matches_m1_feed():
    # One crop at 100% must reproduce the M1 FRR feed (area * FRR).
    res = optimize(_inp(crop_palette=("lettuce",)))
    expected_feed = 10.0 * get_crop("lettuce").frr_g_per_m2_day
    assert res.best.feed_g_per_day == pytest.approx(expected_feed, abs=0.1)


def test_unknown_objective_raises():
    with pytest.raises(ValueError):
        optimize(_inp(objective="maximize_vibes"))


def test_honesty_layer_present():
    res = optimize(_inp())
    assert res.not_modeled and any("value" in n or "cost" in n for n in res.not_modeled)
    assert res.assumptions
