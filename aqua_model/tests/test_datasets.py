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
