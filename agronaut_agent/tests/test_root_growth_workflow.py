"""Changing root depth through saved budgets, measurements and immutable revisions."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.field_io import evaluate_field_request, render_field_report
from agronaut_agent.tests.test_crop_coefficient_workflow import series
from agronaut_agent.tests.test_field_workflow import inputs, water
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_soil_workflow import adoption, measurement


def roots(days=3):
    return [dict(day=f"2026-07-{6+i:02d}", root_depth_m=.8 + .1*i, source="Synthetic daily rooting assumptions",
        **(dict(added_layer_water_content=.25, added_layer_source="Synthetic layer mean before growth") if i else {}))
        for i in range(days)]


def growing():
    data = series()
    data["root_zone_schedule"] = roots()
    return data


def test_daily_growth_changes_capacity_and_starting_state_separately_from_fluxes():
    data = growing()
    original = copy.deepcopy(data)
    result = evaluate_field_request(data, "irrigation-plan")
    assert data == original
    first, second, third = result["days"]
    assert first["balance"]["values"]["total_available_mm"] == pytest.approx(136)
    assert second["balance"]["values"]["total_available_mm"] == pytest.approx(153)
    assert third["balance"]["values"]["total_available_mm"] == pytest.approx(170)
    assert second["balance"]["inputs"]["state"]["depletion_mm"] == pytest.approx(first["balance"]["values"]["depletion_mm"] + 7)
    assert second["root_growth"]["values"]["added_layer_available_water_mm"] == pytest.approx(10)
    assert result["inputs"]["events"] == original["events"]
    for row in result["days"]:
        values = row["balance"]["values"]
        previous = row["balance"]["inputs"]["state"]["depletion_mm"]
        # Explicit zero rainfall/irrigation: old depletion + actual ET + drainage.
        assert values["depletion_mm"] == pytest.approx(previous + values["actual_et_mm"] + values["drainage_mm"])
    report = render_field_report(result)
    assert "Root depth 0.9 m" in report and "storage expansion, not irrigation" in report
    assert "Synthetic layer mean before growth" in report


@pytest.mark.parametrize("change", ["missing", "reordered", "duplicate", "extra", "decrease", "no_layer", "bad_layer", "wrong_mode"])
def test_schedule_rejects_incomplete_or_unsupported_inputs(change):
    data = growing()
    if change == "missing":
        data["root_zone_schedule"].pop()
    elif change == "reordered":
        data["root_zone_schedule"].reverse()
    elif change == "duplicate":
        data["root_zone_schedule"].append(data["root_zone_schedule"][0])
    elif change == "extra":
        data["root_zone_schedule"].append(dict(roots()[0], day="2026-07-09"))
    elif change == "decrease":
        data["root_zone_schedule"][1]["root_depth_m"] = .7
    elif change == "no_layer":
        del data["root_zone_schedule"][1]["added_layer_water_content"]
    elif change == "bad_layer":
        data["root_zone_schedule"][1]["added_layer_water_content"] = .4
    with pytest.raises(ValueError):
        evaluate_field_request(data, "water-needs" if change == "wrong_mode" else "irrigation-plan")


def test_revision_preserves_evidence_and_parent_and_can_restore_fixed_depth(setup):
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    observed = workflow.apply_soil_state("owner", "field", **adoption(base))
    actual = workflow.record_day("owner", "field", water(), request_id="actual", cycle_id="cycle")
    replayed = workflow.replay("owner", "field", observed["id"], [actual["id"]], request_id="replay")
    args = dict(calculation_id=replayed["id"], root_zone_schedule=roots(), reason="Supplied growth assumptions", request_id="roots")
    updated = workflow.revise_roots("owner", "field", **args)
    assert workflow.revise_roots("owner", "field", **args) == updated
    assert updated["soil_state_evidence"] == replayed["soil_state_evidence"]
    assert updated["applied_water_record_ids"] == [actual["id"]]
    for key in ("soil", "initial_state", "events", "rainfall_overrides", "weather", "crop"):
        assert updated["inputs"][key] == replayed["inputs"][key]
    restored = workflow.revise_roots("owner", "field", calculation_id=updated["id"], root_zone_schedule=[],
                                     reason="Restore fixed depth", request_id="restore")
    assert restored["result"] == replayed["result"]
    assert workflow.get("owner", "field", replayed["id"]) == replayed
    with pytest.raises(ValueError, match="different data"):
        workflow.revise_roots("owner", "field", **dict(args, reason="Conflicting retry"))
    with pytest.raises(ValueError):
        workflow.revise_roots("other", "field", **args)


def test_retained_measurement_must_match_that_days_actual_depth(setup):
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    observed = workflow.apply_soil_state("owner", "field", **adoption(base))
    schedule = roots()
    schedule[0].update(root_depth_m=.9, added_layer_water_content=.25, added_layer_source="Synthetic layer")
    schedule[1].pop("added_layer_water_content")
    schedule[1].pop("added_layer_source")
    with pytest.raises(ValueError, match="represented depth"):
        workflow.revise_roots("owner", "field", calculation_id=observed["id"], root_zone_schedule=schedule,
                               reason="Incompatible sample depth", request_id="bad")
    assert len(workflow.list("owner", "field")["calculations"]) == 2


def test_extension_requires_new_depths_and_cannot_rewrite_existing_ones(setup):
    workflow, _ = setup
    data = growing()
    data["crop"] = inputs()["crop"]
    base = workflow.save("owner", "field", "root_zone_budget", data, request_id="base", cycle_id="cycle")
    weather = [dict(data["weather"][-1], day="2026-07-09")]
    events = [dict(data["events"][-1], day="2026-07-09")]
    root = dict(day="2026-07-09", root_depth_m=1., source="Supplied constant rooting depth")
    for added in (None, [root, roots()[0]]):
        with pytest.raises(ValueError, match="exactly appended"):
            workflow.revise_weather("owner", "field", base["id"], weather, events=events, root_zone_days=added, request_id="bad")
    updated = workflow.revise_weather("owner", "field", base["id"], weather, events=events, root_zone_days=[root], request_id="append")
    assert updated["inputs"]["root_zone_schedule"] == roots() + [root]
    revised = workflow.revise_crop("owner", "field", updated["id"], dict(coefficient=data["crop"]["coefficient"]),
                                    "Review Kc", request_id="kc")
    assert revised["inputs"]["root_zone_schedule"] == updated["inputs"]["root_zone_schedule"]


def test_midwindow_measurement_initializes_the_expanded_depth(setup):
    from agronaut_agent.crop_records import CropRecords
    workflow, _ = setup
    data = growing()
    data["initial_state"] = None
    base = workflow.save("owner", "field", "root_zone_budget", data, request_id="base", cycle_id="cycle")
    assert all(row["balance"] is None for row in base["result"]["result"]["days"])
    reading = CropRecords(workflow.db).record_measurement("owner", "field", dict(quantity="soil_water_content",
        value=.25, unit="m3/m3", observed_at="2026-07-07T18:00:00+00:00", source="Synthetic mean over .9 m depth",
        provenance="measured"), cycle_id="cycle", request_id="sample")
    revised = workflow.apply_soil_state("owner", "field", **adoption(base, observation_id=reading["id"],
                                        as_of="2026-07-07", represented_depth_m=.9))
    rows = revised["result"]["result"]["days"]
    assert rows[1]["balance"] is None
    assert rows[1]["soil_state_update"]["values"]["adopted_depletion_mm"] == pytest.approx(65)
    assert rows[2]["balance"]["inputs"]["state"]["depletion_mm"] == pytest.approx(72)


def test_stdlib_cli_revises_root_depth_and_reports_expansion(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "input.json"
    db = str(tmp_path / "workflow.sqlite3")
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    path.write_text(json.dumps(growing()))
    result = command("irrigation-plan", "--input", str(path), "--json")
    assert result["days"][-1]["root_growth"]["values"]["root_depth_m"] == 1
    spec = json.loads((root / "examples/soil_unit.json").read_text())
    spec["farm"]["timezone"] = "UTC"
    path.write_text(json.dumps(spec))
    command("farm", "--db", db, "create", "--input", str(path))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path))
    base = operation("save", dict(task="root_zone_budget", inputs=series(), request_id="base"))
    data = dict(calculation_id=base["id"], root_zone_schedule=roots(), reason="Supplied growth", request_id="growth")
    revised = operation("revise_roots", data)
    assert operation("revise_roots", data) == revised
    assert revised["inputs"]["soil"] == base["inputs"]["soil"]


def test_scoped_chat_uses_explicit_root_schedule(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units
    brain, _ = agent_and_units(tmp_path)
    brain.select_unit("cli", "one", "field")
    base = brain.field_workflow("cli", "one", "save", dict(task="root_zone_budget", inputs=series(), request_id="base"))
    data = dict(calculation_id=base["id"], root_zone_schedule=roots(), reason="Supplied daily rooting depths", request_id="roots")
    brain._base.replies = [AIMessage(content="", tool_calls=[dict(name="field_records", id="roots",
        args=dict(operation="revise_roots", data=data))]), AIMessage(content="Saved your supplied root depths.")]
    brain.handle_message("cli", "one", "Use my explicit daily root depths and supplied subsoil moisture in a revised budget.")
    saved = json.loads(brain.tool_results("cli", "one", "field_records")[-1])
    assert saved["parent_id"] == base["id"]
    assert saved["inputs"]["root_zone_schedule"] == roots()
