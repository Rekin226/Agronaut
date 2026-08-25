"""The business case: joins cost to harvest, and never flatters the result."""

import pytest

from aqua_model.business import NOT_INCLUDED, build_case, format_case
from aqua_model.costing import estimate_cost
from aqua_model.layout import plan_layout
from aqua_model.production import ProductionSummary
from aqua_model.sizing import size_system
from aqua_model.tests.test_costing import _BOOK as _COST_BOOK
from aqua_model.validate import validate_design_input

_BOOK = {
    "regions": {
        "testland": {
            **_COST_BOOK["regions"]["testland"],
            "revenue_items": {
                "fish_tilapia": {"price": 3.0, "low": 2.5, "high": 3.6, "source": "test"},
                "fish_generic": {"price": 2.5, "source": "test"},
                "crop_basil": {"price": 6.0, "low": 4.0, "high": 9.0, "source": "test"},
                "crop_leafy": {"price": 1.5, "source": "test"},
            },
        }
    }
}


def _summary(days=365, fish=120.0, crop=400.0, limiting="light"):
    return ProductionSummary(
        days=days, fish_harvested_kg=fish, fish_standing_kg=0.0, crop_harvested_kg=crop,
        feed_used_kg=200.0, realized_fcr=1.6, heat_deficit_c_days=0.0,
        water_temp_min_c=22.0, water_temp_max_c=30.0, peak_tan_mg_l=0.4,
        peak_no2_mg_l=0.5, peak_no3_mg_l=80.0, mean_f_light=0.9, mean_f_temp=0.8,
        mean_f_nitrogen=0.7, limiting_factor=limiting, warnings=())


def _cost():
    out = size_system(validate_design_input("tilapia", "basil", 24.0, 27.0, 500.0))
    return out, estimate_cost(out, plan_layout(out), _BOOK, "testland")


def _case(summary=None, **kw):
    out, cost = _cost()
    return build_case(summary or _summary(), cost, _BOOK, "testland",
                      crop_key="basil", species_key="tilapia", **kw)


def test_a_profitable_system_reports_margin_and_payback():
    case = _case()
    assert case.margin_per_year()[1] > 0
    assert case.payback_years() and case.payback_years() > 0
    assert "payback" in case.verdict.lower()


def test_a_losing_system_is_called_a_loss_not_softened():
    case = _case(_summary(fish=1.0, crop=1.0))
    assert case.margin_per_year()[1] < 0
    assert case.payback_years() is None, "a loss has no payback period"
    assert "does not clear" in case.verdict
    assert "hobby" in case.verdict or "food-security" in case.verdict


def test_labour_is_excluded_by_default_and_said_so():
    case = _case()
    assert case.labour_cost_per_year is None
    assert "NOT included" in case.labour_note
    assert any("labour" in x for x in NOT_INCLUDED)


def test_labour_can_flip_the_verdict_and_the_case_says_it():
    book = {"regions": {"testland": {**_BOOK["regions"]["testland"]}}}
    book["regions"]["testland"]["items"] = {
        **book["regions"]["testland"]["items"],
        "labour_day": {"price": 40.0, "source": "test"},
    }
    out, cost = _cost()
    case = build_case(_summary(), cost, book, "testland", crop_key="basil",
                      species_key="tilapia", labour_hours_per_week=20.0)
    assert case.labour_cost_per_year and case.labour_cost_per_year > 0
    assert case.margin_per_year(with_labour=True)[1] < case.margin_per_year()[1]
    assert "labour" in case.verdict.lower()


def test_the_low_margin_pairs_low_revenue_with_high_cost():
    """An operator plans against the bad case, not an average of unrelated extremes."""
    case = _case()
    lo, mid, hi = case.margin_per_year()
    assert lo < mid < hi
    assert lo == pytest.approx(case.revenue_total()[0] - case.opex_per_year[2])


def test_an_unpriced_crop_makes_the_case_say_the_number_is_incomplete():
    book = {"regions": {"testland": {**_BOOK["regions"]["testland"],
                                     "revenue_items": {"fish_tilapia": {"price": 3.0,
                                                                        "source": "t"}}}}}
    out, cost = _cost()
    case = build_case(_summary(), cost, book, "testland", crop_key="basil",
                      species_key="tilapia")
    assert case.unpriced_revenue
    assert any("INCOMPLETE" in f and "higher" in f for f in case.findings)


def test_a_missing_species_price_falls_back_and_names_the_fallback():
    out, cost = _cost()
    case = build_case(_summary(), cost, _BOOK, "testland", crop_key="basil",
                      species_key="clarias")
    assert any("generic fish rate" in f for f in case.findings)


def test_a_partial_season_is_scaled_and_the_scaling_is_disclosed():
    case = _case(_summary(days=180, fish=60.0, crop=200.0))
    assert any("scaled" in f for f in case.findings)


def test_the_limiting_factor_becomes_advice():
    case = _case(_summary(limiting="light"))
    assert any("limited by light" in f for f in case.findings)


def test_the_report_states_what_it_is_not():
    text = format_case(_case())
    assert "Not included" in text
    assert "not a forecast" in text
    assert "farm-gate" in text
