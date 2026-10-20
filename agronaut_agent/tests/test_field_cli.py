import copy
import json
from pathlib import Path

import pytest

from agronaut_agent import cli
from agronaut_agent.field_io import (
    evaluate_field_request,
    import_weather_csv,
    parse_weather_records,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/field_water.json"


def request():
    return json.loads(EXAMPLE.read_text())


def test_offline_demand_and_irrigation_cli(tmp_path, capsys):
    for cmd in ("water-needs", "irrigation-plan"):
        assert cli.main([cmd, "--input", str(EXAMPLE), "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["evidence_status"] == "experimental"
        assert result["site"]["timezone"] == "Europe/Brussels"
        assert result["days"][0]["demand"]["values"]["etc_mm_day"] > 0
        if cmd == "irrigation-plan":
            assert result["days"][0]["requirement"]["values"]["gross_irrigation_l"] > 0
            assert result["days"][0]["weather_kind"] == "scenario"


def test_cli_reports_errors_with_nonzero_status(tmp_path, capsys):
    invalid = request()
    invalid["area_m2"] = float("nan")
    p = tmp_path / "invalid.json"
    p.write_text(json.dumps(invalid))
    assert cli.main(["water-needs", "--input", str(p), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "VALIDATION_FAILED"
    assert "finite" in error["error"] or "NaN" in error["error"]


def test_unknown_state_reports_missing_inputs_without_inventing_schedule():
    payload = request()
    payload["initial_state"] = None
    result = evaluate_field_request(payload, "irrigation-plan")
    assert result["days"][0]["requirement"]["missing_inputs"] == ["soil_state"]
    assert result["days"][0]["requirement"]["values"]["gross_irrigation_mm"] is None
    assert result["days"][0]["balance"] is None


@pytest.mark.parametrize("change", ["gap", "duplicate", "unsorted", "unknown_field"])
def test_weather_import_rejects_ambiguous_series(change):
    records = request()["weather"]
    second = copy.deepcopy(records[0])
    second["day"] = "2026-07-07"
    records.append(second)
    if change == "gap":
        second["day"] = "2026-07-08"
    elif change == "duplicate":
        second["day"] = records[0]["day"]
    elif change == "unsorted":
        records.reverse()
    else:
        records[0]["solar_radiation_mj_m2"] = 25
    with pytest.raises(ValueError):
        parse_weather_records(records)


def test_plan_requires_explicit_water_event_for_each_day():
    payload = request()
    payload["events"] = []
    with pytest.raises(ValueError, match="event"):
        evaluate_field_request(payload, "irrigation-plan")


def test_duplicates_and_out_of_series_events_rejected():
    payload = request()
    payload["events"].append(copy.deepcopy(payload["events"][0]))
    with pytest.raises(ValueError, match="event"):
        evaluate_field_request(payload, "irrigation-plan")
    payload = request()
    payload["events"][0]["day"] = "2026-07-08"
    with pytest.raises(ValueError, match="event"):
        evaluate_field_request(payload, "irrigation-plan")


def test_corrected_rainfall_replays_from_initial_state():
    payload = request()
    dry = evaluate_field_request(payload, "irrigation-plan")
    payload["weather"][0]["rainfall_mm"] = 80
    wet = evaluate_field_request(payload, "irrigation-plan")
    assert wet["days"][0]["balance"]["values"]["depletion_mm"] < (
        dry["days"][0]["balance"]["values"]["depletion_mm"])
    assert wet == evaluate_field_request(payload, "irrigation-plan")


def test_capabilities_command_does_not_claim_field_validation(capsys):
    assert cli.main(["field-capabilities", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["soil"]["crop_water"]["status"] == "experimental"
    assert result["soil"]["surface_cutoff"]["status"] == "not_implemented"


def test_csv_blank_rain_is_not_coerced_to_zero(tmp_path):
    import csv
    record = request()["weather"][0]
    record["rainfall_mm"] = ""
    path = tmp_path / "weather.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=record.keys())
        writer.writeheader()
        writer.writerow(record)
    imported = import_weather_csv(path)
    assert imported[0].rainfall_mm is None
    assert imported[0].t_min_c == 12.3


def test_duplicate_json_key_is_rejected(tmp_path, capsys):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":2}')
    assert cli.main(["water-needs", "--input", str(path), "--json"]) == 2
    assert "duplicate" in json.loads(capsys.readouterr().out)["error"]
