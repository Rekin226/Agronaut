"""Reviewed observations condition continuation, never rewrite prior predicted fluxes."""

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from fractions import Fraction

import pytest

from agro_model.soil_snapshots import loads_soil_snapshot
from agronaut_agent.coupled_state_workflow import continuation_checkpoint
from agronaut_agent.coupled_water_io import evaluate_coupled_water_request
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_coupled_soil_evaluation import (
    compare,
    entry,
    observation,
    saved,
)
from agronaut_agent.tests.test_coupled_water_workflow import ROOT, example, next_day
from agronaut_agent.tests.test_field_workflow import setup as setup


def reading(**changes):
    row = dict(observation_id="soil", patch_id="plot", layer_id="upper", top_m="0", bottom_m="0.1",
        representation_note="Synthetic complete upper layer and patch mean", water_content_adjustment=0,
        adjustment_source="Synthetic zero timing change", expected_review=None)
    row.update(changes)
    return row


def apply(workflow, parent, readings=None, memories=None, request_id="adopt"):
    return execute_field_workflow(workflow.db, "owner", "field", "apply_coupled_state", dict(calculation_id=parent["id"],
        readings=[reading()] if readings is None else readings,
        evaporation_memories=[dict(id="plot", retain=True, source="Synthetic drying-history review")] if memories is None else memories,
        dataset_kind="synthetic", reason="Synthetic state correction", request_id=request_id))


def remove(workflow, parent, request_id="withdraw"):
    return execute_field_workflow(workflow.db, "owner", "field", "remove_coupled_state", dict(calculation_id=parent["id"], reason="Synthetic withdrawal", request_id=request_id))


def test_adoption_keeps_original_prediction_and_flux_balance_and_continues_exact_revised_state(setup):
    workflow, store = setup
    observation(store)
    parent = saved(workflow)
    before = store.export("owner")
    child = apply(workflow, parent)
    assert apply(workflow, parent) == child
    assert child["result"] == parent["result"] and child["inputs"] == parent["inputs"]
    initial = loads_soil_snapshot(parent["result"]["result"]["ending_checkpoint"], expected_kind="state")
    adopted = loads_soil_snapshot(continuation_checkpoint(child), expected_kind="state")
    assert adopted.soil.patches[0].compartments[0].water_content == Fraction(23, 100)
    assert adopted.soil.patches[0].compartments[1:] == initial.soil.patches[0].compartments[1:]
    assert adopted.surface == initial.surface
    assert child["coupled_state_revision"]["reconciliation"]["adjustment_residual_l"]["exact"] == "0/1"
    assert Fraction(child["coupled_state_revision"]["cumulative_storage_adjustment_l"]["exact"]) == adopted.soil.water_l-initial.soil.water_l
    after = store.export("owner")
    assert {k:v for k,v in before.items() if k != "calculations"} == {k:v for k,v in after.items() if k != "calculations"}
    weather, days = next_day(example())
    continued = workflow.continue_coupled_water("owner", "field", child["id"], weather, days, "Next day", request_id="continue")
    assert continued["inputs"]["initial_state"]["checkpoint"] == continuation_checkpoint(child)
    assert continued["coupled_state_evidence"] == child["coupled_state_evidence"]
    assert "coupled_state_revision" not in continued
    independent = dict(example(), initial_state=continued["inputs"]["initial_state"], weather=weather, days=days)
    assert continued["result"]["result"] == evaluate_coupled_water_request(independent)
    assert workflow.get("owner", "field", parent["id"]) == parent


def test_further_layer_revision_preserves_other_adopted_cells_and_withdrawal_restores_prediction(setup):
    workflow, store = setup
    observation(store)
    observation(store, "deeper", value=.28)
    parent = saved(workflow)
    first = apply(workflow, parent)
    second = apply(workflow, first, [reading(observation_id="deeper", layer_id="middle", top_m="0.1", bottom_m="0.3")], request_id="second")
    end = loads_soil_snapshot(continuation_checkpoint(second), expected_kind="state")
    assert end.soil.patches[0].compartments[0].water_content == Fraction(23, 100)
    assert end.soil.patches[0].compartments[1].water_content == Fraction(28, 100)
    assert len(second["coupled_state_evidence"]) == 2
    restored = remove(workflow, second)
    assert "coupled_state_revision" not in restored and "coupled_state_evidence" not in restored
    assert continuation_checkpoint(restored) == parent["result"]["result"]["ending_checkpoint"]
    assert restored["result"] == parent["result"]
    assert remove(workflow, second) == restored


def test_quality_changes_require_review_or_withdrawal_before_new_continuation(setup):
    workflow, store = setup
    observation(store)
    parent = saved(workflow)
    child = apply(workflow, parent)
    review = ObservationReviews(workflow.db).review("owner", "field", "soil", "checked", "Synthetic review", "Synthetic",
        request_id="check", expected_revision=None)
    info = execute_field_workflow(workflow.db, "owner", "field", "review_coupled_state", dict(calculation_id=child["id"]))
    assert info["linked_measurements"][0]["needs_review"]
    weather, days = next_day(example())
    with pytest.raises(ValueError, match="review changed"):
        workflow.continue_coupled_water("owner", "field", child["id"], weather, days, "Next", request_id="next")
    with pytest.raises(ValueError, match="review changed"):
        apply(workflow, child, request_id="stale")
    refreshed = apply(workflow, child, [reading(expected_review=review["id"])], request_id="refresh")
    assert not execute_field_workflow(workflow.db, "owner", "field", "review_coupled_state", dict(calculation_id=refreshed["id"]))["linked_measurements"][0]["needs_review"]
    next_plan = workflow.continue_coupled_water("owner", "field", refreshed["id"], weather, days, "Next", request_id="next")
    ObservationReviews(workflow.db).review("owner", "field", "soil", "rejected", "Sensor problem", "Synthetic",
        request_id="reject", expected_revision=review["id"])
    with pytest.raises(ValueError, match="ancestor"):
        remove(workflow, next_plan)
    restored = remove(workflow, refreshed)
    assert "coupled_state_evidence" not in restored
    assert apply(workflow, parent) == child  # Retried historical request never reapplies its state.


def test_known_initialization_reading_is_excluded_from_later_prediction_metrics(setup):
    workflow, store = setup
    observation(store, observed_at=datetime(2026, 7, 7, tzinfo=timezone.utc))
    parent = saved(workflow)
    child = apply(workflow, parent)
    same_day = compare(workflow, child, [entry(bottom_m="0.1", water_content_adjustment=0)])
    assert same_day["metrics"]["count"] == 1  # Prediction precedes adoption.
    weather, days = next_day(example())
    continued = workflow.continue_coupled_water("owner", "field", child["id"], weather, days, "Next", request_id="next")
    report = compare(workflow, continued, [entry(as_of="2026-07-07", bottom_m="0.1", water_content_adjustment=0)])
    assert report["metrics"]["count"] == 0
    assert report["comparisons"][0]["known_target_reuse_dates"] == ["2026-07-06"]


@pytest.mark.parametrize("case", ["partial", "unknown_layer", "duplicate", "missing_memory", "extra_memory", "bad_memory", "retain_false", "memory_source", "rejected", "outside_soil", "foreign", "forecast", "outside_day"])
def test_invalid_adoptions_do_not_change_any_farm_record(setup, case):
    workflow, store = setup
    opts = dict(quality="rejected") if case == "rejected" else dict(value=.9) if case == "outside_soil" else \
        dict(unit_id="field2", cycle_id="cycle2") if case == "foreign" else dict(provenance="forecast") if case == "forecast" else \
        dict(observed_at=datetime(2026, 7, 5, tzinfo=timezone.utc)) if case == "outside_day" else {}
    observation(store, **opts)
    parent = saved(workflow)
    rows, memories = [reading()], None
    if case == "partial":
        rows[0]["bottom_m"] = "0.05"
    elif case == "unknown_layer":
        rows[0]["layer_id"] = "absent"
    elif case == "duplicate":
        rows *= 2
    elif case == "missing_memory":
        memories = []
    elif case == "extra_memory":
        memories = [dict(id="other", retain=True, source="Synthetic")]
    elif case == "retain_false":
        memories = [dict(id="plot", retain=False, source="Synthetic")]
    elif case == "memory_source":
        memories = [dict(id="plot", retain=True, source="")]
    elif case == "bad_memory":
        memories = [dict(id="plot", stage_one_remaining_mm="999", active_depth_m="0.1", stage_two_start_fraction=None, source="Synthetic")]
    before = store.export("owner")
    with pytest.raises(ValueError):
        apply(workflow, parent, rows, memories)
    assert store.export("owner") == before


def test_surface_replay_requires_explicit_withdrawal_and_preserves_inherited_soil_evidence(setup):
    from agronaut_agent.tests.test_surface_water_records import inputs, record, replay
    workflow, store = setup
    observation(store)
    parent = saved(workflow, inputs())
    row = record(workflow, parent)
    child = apply(workflow, parent)
    with pytest.raises(ValueError, match="withdraw"):
        replay(workflow, child, [row])
    replayed = replay(workflow, remove(workflow, child), [row])
    assert "coupled_state_revision" not in replayed
    assert replayed["surface_water_evidence"] == [row]
    adopted = apply(workflow, replayed, request_id="readopt")
    weather, days = next_day(inputs())
    continued = workflow.continue_coupled_water("owner", "field", adopted["id"], weather, days, "Next", request_id="next")
    next_record = record(workflow, continued, dict(row["data"], day="2026-07-07"), request_id="day2")
    result = replay(workflow, continued, [next_record], request_id="replay2")
    assert result["coupled_state_evidence"] == adopted["coupled_state_evidence"]


def test_owned_scope_exports_erasure_and_stdlib_cli(tmp_path, setup):
    workflow, store = setup
    observation(store)
    parent = saved(workflow)
    child = apply(workflow, parent)
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            execute_field_workflow(workflow.db, owner, unit, "review_coupled_state", dict(calculation_id=child["id"]))
    assert child in store.export("owner")["calculations"]
    other = store.export("other")
    store.purge("owner")
    assert all(not r for r in store.export("owner").values()) and store.export("other") == other
    path, db = tmp_path/"request.json", tmp_path/"cli.sqlite3"
    def command(group, operation, data):
        path.write_text(json.dumps(data))
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", group, "--db", str(db), operation, "--input", str(path)],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    command("farm", "create", json.loads((ROOT/"examples/soil_unit.json").read_text()))
    data = example()
    data["site"]["timezone"] = data["weather"][0]["timezone"] = "Africa/Accra"
    plan = command("field", "save", dict(task="coupled_soil_water", inputs=data, request_id="base"))
    sample = command("crop", "record_measurement", dict(request_id="sample", data=dict(quantity="soil_water_content", value=.25,
        unit="m3/m3", observed_at="2026-07-06T18:00:00+00:00", source="Synthetic", provenance="measured")))
    reviewed = command("field", "apply_coupled_state", dict(calculation_id=plan["id"], readings=[reading(observation_id=sample["id"])],
        evaporation_memories=[dict(id="plot", retain=True, source="Synthetic reviewed drying history")], dataset_kind="synthetic", reason="Synthetic", request_id="adopt"))
    assert reviewed["coupled_state_revision"]["ending_checkpoint"] != plan["result"]["result"]["ending_checkpoint"]
    restored = command("field", "remove_coupled_state", dict(calculation_id=reviewed["id"], reason="Synthetic withdrawal", request_id="remove"))
    assert continuation_checkpoint(restored) == continuation_checkpoint(plan)


def test_v16_upgrade_stamps_new_continuation_semantics_and_keeps_backup(setup):
    workflow, store = setup
    observation(store)
    parent = saved(workflow)
    path, before = workflow.db.path, store.export("owner")
    workflow.db.execute("PRAGMA user_version=16")
    workflow.db._conn.close()
    reopened = _Db(path)
    assert reopened.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    assert FarmStore(reopened).export("owner") == before
    backup = next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    prior = sqlite3.connect(backup)
    assert prior.execute("PRAGMA user_version").fetchone()[0] == 16
    assert json.loads(prior.execute("SELECT payload FROM field_calculations WHERE id=?", (parent["id"],)).fetchone()[0]) == parent
    prior.close()
    reopened._conn.close()
