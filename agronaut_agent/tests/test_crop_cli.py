"""The local planting and harvest workflow works with only the standard library."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_stdlib_cycle_harvest_correction_and_void(tmp_path):
    db = tmp_path/"crop.sqlite3"
    path = tmp_path/"request.json"

    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args],
                                 cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)

    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("crop", "--db", str(db), name, "--input", str(path))

    command("farm", "--db", str(db), "create", "--input", str(ROOT/"examples/soil_unit.json"))
    created = operation("create", dict(crop="tomato", planted_on="2026-07-01", source="Synthetic fixture", request_id="plant"))
    cycle = created["cycle"]["id"]
    data = dict(batch="one", day="2026-07-06", quantity=500, unit="g", product="fruit",
                basis="fresh", grade="ungraded", provenance="reported", source="Synthetic fixture")
    row = operation("record_harvest", dict(cycle_id=cycle, data=data, request_id="harvest"))
    journal = operation("get", dict(cycle_id=cycle))
    assert journal["harvest_totals"][0]["quantity"] == "0.500"
    updated = operation("record_harvest", dict(cycle_id=cycle, data=dict(data, quantity=600),
                                               request_id="correction", supersedes_id=row["id"]))
    operation("void_harvest", dict(record_id=updated["id"], reason="Wrong cycle", request_id="void"))
    assert operation("get", dict(cycle_id=cycle))["harvest_totals"] == []
    assert command("crop", "--db", str(db), "list")["cycles"][0]["id"] == cycle

    contract = command("crop", "--db", str(db), "describe")
    assert "record_scouting" in contract["operations"]
    observation = dict(entry="sample-1", day="2026-07-06", location="Row 1", source="Synthetic fixture", provenance="reported")
    stage = operation("record_stage", dict(cycle_id=cycle, request_id="stage", data=dict(observation, stage="flowering")))
    scouting = dict(observation, symptoms="yellow leaf margins", inspected_count=10, affected_count=2)
    sample = operation("record_scouting", dict(cycle_id=cycle, request_id="scout", data=scouting))
    correction = operation("record_scouting", dict(cycle_id=cycle, request_id="scout-correct",
        supersedes_id=sample["id"], data=dict(scouting, affected_count=3)))
    operation("void_observation", dict(record_id=correction["id"], reason="Wrong sample", request_id="scout-void"))
    journal = operation("get", dict(cycle_id=cycle))
    assert journal["latest_stage_reports"][0]["id"] == stage["id"]
    assert journal["records"][-1]["voided"]

    closed = operation("close_cycle", dict(cycle_id=cycle, ended_on="2026-07-10", reason="Planting finished",
        source="Synthetic notebook", request_id="close", expected_revision=None))
    assert operation("get", dict(cycle_id=cycle))["lifecycle"]["status"] == "closed"
    assert command("crop", "--db", str(db), "list")["cycles"][0]["lifecycle"]["ended_on"] == "2026-07-10"
    operation("reopen_cycle", dict(cycle_id=cycle, reason="Planting continues", source="Synthetic correction",
        request_id="reopen", expected_revision=closed["id"]))
    assert operation("get", dict(cycle_id=cycle))["lifecycle"]["status"] == "open"

    # Seed an imported typed observation, then review it using the standard-library CLI.
    from datetime import datetime, timezone

    from agro_model.farm import Observation
    from agronaut_agent.farm_store import FarmStore
    from agronaut_agent.store import _Db

    connection = _Db(db)
    owner, unit = created["record"]["owner_id"], created["record"]["unit_id"]
    at = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    FarmStore(connection).record_observation(owner, Observation("gauge", owner, unit, cycle,
        "rainfall", 5, "mm", at, at, "reported", "Synthetic fixture"))
    connection._conn.close()
    assert command("crop", "--db", str(db), "list_measurements")["measurements"][0]["effective_quality"] == "unverified"
    operation("review_measurement", dict(observation_id="gauge", quality="rejected", reason="Wrong date",
        source="Synthetic notebook", request_id="quality-review", expected_revision=None))
    measured = operation("get", dict(cycle_id=cycle))["measurements"][0]
    assert measured["quality"] == "unverified" and measured["effective_quality"] == "rejected"

    season = operation("season_review", dict(cycle_id=cycle))
    assert season["journal"]["cycle"]["id"] == cycle
    assert season["journal"]["measurements"][0]["effective_quality"] == "rejected"
    assert season["comparison"] is None
    assert season["water"]["coverage"]["recorded_days"] == 0
    assert season["water"]["totals"] == []

    # Actual mineral inputs are separate from a nutrient budget, including preplant work.
    from agronaut_agent.tests.test_fertilizer_records import application

    actual = operation("record_fertilizer", dict(cycle_id=cycle, request_id="fertilizer",
        data=application(day="2026-06-30", phase="preplant")))
    assert actual["accounting"]["nutrients"]["n"]["declared_total_kg"] == 35
    season = operation("season_review", dict(cycle_id=cycle))
    assert season["journal"]["fertilizer"]["groups"][0]["applications"] == 1
    operation("void_fertilizer", dict(record_id=actual["id"], reason="Wrong notebook", request_id="fertilizer-void"))
    assert operation("get", dict(cycle_id=cycle))["fertilizer"]["groups"] == []

    from agronaut_agent.tests.test_crop_establishment import transplant

    before = operation("get", dict(cycle_id=cycle))
    amended = operation("correct_planting", dict(cycle_id=cycle, crop="pepper", planted_on="2026-07-02",
        reason="Synthetic transcription correction", source="Synthetic notebook", request_id="amend",
        expected_revision=before["planting"]["revision_id"]))
    assert amended["previous_cycle"] == before["cycle"]
    assert operation("get", dict(cycle_id=cycle))["cycle"]["crop"] == "pepper"
    transfer = operation("record_transplant", dict(cycle_id=cycle, data=transplant(), request_id="transplant"))
    assert transfer["data"]["plant_count"] == 120
    operation("void_transplant", dict(record_id=transfer["id"], reason="Wrong batch", request_id="transplant-void"))
    assert operation("get", dict(cycle_id=cycle))["records"][-1]["voided"]

    from agronaut_agent.tests.test_cash_records import cash

    receipt = operation("record_cash", dict(cycle_id=cycle, data=cash(amount="0.10"), request_id="cash"))
    second_cycle = operation("create", dict(crop="lettuce", planted_on="2026-08-01", source="Synthetic", request_id="second-plant"))["cycle"]["id"]
    compared = operation("compare_seasons", dict(cycle_ids=[cycle, second_cycle]))
    assert compared["seasons"][0]["cash"]["totals"][0]["recorded_net_cash"] == "-0.10"
    assert compared["seasons"][1]["cash"]["totals"] == []
    operation("void_cash", dict(record_id=receipt["id"], reason="Duplicate", request_id="cash-void"))
    assert operation("get", dict(cycle_id=cycle))["cash"]["totals"] == []

    from agronaut_agent.tests.test_crop_calendar import task

    planned = operation("plan_task", dict(cycle_id=cycle, data=task(activity="stage_check"), request_id="task"))
    complete = operation("resolve_task", dict(record_id=planned["id"], status="completed", completed_on="2026-07-06",
        completion_record=dict(kind="crop_record", id=stage["id"]), reason="Inspection done", source="Synthetic work log", request_id="complete"))
    assert complete["data"]["resolution"]["completion_record"]["snapshot"] == stage
    calendar = operation("get", dict(cycle_id=cycle))["calendar"]
    assert calendar["counts"]["completed"] == 1
    assert operation("get", dict(cycle_id=cycle))["harvest_totals"] == []
    compared = operation("compare_seasons", dict(cycle_ids=[cycle, second_cycle]))
    assert compared["seasons"][0]["calendar"]["counts"]["completed"] == 1
    assert compared["seasons"][1]["calendar"]["tasks"] == []
