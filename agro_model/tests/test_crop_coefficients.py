"""Independent endpoint arithmetic and calendar-boundary checks for supplied Kc."""

from datetime import date, timedelta

import pytest

from agro_model.crop_coefficients import CoefficientPeriod, CoefficientSchedule


def period(**changes):
    data = dict(start=date(2026, 7, 1), end=date(2026, 7, 4), start_value=0.5, end_value=1.1,
                stage="Synthetic development", source="Synthetic explicit endpoint assumptions")
    return CoefficientPeriod(**dict(data, **changes))


def test_interpolation_is_anchored_to_inclusive_dates():
    p = period()
    assert [p.at(date(2026, 7, d)).value for d in range(1, 5)] == pytest.approx([0.5, 0.7, 0.9, 1.1])
    assert p.at(p.start).source == p.source
    falling = period(start_value=1.1, end_value=0.5)
    assert [falling.at(date(2026, 7, d)).value for d in range(1, 5)] == pytest.approx([1.1, 0.9, 0.7, 0.5])
    constant = period(start_value=0.8, end_value=0.8)
    assert [constant.at(date(2026, 7, d)).value for d in range(1, 5)] == pytest.approx([0.8] * 4)


def test_calendar_days_include_leap_day_and_do_not_use_elapsed_hours():
    p = period(start=date(2028, 2, 28), end=date(2028, 3, 1))
    assert p.at(date(2028, 2, 29)).value == pytest.approx(0.8)
    spring = period(start=date(2026, 3, 28), end=date(2026, 3, 30))
    assert spring.at(date(2026, 3, 29)).value == pytest.approx(0.8)


def test_constant_upper_bound_stays_exact_through_a_full_year():
    p = period(start=date(2026, 1, 1), end=date(2026, 12, 31), start_value=3, end_value=3)
    assert all(p.at(p.start + timedelta(days=i)).value == 3 for i in range(365))


def test_consecutive_periods_keep_distinct_stage_and_source():
    first = period()
    second = period(start=date(2026, 7, 5), end=date(2026, 7, 5), start_value=1.2, end_value=1.2,
                    stage="Synthetic mid-season", source="Other supplied basis")
    schedule = CoefficientSchedule((first, second))
    coefficient, selected = schedule.at(date(2026, 7, 5))
    assert coefficient.value == 1.2 and selected == second
    assert coefficient.stage == second.stage and coefficient.source == second.source
    for day in (date(2026, 6, 30), date(2026, 7, 6)):
        with pytest.raises(ValueError, match="extrapolation"):
            schedule.at(day)


@pytest.mark.parametrize("changes", [dict(start_value=True), dict(end_value=float("nan")), dict(start_value=-0.1),
    dict(end_value=3.1), dict(source=""), dict(stage=""), dict(end=date(2026, 6, 30)),
    dict(end=date(2026, 7, 1)), dict(start="2026-07-01")])
def test_periods_require_valid_values_dates_and_sources(changes):
    with pytest.raises(ValueError):
        period(**changes)


@pytest.mark.parametrize("start", [date(2026, 7, 1), date(2026, 7, 4), date(2026, 7, 6)])
def test_duplicates_overlap_and_gaps_fail(start):
    with pytest.raises(ValueError, match="consecutive"):
        CoefficientSchedule((period(), period(start=start, end=date(2026, 7, 8))))
