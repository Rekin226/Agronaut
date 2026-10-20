"""Dated crop demand, immutable recalculation and scoped stage-report evidence."""

import copy
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.field_io import evaluate_field_request, render_field_report
from agronaut_agent.tests.test_field_workflow import inputs, setup, water  # noqa: F401
from agronaut_agent.tests.test_mixed_water_replay import historical_inputs


def curve():
    return [dict(start="2026-07-06", end="2026-07-08", start_value=0.5, end_value=1.1,
                 stage="Synthetic development", source="Supplied synthetic Kc endpoints and dates")]


def series():
    data = inputs()
    data["crop"] = dict(name="tomato", coefficient_schedule=curve())
    for offset in (1, 2):
        day = (date(2026, 7, 6) + timedelta(days=offset)).isoformat()
        data["weather"].append(dict(data["weather"][0], day=day))
        data["events"].append(dict(data["events"][0], day=day))
    return data


def stage(journal, unit="field", cycle="cycle", entry="stage-1", **changes):
    data = dict(entry=entry, day="2026-07-06", location="North rows", stage="Reported development",
                source="Synthetic field report", provenance="reported")
    data.update(changes)
    return journal.record_stage("owner", unit, cycle, data, request_id=entry)


def test_schedule_changes_daily_demand_and_state_without_changing_weather():
    data = series()
    saved = copy.deepcopy(data)
    result = evaluate_field_request(data, "irrigation-plan")
    assert data == saved
    rows = result["days"]
    assert [r["demand"]["coefficients"]["kc"]["value"] for r in rows] == pytest.approx([0.5, 0.8, 1.1])
    et0 = rows[0]["demand"]["values"]["et0_mm_day"]
    assert [r["demand"]["values"]["etc_mm_day"] for r in rows] == pytest.approx([et0 * k for k in (0.5, 0.8, 1.1)])
    assert rows[1]["balance"]["inputs"]["state"]["depletion_mm"] == rows[0]["balance"]["values"]["depletion_mm"]
    assert rows[0]["demand"]["inputs"]["coefficient_period"] == curve()[0]
    assert "https://www.fao.org/4/X0490E/x0490e0b.htm" in rows[0]["demand"]["sources"]
    assert "Kc 0.8" in render_field_report(result)
    assert "Synthetic development" in render_field_report(result)


@pytest.mark.parametrize("change", ["both", "none", "empty", "gap", "missing_day", "unknown"])
def test_coefficient_contract_rejects_ambiguous_or_incomplete_curves(change):
    data = series()
    if change == "both":
        data["crop"]["coefficient"] = inputs()["crop"]["coefficient"]
    elif change == "none":
        del data["crop"]["coefficient_schedule"]
    elif change == "empty":
        data["crop"]["coefficient_schedule"] = []
    elif change == "gap":
        data["crop"]["coefficient_schedule"].append(dict(curve()[0], start="2026-07-10", end="2026-07-12"))
    elif change == "missing_day":
        data["crop"]["coefficient_schedule"][0]["end"] = "2026-07-07"
    else:
        data["crop"]["coefficient_schedule"][0]["guessed_root_depth"] = 1
    with pytest.raises(ValueError):
        evaluate_field_request(data, "irrigation-plan")


def test_crop_revision_preserves_provider_actuals_and_initial_soil_state(request):
    workflow, _ = request.getfixturevalue("setup")
    base = workflow.save("owner", "field", "root_zone_budget", historical_inputs(), request_id="base", cycle_id="cycle")
    actual = workflow.record_day("owner", "field", water(), request_id="water", cycle_id="cycle")
    replayed = workflow.replay("owner", "field", base["id"], [actual["id"]], request_id="replay", weather_policy="allow_historical")
    journal = CropRecords(workflow.db)
    report = stage(journal)
    updated = workflow.revise_crop("owner", "field", replayed["id"], dict(coefficient_schedule=curve()),
        "Explicit revised growth assumptions", stage_record_ids=[report["id"]], request_id="crop")
    for key in ("weather", "weather_sources", "rainfall_overrides", "events", "initial_state", "soil", "area_m2"):
        assert updated["inputs"][key] == replayed["inputs"][key]
    assert updated["applied_water_record_ids"] == [actual["id"]]
    assert updated["crop_stage_evidence"] == [report]
    assert updated["result"]["result"]["days"][0]["demand"]["values"]["etc_mm_day"] == 3
    assert updated["result"]["result"]["days"][0]["balance"]["values"]["depletion_mm"] == 56
    assert workflow.get("owner", "field", replayed["id"]) == replayed
    assert workflow.review_crop("owner", "field", updated["id"])["current_stage_reports"] == [report]


def test_stage_correction_flags_review_without_rewriting_or_breaking_retries(request):
    workflow, _ = request.getfixturevalue("setup")
    base = workflow.save("owner", "field", "crop_water", inputs(), request_id="base", cycle_id="cycle")
    journal = CropRecords(workflow.db)
    report = stage(journal)
    coefficients = dict(coefficient_schedule=curve())
    revised = workflow.revise_crop("owner", "field", base["id"], coefficients, "Supplied changes",
                                   request_id="revision", stage_record_ids=[report["id"]])
    corrected = journal.record_stage("owner", "field", "cycle", dict(report["data"], stage="Corrected report"),
                                      request_id="correction", supersedes_id=report["id"])
    review = workflow.review_crop("owner", "field", revised["id"])
    assert review["needs_stage_review"]
    assert review["linked_stage_reports"][0]["replacement_id"] == corrected["id"]
    assert review["linked_stage_reports"][0]["snapshot"] == report
    assert review["current_stage_reports"] == [corrected]
    assert workflow.revise_crop("owner", "field", base["id"], coefficients, "Supplied changes",
        request_id="revision", stage_record_ids=[report["id"]]) == revised
    with pytest.raises(ValueError, match="superseded"):
        workflow.revise_crop("owner", "field", revised["id"], coefficients, "Use old report",
                            request_id="stale", stage_record_ids=[report["id"]])
    with pytest.raises(ValueError, match="different data"):
        workflow.revise_crop("owner", "field", base["id"], coefficients, "Other reason",
                            request_id="revision", stage_record_ids=[report["id"]])
    assert workflow.get("owner", "field", revised["id"]) == revised


def test_stage_evidence_is_scoped_to_unit_cycle_kind_and_live_revision(request):
    workflow, _ = request.getfixturevalue("setup")
    base = workflow.save("owner", "field", "crop_water", inputs(), request_id="base", cycle_id="cycle")
    journal = CropRecords(workflow.db)
    other = stage(journal, unit="field2", cycle="cycle2", entry="other")
    report = stage(journal)
    voided = journal.void_observation("owner", "field", report["id"], "Synthetic withdrawal", request_id="void")
    for identifier in (other["id"], report["id"], voided["id"], "unknown"):
        with pytest.raises(ValueError):
            workflow.revise_crop("owner", "field", base["id"], dict(coefficient_schedule=curve()),
                                 "Supplied assumptions", request_id="invalid", stage_record_ids=[identifier])
    assert len(workflow.list("owner", "field")["calculations"]) == 1
    with pytest.raises(ValueError):
        workflow.review_crop("other", "field", base["id"])


def test_extension_cannot_extrapolate_and_coefficient_revision_preserves_lineage(request):
    workflow, _ = request.getfixturevalue("setup")
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    patch = [dict(base["inputs"]["weather"][0], day="2026-07-09")]
    events = [dict(base["inputs"]["events"][0], day="2026-07-09")]
    with pytest.raises(ValueError, match="extrapolation"):
        workflow.revise_weather("owner", "field", base["id"], patch, events=events, request_id="beyond")
    extended = curve() + [dict(curve()[0], start="2026-07-09", end="2026-07-12", start_value=1.1, end_value=1.1)]
    revised = workflow.revise_crop("owner", "field", base["id"], dict(coefficient_schedule=extended),
                                   "Supplied additional growth dates", request_id="extend-kc")
    updated = workflow.revise_weather("owner", "field", revised["id"], patch, events=events, request_id="extend")
    assert updated["parent_id"] == revised["id"]
    assert updated["result"]["result"]["days"][-1]["demand"]["coefficients"]["kc"]["value"] == 1.1
    assert workflow.get("owner", "field", base["id"]) == base


def test_schedule_dates_must_match_crop_cycle_establishment(request):
    workflow, _ = request.getfixturevalue("setup")
    data = series()
    data["crop"]["coefficient_schedule"][0]["start"] = "2026-06-30"
    with pytest.raises(ValueError, match="before the crop cycle"):
        workflow.save("owner", "field", "crop_water", data, request_id="invalid", cycle_id="cycle")


def test_water_replay_and_weather_revision_keep_exact_stage_evidence(request):
    workflow, _ = request.getfixturevalue("setup")
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    journal = CropRecords(workflow.db)
    report = stage(journal)
    revised = workflow.revise_crop("owner", "field", base["id"], dict(coefficient_schedule=curve()), "Supplied Kc",
                                   stage_record_ids=[report["id"]], request_id="coefficient")
    journal.void_observation("owner", "field", report["id"], "Synthetic withdrawal", request_id="void")
    actual = workflow.record_day("owner", "field", water(), request_id="actual", cycle_id="cycle")
    replayed = workflow.replay("owner", "field", revised["id"], [actual["id"]], request_id="water")
    refreshed = workflow.revise_weather("owner", "field", replayed["id"],
        [dict(inputs()["weather"][0], net_radiation_mj_m2=16)], request_id="weather")
    assert replayed["crop_stage_evidence"] == refreshed["crop_stage_evidence"] == [report]
    review = workflow.review_crop("owner", "field", refreshed["id"])
    assert review["needs_stage_review"] and review["current_stage_reports"] == []
    assert workflow.get("owner", "field", revised["id"]) == revised


def test_same_unit_different_cycle_stage_is_rejected(request):
    from agro_model.farm import ProductionCycle
    workflow, store = request.getfixturevalue("setup")
    store.create_cycle("owner", ProductionCycle("different-cycle", "field", "tomato", "2026-07-01"))
    report = stage(CropRecords(workflow.db), cycle="different-cycle")
    base = workflow.save("owner", "field", "crop_water", inputs(), request_id="base", cycle_id="cycle")
    with pytest.raises(ValueError, match="crop cycle"):
        workflow.revise_crop("owner", "field", base["id"], dict(coefficient_schedule=curve()), "Different cycle",
                            stage_record_ids=[report["id"]], request_id="bad")


def test_changed_planting_date_flags_prior_curve_and_prevents_unreviewed_recalculation(request):
    workflow, _ = request.getfixturevalue("setup")
    data = inputs()
    data["crop"] = dict(name="tomato", coefficient_schedule=[dict(curve()[0], start="2026-07-01")])
    original = workflow.save("owner", "field", "crop_water", data, request_id="base", cycle_id="cycle")
    journal = CropRecords(workflow.db)
    journal.correct_planting("owner", "field", "cycle", "tomato", "2026-07-03", "Synthetic source",
                              "Corrected planting date", request_id="planting", expected_revision=None)
    review = workflow.review_crop("owner", "field", original["id"])
    assert review["cycle_review"]["changed_fields"] == ["planted_on"]
    assert "coefficient schedule starts before current planting date" in review["cycle_review"]["conflicts"]
    assert workflow.get("owner", "field", original["id"]) == original
    with pytest.raises(ValueError, match="before the crop cycle"):
        workflow.revise_weather("owner", "field", original["id"], data["weather"], request_id="invalid")


def test_stdlib_cli_calculation_revision_and_review(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "input.json"
    db = str(tmp_path / "workflow.sqlite3")
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    path.write_text(json.dumps(series()))
    result = command("water-needs", "--input", str(path), "--json")
    assert [r["demand"]["coefficients"]["kc"]["value"] for r in result["days"]] == pytest.approx([0.5, 0.8, 1.1])
    spec = json.loads((root / "examples/soil_unit.json").read_text())
    spec["farm"]["timezone"] = "UTC"
    path.write_text(json.dumps(spec))
    command("farm", "--db", db, "create", "--input", str(path))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path))
    base = operation("save", dict(task="root_zone_budget", inputs=inputs(), request_id="base"))
    request_data = dict(calculation_id=base["id"], coefficients=dict(coefficient_schedule=curve()),
                        reason="Supplied seasonal assumptions", request_id="crop")
    revised = operation("revise_crop", request_data)
    assert operation("revise_crop", request_data) == revised
    review = operation("review_crop", dict(calculation_id=revised["id"]))
    assert review["crop"]["coefficient_schedule"] == curve()
    assert not review["current_stage_reports"] and review["cycle_review"] is None


def test_scoped_chat_uses_supplied_curve_and_keeps_parent(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units
    brain, _ = agent_and_units(tmp_path)
    brain.select_unit("cli", "one", "field")
    base = brain.field_workflow("cli", "one", "save", dict(task="root_zone_budget", inputs=inputs(), request_id="base"))
    data = dict(calculation_id=base["id"], coefficients=dict(coefficient_schedule=curve()),
                reason="Supplied explicit dates and Kc", request_id="crop")
    brain._base.replies = [AIMessage(content="", tool_calls=[dict(name="field_records", id="crop-revision",
        args=dict(operation="revise_crop", data=data))]), AIMessage(content="Saved the supplied coefficient curve.")]
    brain.handle_message("cli", "one", "Use the supplied dated Kc curve and save a new calculation version.")
    updated = json.loads(brain.tool_results("cli", "one", "field_records")[-1])
    assert updated["inputs"]["crop"]["coefficient_schedule"] == curve()
    assert updated["parent_id"] == base["id"]
    assert brain.field_workflow("cli", "one", "get", dict(calculation_id=base["id"])) == base
