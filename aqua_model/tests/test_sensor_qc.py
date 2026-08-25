"""Physical plausibility checks on sensor channels.

Every case here is drawn from real data. The dissolved-oxygen numbers are the ones that shipped in
`data/empirical_envelope.json` flagged `reliable` while sitting at 4.3x saturation; the -127 is
what a DS18B20 with no probe attached reports, observed as the MEDIAN of a live public aquaponics
channel; the 0.9994 correlation is a real `Ammonia`/`Nitrite` pair, verified against the
instrument's own API rather than a CSV export.
"""

import math

import pytest

from aqua_model import sensor_qc as qc
from aqua_model.coefficients import DO_SUPERSATURATION_TOLERANCE


# --- oxygen saturation -------------------------------------------------------

@pytest.mark.parametrize("temp_c,expected", [
    (0.0, 14.62), (10.0, 11.29), (20.0, 9.09), (25.0, 8.24), (30.0, 7.56),
])
def test_saturation_matches_published_values(temp_c, expected):
    """Benson & Krause against the standard table. Within 1% or the equation is mistyped."""
    got = qc.do_saturation_mg_l(temp_c)
    assert abs(got - expected) / expected < 0.01, f"{temp_c}C: {got:.2f} vs {expected}"


def test_saturation_falls_with_temperature():
    temps = [5, 15, 25, 35]
    vals = [qc.do_saturation_mg_l(t) for t in temps]
    assert vals == sorted(vals, reverse=True)


def test_saturation_falls_with_elevation():
    """A highland site genuinely holds less oxygen; without this it looks supersaturated."""
    assert qc.do_saturation_mg_l(20, elevation_m=2500) < qc.do_saturation_mg_l(20, elevation_m=0)


def test_saturation_rejects_absurd_temperature():
    with pytest.raises(ValueError):
        qc.do_saturation_mg_l(500)


# --- the bug this module exists for ------------------------------------------

def test_the_shipped_impossible_reading_is_caught():
    """The exact value that shipped as `reliable`: p95 of 35.55 mg/L at a median 24.5 C, which is
    4.3x saturation."""
    assert qc.implausible_do(35.55, 24.5)
    assert qc.implausible_do(41.12, 24.5)          # the recorded max, 4.9x


def test_a_real_afternoon_oxygen_peak_is_not_caught():
    """Photosynthesis genuinely supersaturates a planted bed. Rejecting that would discard real
    information, which is why the tolerance is deliberately generous."""
    sat = qc.do_saturation_mg_l(24.5)
    assert not qc.implausible_do(sat * 1.25, 24.5)
    assert not qc.implausible_do(sat * 0.75, 24.5)


def test_negative_oxygen_is_impossible():
    assert qc.implausible_do(-1.5, 24.5)


def test_tolerance_is_the_documented_coefficient():
    """The threshold is a sourced coefficient, not a literal buried in a comparison."""
    sat = qc.do_saturation_mg_l(20.0)
    tol = DO_SUPERSATURATION_TOLERANCE.value
    assert not qc.implausible_do(sat * (tol - 0.05), 20.0)
    assert qc.implausible_do(sat * (tol + 0.05), 20.0)


# --- dead-sensor sentinels ---------------------------------------------------

def test_ds18b20_disconnected_code_is_recognised():
    """-127 is stable, precise and inside any range wide enough for real extremes. It was the
    MEDIAN temperature of a live public channel."""
    assert "DS18B20" in qc.is_sentinel(-127.0)


@pytest.mark.parametrize("val", [-999.0, -9999.0, 65535.0, -32768.0])
def test_other_sentinels_are_recognised(val):
    assert qc.is_sentinel(val) is not None


def test_ordinary_readings_are_not_sentinels():
    for v in (24.5, 7.2, 0.0, 8.34, -1.0):
        assert qc.is_sentinel(v) is None


def test_sentinel_fraction_reports_the_commonest_cause():
    vals = [-127.0] * 30 + [24.0] * 70
    frac, meaning = qc.sentinel_fraction(vals)
    assert frac == pytest.approx(0.30)
    assert "DS18B20" in meaning


def test_sentinel_fraction_on_clean_data():
    frac, meaning = qc.sentinel_fraction([24.0, 24.5, 25.0])
    assert frac == 0.0 and meaning == ""


# --- per-value plausibility --------------------------------------------------

def test_ph_outside_its_own_scale():
    assert qc.implausible_value("ph", 15.78) is not None   # observed in a real dataset
    assert qc.implausible_value("ph", -0.5) is not None
    assert qc.implausible_value("ph", 7.2) is None


def test_negative_concentrations():
    assert qc.implausible_value("dissolved_oxygen_mg_l", -1.51) is not None  # real, from a
    assert qc.implausible_value("nitrite_mg_l", -3.0) is not None            # normalised dataset
    assert qc.implausible_value("ammonia_mg_l", 0.0) is None


def test_nan_and_inf_are_rejected():
    assert qc.implausible_value("tds_mg_l", float("nan")) is not None
    assert qc.implausible_value("tds_mg_l", float("inf")) is not None


def test_unknown_channel_is_not_judged():
    """No test defined means no verdict — not a pass."""
    assert qc.implausible_value("some_new_channel", -5.0) is None


# --- channel independence ----------------------------------------------------

def test_one_probe_written_to_two_fields_is_caught():
    """The real case: an Ammonia/Nitrite pair at r = 0.9994, confirmed against the instrument API."""
    a = [0.11 + i * 0.001 for i in range(500)]
    b = [x + 0.0001 for x in a]
    ok, r = qc.channels_are_independent(a, b)
    assert not ok and r > 0.99


def test_genuinely_different_channels_pass():
    """Ammonia falling while nitrite rises — the actual signature of nitrification."""
    a = [10.0 - i * 0.02 for i in range(500)]
    b = [0.1 + (i % 37) * 0.05 for i in range(500)]
    ok, _ = qc.channels_are_independent(a, b)
    assert ok


def test_too_little_overlap_makes_no_accusation():
    ok, r = qc.channels_are_independent([1, 2, 3], [1, 2, 3])
    assert ok and math.isnan(r)


def test_constant_channel_is_not_accused():
    """A flat channel has no correlation to speak of; saturation detection is its job, not this."""
    ok, r = qc.channels_are_independent([5.0] * 200, list(range(200)))
    assert ok and math.isnan(r)
