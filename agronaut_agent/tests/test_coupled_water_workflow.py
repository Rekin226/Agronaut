"""Public contract, CLI, ownership and exact continuation for coupled soil water."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agronaut_agent.coupled_water_io import (
    evaluate_coupled_water_request,
    render_coupled_water_report,
)
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_field_workflow import setup as setup

ROOT = Path(__file__).resolve().parents[2]


def example():
    return json.loads((ROOT/"examples/coupled_soil_water.json").read_text())


def next_day(data, day="2026-07-07"):
    weather, days = deepcopy(data["weather"]), deepcopy(data["days"])
    weather[0]["day"] = days[0]["day"] = day
    return weather, days


def test_discoverable_schema_adapter_cli_and_exact_checkpoints_agree(tmp_path):
    import jsonschema
    data = example()
    cap = capabilities("soil")["soil"]["coupled_soil_water"]
    assert cap["status"] == "experimental"
    jsonschema.validate(data, cap["input_schema"])
    result = evaluate_coupled_water_request(data)
    assert result["totals"]["soil_evaporation_l"]["exact"] == "200/1"
    assert result["totals"]["actual_transpiration_l"]["exact"] == "200/1"
    assert result["totals"]["actual_evapotranspiration_l"]["exact"] == "400/1"
    assert result["totals"]["combined_balance_residual_l"]["exact"] == "0/1"
    assert result["days"][0]["root_layer_units"]["maximum_rate_per_day"] == "m3/m3/day"
    assert result["days"][0]["root_status_units"]["total_available_mm"] == "mm"
    assert run_capability("soil", "coupled_soil_water", data)["result"] == result
    for domain in ("aquaponics", "hydroponics"):
        with pytest.raises(ValueError):
            run_capability(domain, "coupled_soil_water", data)
    path = tmp_path/"coupled.json"
    path.write_text(json.dumps(data))
    cmd = [sys.executable, "-S", "-m", "agronaut_agent.cli", "coupled-soil-water", "--input", str(path)]
    json_run = subprocess.run([*cmd, "--json"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert json_run.returncode == 0, json_run.stdout+json_run.stderr
    assert json.loads(json_run.stdout) == result
    report = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert report.returncode == 0 and report.stdout.strip() == render_coupled_water_report(result)
    assert "EXPERIMENTAL" in report.stdout and "transpiration 200 L" in report.stdout


def test_save_retry_export_erasure_and_input_mutation_leave_other_records_unchanged(setup):
    workflow, farms = setup
    data = example()
    before = farms.export("owner")
    saved = workflow.save("owner", "field", "coupled_soil_water", data, request_id="coupled", cycle_id="cycle")
    assert workflow.save("owner", "field", "coupled_soil_water", data, request_id="coupled", cycle_id="cycle") == saved
    assert saved["inputs"] == data and saved["result"]["result"]["inputs"] == data
    after = farms.export("owner")
    assert {k:v for k,v in before.items() if k != "calculations"} == {k:v for k,v in after.items() if k != "calculations"}
    data["days"][0]["patches"][0]["water"]["rainfall_mm"] = "10"
    assert workflow.get("owner", "field", saved["id"]) == saved
    with pytest.raises(ValueError, match="different data"):
        workflow.save("owner", "field", "coupled_soil_water", data, request_id="coupled", cycle_id="cycle")
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.get(owner, unit, saved["id"])
    others = farms.export("other")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other") == others


def test_owned_continuation_matches_uninterrupted_replay_and_preserves_parent(setup):
    import jsonschema
    workflow, _ = setup
    data = example()
    parent = workflow.save("owner", "field", "coupled_soil_water", data, request_id="base", cycle_id="cycle")
    weather, days = next_day(data)
    days[0]["patches"][0]["water"]["irrigation_at_surface_mm"] = "5"
    args = dict(calculation_id=parent["id"], weather=weather, days=days, reason="Explicit next day", request_id="next")
    child = execute_field_workflow(workflow.db, "owner", "field", "continue_coupled_water", args)
    assert execute_field_workflow(workflow.db, "owner", "field", "continue_coupled_water", args) == child
    assert child["parent_id"] == parent["id"] and child["cycle_id"] == "cycle"
    assert child["inputs"]["initial_state"]["checkpoint"] == parent["result"]["result"]["ending_checkpoint"]
    jsonschema.validate(child["inputs"], capabilities("soil")["soil"]["coupled_soil_water"]["input_schema"])
    full = deepcopy(data)
    full["weather"].extend(weather)
    full["days"].extend(days)
    uninterrupted = evaluate_coupled_water_request(full)
    assert child["result"]["result"]["ending_checkpoint"] == uninterrupted["ending_checkpoint"]
    assert child["result"]["result"]["days"] == uninterrupted["days"][1:]
    assert child["applied_water_record_ids"] == []
    assert workflow.get("owner", "field", parent["id"]) == parent
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.continue_coupled_water(owner, unit, **{**args, "request_id": "wrong"})
    with pytest.raises(ValueError):
        workflow.continue_coupled_water("owner", "field", **{**args, "weather": data["weather"], "days": data["days"], "request_id": "overlap"})


@pytest.mark.parametrize("case", ["timezone", "crop", "preplant", "closed", "wrong_cycle"])
def test_scope_and_crop_lifecycle_rejections_are_atomic(setup, case):
    workflow, farms = setup
    data, cycle = example(), "cycle"
    if case == "timezone":
        data["site"]["timezone"] = data["weather"][0]["timezone"] = "Africa/Ouagadougou"
    elif case == "crop":
        data["crop"]["name"] = "pepper"
    elif case == "preplant":
        data["initial_state"]["as_of"] = "2026-06-29"
        data["days"][0]["day"] = data["weather"][0]["day"] = "2026-06-30"
    elif case == "closed":
        CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-05", "Ended", "Synthetic", request_id="close", expected_revision=None)
    else:
        cycle = "cycle2"
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "coupled_soil_water", data, request_id="bad", cycle_id=cycle)
    assert farms.export("owner") == before


def test_changed_cycle_context_is_reported_and_blocks_continuation_without_rewriting_history(setup):
    workflow, farms = setup
    parent = workflow.save("owner", "field", "coupled_soil_water", example(), request_id="base", cycle_id="cycle")
    crops = CropRecords(workflow.db)
    crops.correct_planting("owner", "field", "cycle", "pepper", "2026-07-08", "Synthetic", "Correction", request_id="correct", expected_revision=None)
    summary = crops.season_review("owner", "field", "cycle")["calculations"][0]
    assert len(summary["cycle_conflicts"]) == 2
    before = farms.export("owner")
    weather, days = next_day(example(), "2026-07-09")
    with pytest.raises(ValueError, match="context changed"):
        workflow.continue_coupled_water("owner", "field", parent["id"], weather, days, "Continuation", request_id="bad")
    assert farms.export("owner") == before
    assert workflow.get("owner", "field", parent["id"]) == parent


@pytest.mark.parametrize("case", ["numeric_float", "boolean", "missing_water", "unknown_parameter", "duplicate_patch", "duplicate_layer", "inapplicable", "missing_day", "checkpoint_area", "wrong_checkpoint_kind", "provider_without_snapshot", "future_observed"])
def test_invalid_public_inputs_are_rejected(case):
    data = example()
    patch = data["days"][0]["patches"][0]
    if case == "numeric_float":
        data["area_m2"] = 100.0
    elif case == "boolean":
        patch["water"]["rainfall_mm"] = True
    elif case == "missing_water":
        del patch["water"]["irrigation_at_surface_mm"]
    elif case == "unknown_parameter":
        patch["root_parameters"]["guessed_crop_default"] = True
    elif case == "duplicate_patch":
        data["days"][0]["patches"].append(deepcopy(patch))
    elif case == "duplicate_layer":
        data["initial_state"]["patches"][0]["layers"].append(deepcopy(data["initial_state"]["patches"][0]["layers"][0]))
    elif case == "inapplicable":
        data["conditions"]["no_capillary_rise"] = False
    elif case == "missing_day":
        data["weather"][0]["day"] = data["days"][0]["day"] = "2026-07-07"
    elif case in ("checkpoint_area", "wrong_checkpoint_kind"):
        result = evaluate_coupled_water_request(data)
        data["initial_state"] = dict(checkpoint=result["initial_checkpoint"], source="Explicit restart")
        if case == "checkpoint_area":
            data["area_m2"] = "99"
        else:
            checkpoint = json.loads(data["initial_state"]["checkpoint"])
            checkpoint["kind"] = "day"
            data["initial_state"]["checkpoint"] = json.dumps(checkpoint)
    elif case == "provider_without_snapshot":
        data["weather"][0]["source_snapshot_id"] = "missing-provider-data"
    else:
        future = datetime.now(timezone.utc).date()+timedelta(days=2)
        data["initial_state"]["as_of"] = (future-timedelta(days=1)).isoformat()
        data["weather"][0].update(day=future.isoformat(), kind="observed")
        data["days"][0]["day"] = future.isoformat()
    with pytest.raises(ValueError):
        evaluate_coupled_water_request(data)


def test_legacy_net_root_zone_logs_cannot_be_replayed_as_surface_application(setup):
    from agronaut_agent.tests.test_field_workflow import water
    workflow, farms = setup
    parent = workflow.save("owner", "field", "coupled_soil_water", example(), request_id="base")
    log = workflow.record_day("owner", "field", water(), request_id="actual")
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.replay("owner", "field", parent["id"], [log["id"]], request_id="bad")
    assert farms.export("owner") == before


def test_retained_provider_weather_is_verified_and_does_not_override_patch_rain():
    from agronaut_agent.tests.test_weather_provider import bundle
    data, retained = example(), bundle()
    data.update({k: retained[k] for k in ("weather", "weather_sources")})
    result = evaluate_coupled_water_request(data)
    assert result["inputs"]["weather_sources"] == retained["weather_sources"]
    assert result["totals"]["rainfall_l"]["exact"] == "0/1"
    report = render_coupled_water_report(result)
    assert "grass-reference ET0 6 mm" in report and "provider grid" in report
    assert "download time, not the forecast issue time" in report
    data["weather"][0]["et0_mm_day"] = 1
    with pytest.raises(ValueError, match="retained provider"):
        evaluate_coupled_water_request(data)


def test_cycle_closure_after_parent_end_blocks_later_continuation(setup):
    workflow, farms = setup
    parent = workflow.save("owner", "field", "coupled_soil_water", example(), request_id="base", cycle_id="cycle")
    CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-06", "Ended", "Synthetic", request_id="close", expected_revision=None)
    weather, days = next_day(example())
    before = farms.export("owner")
    with pytest.raises(ValueError, match="cycle end"):
        workflow.continue_coupled_water("owner", "field", parent["id"], weather, days, "Next day", request_id="bad")
    assert farms.export("owner") == before


def test_stdlib_terminal_setup_save_and_continue_use_owned_records(tmp_path):
    db, path = tmp_path/"coupled.sqlite3", tmp_path/"request.json"
    def command(*args):
        process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=ROOT,
                                 capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout+process.stderr
        return json.loads(process.stdout)
    command("farm", "--db", str(db), "create", "--input", str(ROOT/"examples/soil_unit.json"))
    data = example()
    data["site"]["timezone"] = data["weather"][0]["timezone"] = "Africa/Accra"
    path.write_text(json.dumps(dict(task="coupled_soil_water", inputs=data, request_id="base")))
    parent = command("field", "--db", str(db), "save", "--input", str(path))
    weather, days = next_day(data)
    path.write_text(json.dumps(dict(calculation_id=parent["id"], weather=weather, days=days, reason="Synthetic continuation", request_id="next")))
    child = command("field", "--db", str(db), "continue_coupled_water", "--input", str(path))
    assert child["parent_id"] == parent["id"]
    assert len(command("field", "--db", str(db), "list")["calculations"]) == 2
