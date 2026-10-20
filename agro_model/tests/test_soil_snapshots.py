"""Exact file continuation and rejection of incompatible or malformed snapshots."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest

from agro_model.soil_replay import replay_soil_water
from agro_model.soil_snapshots import (
    MAX_SNAPSHOT_BYTES,
    dumps_soil_snapshot,
    loads_soil_snapshot,
)
from agro_model.tests.test_soil_replay import setup, supplied


def test_state_and_daily_inputs_roundtrip_exactly_with_unicode_sources_and_weather_times():
    initial = replace(setup(theta=Fraction(1, 3)), source="Synthetic Burkina Faso / 台灣")
    day = supplied(initial, initial.as_of+timedelta(days=1))
    day = replace(day, weather=replace(day.weather, kind="forecast",
        issued_at=datetime(2026, 7, 5, 12, tzinfo=timezone.utc), source_snapshot_id="local-synthetic"))
    for value, kind in ((initial, "state"), (day, "day")):
        encoded = dumps_soil_snapshot(value)
        decoded = loads_soil_snapshot(encoded, expected_kind=kind)
        assert decoded == value
        assert dumps_soil_snapshot(decoded) == encoded
    assert "台灣" in dumps_soil_snapshot(initial)


def test_saved_checkpoint_and_daily_inputs_resume_the_exact_same_calculation(tmp_path):
    initial = setup()
    days = [supplied(initial, initial.as_of+timedelta(days=i), rain=i % 5, irrigation=i % 3) for i in range(1, 21)]
    full = replay_soil_water(initial, days)
    first = replay_soil_water(initial, days[:9])
    checkpoint = tmp_path / "soil-checkpoint.json"
    checkpoint.write_text(dumps_soil_snapshot(first["ending"]), encoding="utf-8")
    restored = loads_soil_snapshot(checkpoint.read_text(encoding="utf-8"), expected_kind="state")
    remaining = [loads_soil_snapshot(dumps_soil_snapshot(d), expected_kind="day") for d in days[9:]]
    resumed = replay_soil_water(restored, remaining)
    assert resumed["ending"] == full["ending"]
    assert resumed["days"] == full["days"][9:]


@pytest.mark.parametrize("change", ["schema", "model", "missing_model", "kind", "root_type", "extra_field", "missing_field", "unknown_record"])
def test_unknown_versions_kinds_and_record_shapes_are_rejected(change):
    envelope = json.loads(dumps_soil_snapshot(setup()))
    if change == "schema":
        envelope["schema_version"] = "future-version"
    elif change == "model":
        envelope["model_versions"]["soil_roots"] = "different-root-equations"
    elif change == "missing_model":
        del envelope["model_versions"]["soil_roots"]
    elif change == "kind":
        envelope["kind"] = "day"
    elif change == "root_type":
        envelope["value"] = "SoilWaterState"
    elif change == "extra_field":
        envelope["value"]["fields"]["arbitrary"] = "not accepted"
    elif change == "missing_field":
        del envelope["value"]["fields"]["source"]
    else:
        envelope["value"]["record"] = "SomeArbitraryPythonClass"
    with pytest.raises(ValueError):
        loads_soil_snapshot(json.dumps(envelope), expected_kind="state")


def test_physical_validation_runs_again_when_state_is_loaded():
    envelope = json.loads(dumps_soil_snapshot(setup()))
    field = envelope["value"]["fields"]
    cell = field["soil"]["fields"]["patches"]["tuple"][0]["fields"]["compartments"]["tuple"][0]
    cell["fields"]["water_content"] = {"fraction": [9, 10]}
    with pytest.raises(ValueError, match="water content"):
        loads_soil_snapshot(json.dumps(envelope), expected_kind="state")


@pytest.mark.parametrize("pair", [[1, 0], [1, -1], [True, 1], [1.5, 2], [1], "1/2"])
def test_invalid_fraction_encodings_are_rejected(pair):
    envelope = json.loads(dumps_soil_snapshot(setup()))
    envelope["value"]["fields"]["soil"]["fields"]["area_m2"] = {"fraction": pair}
    with pytest.raises(ValueError):
        loads_soil_snapshot(json.dumps(envelope), expected_kind="state")


@pytest.mark.parametrize("payload", ["{", "null", "[]", '{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}'])
def test_invalid_json_duplicate_keys_and_nonfinite_numbers_are_rejected(payload):
    with pytest.raises(ValueError):
        loads_soil_snapshot(payload, expected_kind="state")


def test_wrong_expected_kind_and_oversized_or_unsupported_values_are_rejected():
    snapshot = dumps_soil_snapshot(setup())
    for expected in (None, [], "unknown", "day"):
        with pytest.raises(ValueError):
            loads_soil_snapshot(snapshot, expected_kind=expected)
    with pytest.raises(ValueError):
        loads_soil_snapshot(" "*(MAX_SNAPSHOT_BYTES+1), expected_kind="state")
    with pytest.raises(ValueError):
        dumps_soil_snapshot({"state": setup()})
