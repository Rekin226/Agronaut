"""Retained provider evidence and immutable dual-demand weather revisions."""

import copy
import json
import subprocess
import sys

import pytest

from agro_model.tests.test_evaporation import example
from agronaut_agent.domains import capabilities
from agronaut_agent.evaporation_io import (
    evaluate_evaporation_request,
    render_evaporation_report,
)
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_weather_provider import response, weather_request
from agronaut_agent.weather_provider import ASSUMPTIONS, adapt_response


def weather_bundle(retrieved="2026-07-05T12:00:00+00:00"):
    request = weather_request()
    request["end"] = "2026-07-08"
    return adapt_response(request, response(count=72), retrieved)


def provider_inputs(retrieved="2026-07-05T12:00:00+00:00"):
    data, bundle = example(), weather_bundle(retrieved)
    data.update({key: bundle[key] for key in ("weather", "weather_sources")})
    return data


@pytest.mark.parametrize("retrieved,kind", [("2026-07-05T12:00:00+00:00", "forecast"), ("2026-07-09T12:00:00+00:00", "historical")])
def test_import_is_offline_reproducible_and_preserves_provider_evidence(tmp_path, retrieved, kind):
    import jsonschema

    data = provider_inputs(retrieved)
    before = copy.deepcopy(data)
    jsonschema.validate(data, capabilities("soil")["soil"]["dual_crop_water"]["input_schema"])
    result = evaluate_evaporation_request(data)
    assert data == before == result["inputs"]
    assert all(row["weather_kind"] == kind for row in result["days"])
    assert all(item in result["assumptions"] for item in ASSUMPTIONS)
    assert "download time, not the forecast issue time" in render_evaporation_report(result)
    assert "grass-reference ET0 6 mm; rain 12 mm" in render_evaporation_report(result)
    assert "requested location 5.6, -0.2; provider grid 5.625, -0.25" in render_evaporation_report(result)
    # The provider fixture totals 6 mm ET and 12 mm rain per day. Kcb is 0.9.
    assert result["days"][0]["values"]["basal_et_mm"] == pytest.approx(5.4)
    assert result["days"][0]["values"]["surface_wetting_mm"] == pytest.approx(12+10/0.3)
    path = tmp_path/"retained-weather.json"
    path.write_text(json.dumps(data))
    output = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "dual-crop-water", "--input", str(path), "--json"],
                            capture_output=True, text=True, timeout=30)
    assert output.returncode == 0, output.stderr+output.stdout
    assert json.loads(output.stdout) == result


@pytest.mark.parametrize("change", ["rain", "et", "kind", "hash", "missing", "unused", "duplicate", "timezone", "overflow"])
def test_invalid_or_unmatched_snapshot_is_rejected_without_saving(setup, change):
    workflow, farms = setup
    data = provider_inputs()
    if change in ("rain", "et", "kind"):
        key, value = {"rain": ("rainfall_mm", 0), "et": ("et0_mm_day", 1), "kind": ("kind", "observed")}[change]
        data["weather"][0][key] = value
    elif change == "hash":
        data["weather_sources"][0]["response"]["latitude"] = 1
    elif change == "missing":
        del data["weather_sources"]
    elif change == "unused":
        data["weather"] = example()["weather"]
    elif change == "duplicate":
        data["weather_sources"] *= 2
    elif change == "timezone":
        data["site"]["timezone"] = "Africa/Accra"
    else:
        data["weather_sources"][0]["request"].update(start="9999-12-31", end="9999-12-31")
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "dual_crop_water", data, request_id="invalid", cycle_id="cycle")
    assert farms.export("owner") == before


def test_partial_forecast_revisions_keep_both_snapshots_and_do_not_write_actuals(setup):
    workflow, farms = setup
    data = provider_inputs()
    parent = workflow.save("owner", "field", "dual_crop_water", data, request_id="parent", cycle_id="cycle")
    updated = weather_bundle("2026-07-05T15:00:00+00:00")
    patch = dict(calculation_id=parent["id"], weather=updated["weather"][:1], weather_sources=updated["weather_sources"], request_id="revision")
    # Exercise the tool/CLI dispatcher, including its allowed argument list.
    child = execute_field_workflow(workflow.db, "owner", "field", "revise_weather", patch)
    assert execute_field_workflow(workflow.db, "owner", "field", "revise_weather", patch) == child
    assert child["parent_id"] == parent["id"]
    assert child["inputs"]["days"] == data["days"]
    assert child["inputs"]["initial_state"] == data["initial_state"]
    assert child["inputs"]["weather"] == updated["weather"][:1]+data["weather"][1:]
    assert child["inputs"]["weather_sources"] == data["weather_sources"]+updated["weather_sources"]
    with pytest.raises(ValueError, match="newer retrieved_at"):
        workflow.revise_weather("owner", "field", child["id"], data["weather"][:1], request_id="stale", weather_sources=data["weather_sources"])
    complete = workflow.revise_weather("owner", "field", child["id"], updated["weather"][1:], request_id="rest", weather_sources=updated["weather_sources"])
    assert complete["inputs"]["weather_sources"] == updated["weather_sources"]
    assert workflow.get("owner", "field", parent["id"]) == parent
    assert farms.export("owner")["water_days"] == []
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.revise_weather(owner, unit, parent["id"], updated["weather"], request_id="foreign", weather_sources=updated["weather_sources"])


def test_extension_requires_explicit_dual_inputs_and_preserves_existing_days(setup):
    workflow, _ = setup
    data = example()
    parent = workflow.save("owner", "field", "dual_crop_water", data, request_id="parent", cycle_id="cycle")
    day = dict(data["days"][-1], day="2026-07-09", kcb=0.8, net_irrigation_mm=7, source="Explicit next-day scenario")
    weather = dict(data["weather"][-1], day=day["day"])
    arguments = dict(calculation_id=parent["id"], weather=[weather], dual_days=[day], request_id="append")
    child = execute_field_workflow(workflow.db, "owner", "field", "revise_weather", arguments)
    expected = copy.deepcopy(data)
    expected["days"].append(day)
    expected["weather"].append(weather)
    assert child["inputs"] == expected
    assert child["result"]["result"] == evaluate_evaporation_request(expected)
    assert workflow.get("owner", "field", parent["id"]) == parent
    assert child["applied_water_record_ids"] == []


@pytest.mark.parametrize("case", ["missing", "duplicate", "existing", "gap", "prepend", "runoff", "events", "root_days", "overflow", "closure"])
def test_invalid_weather_extensions_or_replacements_are_atomic(setup, case):
    workflow, farms = setup
    data = example()
    data["days"][0]["runoff_mm"] = 1
    data["weather"][0]["rainfall_mm"] = 1
    parent = workflow.save("owner", "field", "dual_crop_water", data, request_id="parent", cycle_id="cycle")
    day = dict(data["days"][-1], day="2026-07-09")
    weather = dict(data["weather"][-1], day=day["day"])
    kwargs = dict(dual_days=[day])
    if case == "missing":
        kwargs = {}
    elif case == "duplicate":
        kwargs["dual_days"] *= 2
    elif case == "existing":
        kwargs["dual_days"] = [data["days"][0]]
        weather = data["weather"][0]
    elif case in ("gap", "prepend"):
        day["day"] = weather["day"] = "2026-07-10" if case == "gap" else "2026-07-05"
    elif case == "runoff":
        kwargs = {}
        weather = dict(data["weather"][0], rainfall_mm=0)
    elif case == "events":
        kwargs["events"] = [dict(day=day["day"], net_irrigation_mm=0, runoff_mm=0, source="Synthetic")]
    elif case == "root_days":
        kwargs["root_zone_days"] = [dict(day=day["day"], root_depth_m=0.5, source="Synthetic")]
    elif case == "overflow":
        kwargs = {}
        weather["day"] = "9999-12-31"
    else:
        from agronaut_agent.crop_records import CropRecords
        CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-08", "Ended", "Synthetic", request_id="close", expected_revision=None)
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.revise_weather("owner", "field", parent["id"], [weather], request_id="invalid", **kwargs)
    assert farms.export("owner") == before


def test_dual_day_arguments_cannot_modify_single_coefficient_budgets(setup):
    from agronaut_agent.tests.test_field_workflow import inputs
    workflow, farms = setup
    data = inputs()
    parent = workflow.save("owner", "field", "root_zone_budget", data, request_id="parent", cycle_id="cycle")
    before = farms.export("owner")
    with pytest.raises(ValueError, match="dual_crop_water"):
        workflow.revise_weather("owner", "field", parent["id"], data["weather"], request_id="wrong", dual_days=example()["days"])
    assert farms.export("owner") == before
