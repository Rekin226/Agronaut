"""Root-zone conversion, explicit timing and bounded state corrections."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from agro_model.soil_state import SoilStateUpdate, reconcile_soil_state
from agro_model.water import Soil, SoilState


def update(**changes):
    data = dict(as_of=date(2026, 7, 6), observed_at=datetime(2026, 7, 6, 18, tzinfo=timezone.utc),
        timezone="UTC", quantity="soil_water_content", value=0.25, unit="m3/m3", represented_depth_m=0.8,
        representation_note="Synthetic whole-depth/field mean", depletion_adjustment_mm=2,
        adjustment_source="Supplied 2 mm net depletion before midnight", source="Synthetic sample",
        provenance="measured", quality="unverified")
    data.update(changes)
    return SoilStateUpdate(**data)


def test_conversion_and_reconciliation_are_distinct_from_water_fluxes():
    soil = Soil(0.32, 0.15, 0.8, 0.4, "Synthetic soil")
    result = reconcile_soil_state(soil, SoilState(date(2026, 7, 6), 60, "Prior model"), update())
    # Independent arithmetic: (.32 - .25) * .8 * 1000 = 56 mm;
    # 2 mm timing correction yields 58; compared with 60 the reconciliation is -2.
    assert result.values == pytest.approx(dict(reading_basis_depletion_mm=56, timing_adjustment_mm=2,
        adopted_depletion_mm=58, prior_model_depletion_mm=60, reconciliation_mm=-2))
    assert set(result.units.values()) == {"mm"}
    assert result.inputs["update"]["quality"] == "unverified"
    assert "https://www.fao.org/4/X0490E/x0490e0e.htm" in result.sources


@pytest.mark.parametrize("changes", [dict(value=True), dict(value=-1), dict(value=1.1), dict(unit="%"),
    dict(depletion_adjustment_mm=float("nan")), dict(represented_depth_m=0), dict(representation_note=""),
    dict(adjustment_source=""), dict(quality="rejected"), dict(provenance="forecast"),
    dict(observed_at=datetime(2026, 7, 6)), dict(timezone="unknown"), dict(as_of=date.max),
    dict(observed_at=datetime(2026, 7, 7, 0, 0, 1, tzinfo=timezone.utc))])
def test_invalid_or_unqualified_readings_fail(changes):
    with pytest.raises(ValueError):
        update(**changes)


def test_no_clipping_and_no_implicit_depth_conversion():
    soil = Soil(0.32, 0.15, 0.8, 0.4, "Synthetic soil")
    for reading in (update(value=0.1), update(value=0.4), update(represented_depth_m=0.2)):
        with pytest.raises(ValueError):
            reconcile_soil_state(soil, None, reading)
    # The adjusted state is what must be bounded, not a falsely clipped raw reading.
    result = reconcile_soil_state(soil, None, update(value=0.33, depletion_adjustment_mm=10))
    assert result.values["adopted_depletion_mm"] == pytest.approx(2)
    assert result.values["prior_model_depletion_mm"] is None
    assert result.values["reconciliation_mm"] is None
    with pytest.raises(ValueError, match="same end-of-day"):
        reconcile_soil_state(soil, SoilState(date(2026, 7, 5), 60, "Old"), update())


@pytest.mark.parametrize("zone,day,stamp,closing", [
    ("Asia/Kathmandu", "2026-07-06", "2026-07-06T18:15:00+00:00", "2026-07-06T18:15:00+00:00"),
    ("Europe/Berlin", "2026-03-29", "2026-03-29T21:59:00+00:00", "2026-03-29T22:00:00+00:00"),
    ("Europe/Berlin", "2026-10-25", "2026-10-25T01:30:00+00:00", "2026-10-25T23:00:00+00:00"),
])
def test_explicit_local_boundaries_include_dst_and_fractional_offsets(zone, day, stamp, closing):
    reading = update(timezone=zone, as_of=date.fromisoformat(day), observed_at=datetime.fromisoformat(stamp))
    assert reading.effective_at == datetime.fromisoformat(closing)
    with pytest.raises(ValueError):
        replace(reading, observed_at=datetime.fromisoformat(closing).replace(second=1))


def test_skipped_local_date_is_not_a_valid_boundary():
    with pytest.raises(ValueError):
        update(timezone="Pacific/Apia", as_of=date(2011, 12, 30),
               observed_at=datetime(2011, 12, 30, 10, tzinfo=timezone.utc))
