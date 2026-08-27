"""Validate the open-dataset ingestion and the envelope cross-check against real ponds.

Two tiers, mirroring the trust philosophy:
  * Artifact tests always run — they read the committed data/empirical_envelope.json, so CI
    needs no 20 MB download.
  * Raw-data tests skip unless the pond CSVs have been fetched.
"""

import pytest

from aqua_model import datasets as D
from aqua_model import size_system
from aqua_model.validate import validate_design_input

ARTIFACT = D.load_artifact() if D.ARTIFACT.exists() else None


# ---------------------------------------------------------------- artifact tier

@pytest.mark.skipif(ARTIFACT is None, reason="empirical_envelope.json not generated yet")
def test_artifact_provenance_and_size():
    assert "Udanor" in ARTIFACT["source"]
    assert ARTIFACT["license"] == "CC BY 4.0"
    assert ARTIFACT["n_ponds"] == 4
    assert ARTIFACT["n_readings"] > 200_000   # ~233k real readings


@pytest.mark.skipif(ARTIFACT is None, reason="empirical_envelope.json not generated yet")
def test_saturated_channels_flagged_low_trust():
    # Turbidity and ammonia sit pinned at their sensor rail — must NOT be sold as reliable.
    channels = ARTIFACT["channels"]
    assert channels["turbidity_ntu"]["trust"].startswith("low")
    assert channels["ammonia_mg_l"]["trust"].startswith("low")


@pytest.mark.skipif(ARTIFACT is None, reason="empirical_envelope.json not generated yet")
def test_reliable_channels_marked_reliable():
    channels = ARTIFACT["channels"]
    assert channels["water_temp_c"]["trust"] == "reliable"
    assert channels["ph"]["trust"] == "reliable"


@pytest.mark.skipif(ARTIFACT is None, reason="empirical_envelope.json not generated yet")
def test_real_temperature_is_physically_plausible():
    temp = ARTIFACT["channels"]["water_temp_c"]
    assert 20.0 <= temp["p50"] <= 30.0           # warm freshwater pond
    assert temp["min"] >= 0.0 and temp["max"] <= 45.0   # within logging_schema bounds


# ------------------------------------------------------------- envelope cross-check

@pytest.mark.skipif(not D.available(), reason="raw pond CSVs not fetched")
def test_envelope_crosscheck_reports_known_tensions():
    # Real ponds ran cooler than the tilapia optimum and more alkaline than the leafy-crop
    # ceiling. The cross-check must surface both rather than silently 'pass'.
    di = validate_design_input(
        fish_species="tilapia", crop="lettuce",
        grow_area_m2=6.0, temperature_c=26.0, water_budget_lpd=500.0,
    )
    env = size_system(di).operating_envelope
    checks = D.compare_to_model_envelope(env)

    temp = checks["water_temp_c"]
    assert temp["median_position"] == "below target band"     # 24.5 C < 27 C optimum
    assert temp["frac_in_do_not_exceed"] > 0.95               # but safely within survival band

    ph = checks["ph"]
    assert ph["median_position"] == "above target band"        # 7.33 > 7.0 leafy ceiling


@pytest.mark.skipif(not D.available(), reason="raw pond CSVs not fetched")
def test_loader_uses_canonical_schema_names():
    df = D.load_all()
    assert {"water_temp_c", "ph", "ammonia_mg_l", "nitrate_mg_l", "pond"} <= set(df.columns)
    assert df["pond"].nunique() == 4


# --- trust is a verdict, not a fallback --------------------------------------

def test_dissolved_oxygen_is_no_longer_flagged_reliable():
    """The bug this fixes. The shipped envelope marked DO `reliable` while its p95 sat at 4.3x
    oxygen saturation, making it eligible to calibrate coefficients against."""
    from aqua_model import datasets
    env = datasets.load_artifact()["channels"]
    assert env["dissolved_oxygen_mg_l"]["trust"] == "low (physically implausible)"
    assert env["dissolved_oxygen_mg_l"]["do_saturation_at_median_temp"] < 10


def test_saturated_channels_still_caught():
    """The check that already existed must not regress: turbidity and ammonia sit on their rails."""
    from aqua_model import datasets
    env = datasets.load_artifact()["channels"]
    assert env["turbidity_ntu"]["trust"].startswith("low")
    assert env["ammonia_mg_l"]["trust"].startswith("low")


def test_clean_channels_still_earn_reliable():
    """The verdict has to stay usable — over-flagging would be its own failure."""
    from aqua_model import datasets
    env = datasets.load_artifact()["channels"]
    assert env["water_temp_c"]["trust"] == "reliable"
    assert env["ph"]["trust"] == "reliable"


def test_unknown_channel_reports_unassessed_not_reliable():
    """The heart of the bug: `reliable` used to be the else-branch, so any channel nobody wrote a
    check for was silently promoted. An unchecked channel must say so."""
    import pandas as pd

    from aqua_model import datasets
    # Varied values on purpose: a fixture with few distinct values trips the saturation check
    # first and never reaches the question being asked.
    s = pd.Series([i * 0.37 for i in range(200)])
    verdict, _ = datasets._trust_verdict("a_channel_with_no_check", s, None)
    assert verdict == "unassessed"


def test_dead_sensor_sentinels_outrank_other_verdicts():
    """A channel whose median is -127 is a disconnected probe, and that is the useful thing to
    report — not that it happens to also look saturated."""
    import pandas as pd

    from aqua_model import datasets
    s = pd.Series([-127.0] * 60 + [24.0] * 40)
    verdict, evidence = datasets._trust_verdict("water_temp_c", s, None)
    assert verdict == "low (dead-sensor sentinel)"
    assert "DS18B20" in evidence["sentinel"]


def test_non_independent_channels_are_demoted():
    """Two channels that are one instrument must both lose their `reliable` verdict, even though
    each looks fine measured alone."""
    import pandas as pd

    from aqua_model import datasets
    n = 400
    a = [0.11 + i * 0.001 for i in range(n)]
    df = pd.DataFrame({
        "water_temp_c": [24.5] * n,
        "ammonia_mg_l": a,
        "nitrite_mg_l": [x + 0.0001 for x in a],
    })
    env = datasets.empirical_envelope(df)
    assert env["ammonia_mg_l"]["trust"] == "low (not an independent instrument)"
    assert env["nitrite_mg_l"]["trust"] == "low (not an independent instrument)"
    assert env["ammonia_mg_l"]["correlated_with"]["r"] > 0.99
