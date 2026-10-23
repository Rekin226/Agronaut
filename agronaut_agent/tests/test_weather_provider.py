"""Synthetic provider fixtures: conservation, calendar boundaries and provenance."""

import copy
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import pytest

from agro_model.water import CropCoefficient, crop_water_demand
from agronaut_agent import weather_provider as provider
from agronaut_agent.field_io import (
    evaluate_field_request,
    import_weather_csv,
    parse_weather_records,
    render_field_report,
)
from agronaut_agent.tests.test_field_workflow import inputs, setup  # noqa: F401


def weather_request(day="2026-07-06", zone="UTC", **changes):
    return dict(latitude=5.6, longitude=-0.2, timezone=zone, start=day, end=day, **changes)


def response(first="2026-07-06T01:00:00+00:00", count=24):
    stamp = int(datetime.fromisoformat(first).timestamp())
    return dict(latitude=5.625, longitude=-0.25, elevation=30.0, timezone="GMT", utc_offset_seconds=0,
                generationtime_ms=0.7,
                hourly_units=dict(time="unixtime", et0_fao_evapotranspiration="mm", precipitation="mm", snowfall="cm"),
                hourly=dict(time=[stamp + i * 3600 for i in range(count)],
                            et0_fao_evapotranspiration=[0.25] * count,
                            precipitation=[0.5] * count, snowfall=[0] * count))


def bundle(retrieved="2026-07-05T12:00:00+00:00"):
    return provider.adapt_response(weather_request(), response(), retrieved)


def test_et0_input_has_no_fabricated_meteorology_and_keeps_exact_original():
    result = bundle()
    row = result["weather"][0]
    assert row["et0_mm_day"] == 6 and row["rainfall_mm"] == 12
    assert row["kind"] == "forecast" and "issued_at" not in row
    assert "net_radiation_mj_m2" not in row
    parsed = parse_weather_records(result["weather"])[0]
    demand = crop_water_demand(parsed, CropCoefficient(1.2, "synthetic", "mid"), 100)
    assert demand.values["crop_demand_l_day"] == pytest.approx(720)
    assert demand.coefficients.keys() == {"kc"}
    data = inputs()
    data.update({k: result[k] for k in ("weather", "weather_sources")})
    output = evaluate_field_request(data, "irrigation-plan")
    assert output["days"][0]["balance"]["values"]["depletion_mm"] >= 0
    assert data["weather_sources"][0]["response"] == response()
    assert "uniformly within each hour" in render_field_report(output)
    assert bundle("2026-07-07T12:00:00+00:00")["weather"][0]["kind"] == "historical"


@pytest.mark.parametrize("day,first,hours", [
    ("2026-03-29", "2026-03-29T00:00:00+00:00", 23),
    ("2026-10-25", "2026-10-24T23:00:00+00:00", 25),
])
def test_dst_uses_actual_elapsed_hours(day, first, hours):
    result = provider.adapt_response(weather_request(day, "Europe/Berlin"), response(first, hours),
                                     "2026-01-01T00:00:00+00:00")
    row = result["weather"][0]
    assert row["period_hours"] == hours
    assert row["et0_mm_day"] == hours * 0.25
    assert row["rainfall_mm"] == hours * 0.5
    assert parse_weather_records(result["weather"])[0].period_hours == hours


def test_fractional_zone_prorates_boundary_hours_and_conserves_two_days():
    req = weather_request("2026-07-06", "Asia/Kathmandu")
    req["end"] = "2026-07-07"
    raw = response("2026-07-05T19:00:00+00:00", 49)
    raw["hourly"]["precipitation"] = list(range(49))
    result = provider.adapt_response(req, raw, "2026-07-05T00:00:00+00:00")
    first, last = result["weather"]
    assert first["rainfall_mm"] == sum(range(1, 24)) + 24 * 0.25
    assert last["rainfall_mm"] == 24 * 0.75 + sum(range(25, 48)) + 48 * 0.25
    assert first["rainfall_mm"] + last["rainfall_mm"] == sum(range(1, 48)) + 48 * 0.25
    assert first["et0_mm_day"] == last["et0_mm_day"] == 6


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "reorder", "null", "negative", "nan", "bool",
                                       "units", "snow", "array", "offset", "shortwave"])
def test_provider_rejects_missing_or_ambiguous_data(corruption):
    raw = response()
    if corruption == "missing":
        raw["hourly"]["time"].pop()
    elif corruption == "duplicate":
        raw["hourly"]["time"][1] = raw["hourly"]["time"][0]
    elif corruption == "reorder":
        raw["hourly"]["time"].reverse()
    elif corruption in ("null", "negative", "nan", "bool"):
        raw["hourly"]["precipitation"][0] = {"null": None, "negative": -1, "nan": float("nan"), "bool": True}[corruption]
    elif corruption == "units":
        raw["hourly_units"]["et0_fao_evapotranspiration"] = "MJ/m²"
    elif corruption == "snow":
        raw["hourly"]["snowfall"][0] = 0.1
    elif corruption == "array":
        raw["hourly"]["precipitation"].pop()
    elif corruption == "offset":
        raw["utc_offset_seconds"] = 3600
    else:
        raw["hourly"]["shortwave_radiation"] = raw["hourly"].pop("et0_fao_evapotranspiration")
    with pytest.raises(ValueError):
        provider.adapt_response(weather_request(), raw, "2026-07-05T12:00:00+00:00")


@pytest.mark.parametrize("change", ["hash", "record", "missing", "unused", "timezone", "duration", "surface"])
def test_saved_snapshot_and_import_contract_are_verified(change):
    b = bundle()
    data = inputs()
    data.update({k: b[k] for k in ("weather", "weather_sources")})
    if change == "hash":
        data["weather_sources"][0]["response"]["latitude"] = 1
    elif change == "record":
        data["weather"][0]["rainfall_mm"] = 0
    elif change == "missing":
        data["weather_sources"] = []
    elif change == "unused":
        del data["weather"][0]["source_snapshot_id"]
    elif change == "timezone":
        data["site"]["timezone"] = "Africa/Accra"
    elif change == "duration":
        data["weather"][0]["period_hours"] = 23
    else:
        data["weather"][0]["reference_surface"] = "alfalfa"
    with pytest.raises(ValueError):
        evaluate_field_request(data, "water-needs")


def test_scoped_fetch_save_and_revision_are_immutable_and_do_not_create_actuals(request, monkeypatch):
    workflow, _ = request.getfixturevalue("setup")
    calls = []
    def fetch(req):
        calls.append(req)
        return bundle()
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    with pytest.raises(ValueError):
        workflow.fetch_weather("other", "field", 5.6, -0.2, "2026-07-06", "2026-07-06")
    assert not calls
    weather = workflow.fetch_weather("owner", "field", 5.6, -0.2, "2026-07-06", "2026-07-06")
    assert calls[0]["timezone"] == "UTC"
    assert workflow.list("owner", "field") == dict(calculations=[], water_days=[])
    data = inputs()
    data.update({k: weather[k] for k in ("weather", "weather_sources")})
    parent = workflow.save("owner", "field", "root_zone_budget", data, request_id="save-weather", cycle_id="cycle")
    next_bundle = bundle("2026-07-05T15:00:00+00:00")
    revised = workflow.revise_weather("owner", "field", parent["id"], next_bundle["weather"],
                                      weather_sources=next_bundle["weather_sources"], request_id="revise-weather")
    assert revised["inputs"]["weather_sources"] == next_bundle["weather_sources"]
    assert workflow.get("owner", "field", parent["id"]) == parent
    assert workflow.list("owner", "field")["water_days"] == []
    assert workflow.revise_weather("owner", "field", parent["id"], next_bundle["weather"],
            weather_sources=next_bundle["weather_sources"], request_id="revise-weather") == revised
    with pytest.raises(ValueError, match="newer retrieved_at"):
        workflow.revise_weather("owner", "field", revised["id"], weather["weather"],
                                weather_sources=weather["weather_sources"], request_id="stale")
    mixed = copy.deepcopy(next_bundle["weather"])
    mixed[0]["issued_at"] = "2026-07-05T16:00:00+00:00"
    with pytest.raises(ValueError, match="same timestamp basis"):
        workflow.revise_weather("owner", "field", revised["id"], mixed, request_id="mixed")


def test_fetch_requests_utc_hour_ends_and_does_not_invent_issue_time(monkeypatch):
    day = datetime.now(timezone.utc).date()
    req = weather_request(day.isoformat())
    captured = []
    def download(params):
        captured.append(params)
        return response(f"{day}T01:00:00+00:00")
    monkeypatch.setattr(provider, "_download", download)
    result = provider.fetch_weather(req)
    assert captured[0]["start_hour"] == f"{day}T01:00"
    assert captured[0]["end_hour"] == f"{day + timedelta(days=1)}T00:00"
    assert captured[0]["timeformat"] == "unixtime" and captured[0]["timezone"] == "GMT"
    assert "issued_at" not in result["weather"][0]


@pytest.mark.parametrize("error", [HTTPError("secret-url", 429, "secret", {}, None), URLError("secret-key")])
def test_network_errors_are_actionable_and_do_not_expose_credentials(monkeypatch, error):
    class Opener:
        def open(self, request, timeout):
            assert timeout == 20
            raise error
    monkeypatch.setattr(provider, "build_opener", lambda *args: Opener())
    monkeypatch.setenv("OPEN_METEO_API_KEY", "private-secret")
    with pytest.raises(ValueError) as caught:
        provider._download({})
    assert "secret" not in str(caught.value)


def test_transport_rejects_redirect_duplicate_keys_and_oversized_json(monkeypatch):
    with pytest.raises(ValueError, match="redirect"):
        provider._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")
    class Body:
        payload = b'{"a":1,"a":2}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, maximum):
            assert maximum == provider.MAX_BYTES + 1
            return self.payload
    class Opener:
        def open(self, request, timeout):
            return Body()
    monkeypatch.setattr(provider, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="invalid JSON"):
        provider._download({})
    Body.payload = b" " * (provider.MAX_BYTES + 1)
    with pytest.raises(ValueError, match="size limit"):
        provider._download({})
    Body.payload = json.dumps(response()).encode()
    assert provider._download({}) == response()


@pytest.mark.parametrize("changes", [dict(latitude=True), dict(longitude=181), dict(timezone="Missing/Zone"),
    dict(start="2026-07-07", end="2026-07-06"), dict(end="2026-08-06"), dict(start="20260706")])
def test_invalid_fetch_parameters_never_reach_the_network(monkeypatch, changes):
    def unexpected(params):
        raise AssertionError("invalid request reached the network")
    monkeypatch.setattr(provider, "_download", unexpected)
    req = weather_request()
    req.update(changes)
    with pytest.raises(ValueError):
        provider.fetch_weather(req)


def test_offline_supplied_reference_et_csv_remains_available(tmp_path):
    import csv
    row = bundle()["weather"][0]
    del row["source_snapshot_id"]
    row.update(kind="scenario", source="Synthetic daily ET fixture", method="Declared supplied grass-reference ET")
    path = tmp_path / "et0.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    parsed = import_weather_csv(path)[0]
    assert parsed.et0_mm_day == 6 and parsed.rainfall_mm == 12
    invalid = dict(row, retrieved_at="2026-07-05T12:00:00")
    with pytest.raises(ValueError, match="timezone"):
        parse_weather_records([invalid])


def test_partial_revision_retains_both_snapshots_and_original_days(request):
    workflow, _ = request.getfixturevalue("setup")
    req = weather_request()
    req["end"] = "2026-07-07"
    original = provider.adapt_response(req, response(count=48), "2026-07-05T12:00:00+00:00")
    data = inputs()
    data.update({k: original[k] for k in ("weather", "weather_sources")})
    parent = workflow.save("owner", "field", "crop_water", data, request_id="two-days")
    latest = bundle("2026-07-05T16:00:00+00:00")
    child = workflow.revise_weather("owner", "field", parent["id"], latest["weather"],
        weather_sources=latest["weather_sources"], request_id="partial")
    assert len(child["inputs"]["weather_sources"]) == 2
    assert child["inputs"]["weather"][1] == parent["inputs"]["weather"][1]
    assert child["inputs"]["weather"][0] == latest["weather"][0]


def test_cli_fetches_for_owned_unit_without_saving(tmp_path, monkeypatch, capsys):
    from agro_model.farm import Farm, ProductionUnit
    from agronaut_agent.cli import main
    from agronaut_agent.farm_store import FarmStore
    from agronaut_agent.store import ConversationStore, _Db
    db = _Db(tmp_path / "weather-cli.sqlite3")
    owner = ConversationStore(db).get_or_create_user("cli", "local")
    store = FarmStore(db)
    store.create_farm(Farm("farm", owner, "Home", "UTC"))
    store.create_unit(owner, ProductionUnit("field", "farm", "Field", "soil", "soil", "open_field", "rainfed"))
    path = tmp_path / "weather-request.json"
    path.write_text(json.dumps(dict(latitude=5.6, longitude=-0.2, start="2026-07-06", end="2026-07-06")))
    monkeypatch.setattr(provider, "fetch_weather", lambda req: bundle())
    assert main(["field", "--db", str(tmp_path / "weather-cli.sqlite3"), "--unit", "field",
                 "fetch_weather", "--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["weather_sources"] == bundle()["weather_sources"]
    assert db._conn.execute("SELECT count(*) FROM field_calculations").fetchone()[0] == 0
    db._conn.close()


def test_chat_fetch_tool_uses_selected_unit_and_keeps_snapshot(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import Model, agent_and_units
    call = dict(name="field_records", id="weather-fetch", args=dict(operation="fetch_weather",
        data=dict(latitude=5.6, longitude=-0.2, start="2026-07-06", end="2026-07-06")))
    model = Model([AIMessage(content="", tool_calls=[call]), AIMessage(content="Weather preview ready.")])
    brain, owner = agent_and_units(tmp_path, model)
    brain.select_unit("cli", "one", "field")
    monkeypatch.setattr(provider, "fetch_weather", lambda req: bundle())
    brain.handle_message("cli", "one", "Fetch weather for this field at latitude 5.6 longitude -0.2 on July 6.")
    output = json.loads(brain.tool_results("cli", "one", "field_records")[-1])
    assert output["weather_sources"] == bundle()["weather_sources"]
    assert brain.field_workflow("cli", "one", "list", {}) == dict(calculations=[], water_days=[])


def test_retained_provider_calculation_runs_with_stdlib_only(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    data = inputs()
    weather = bundle()
    data.update({k: weather[k] for k in ("weather", "weather_sources")})
    path = tmp_path / "provider-calculation.json"
    path.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "irrigation-plan",
        "--input", str(path), "--json"], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(result.stdout)
    assert saved["inputs"]["weather_sources"] == weather["weather_sources"]
    assert saved["days"][0]["demand"]["values"]["et0_mm_day"] == 6
