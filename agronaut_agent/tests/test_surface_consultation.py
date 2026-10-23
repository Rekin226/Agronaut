"""Surface-event questions preserve timing, units and explicit measurement gaps."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.store import _Db
from agronaut_agent.surface_io import evaluate_surface_request
from agronaut_agent.tests.test_field_workflow import setup as setup

ROOT = Path(__file__).resolve().parents[2]


def example(method="border", kind="scenario"):
    data = json.loads((ROOT / "examples/surface_event.json").read_text())
    data["site"]["name"] = "Field"
    data["geometry"]["method"] = method
    data["event"]["kind"] = kind
    if method == "furrow":
        data["geometry"]["width_m"] = 1
        data["infiltration"].update(basis="volume_per_length_m2", steady_rate=.0005)
        for key in ("inflow", "tailwater"):
            for interval in data[key]:
                interval["mean_lps"] /= 10
    return data


def answers(data=None):
    data = example() if data is None else data
    result = dict(task="surface_evaluation", cycle_id="1")
    def add(prefix, record):
        for key, value in record.items():
            if key == "balance_tolerance_fraction":
                value *= 100
            result[f"{prefix}.{key}"] = "yes" if value is True else "unknown" if value is None else str(value)
    for key in ("geometry", "event", "infiltration", "assessment"):
        add(key, data[key])
    for key in ("stations", "inflow", "tailwater"):
        result[key + "_count"] = str(len(data[key]))
        for i, record in enumerate(data[key]):
            add(f"{key}.{i}", record)
    return result


def advance(wizard, data=None, target=None, **changes):
    supplied = dict(answers(data), **changes)
    view = wizard.start("owner", "field")
    while view["next_field"] != target:
        view = wizard.answer("owner", "field", supplied[view["next_field"]], view["revision"])
    return view


@pytest.mark.parametrize("method", ["furrow", "border", "basin"])
@pytest.mark.parametrize("kind", ["scenario", "observed"])
def test_complete_surface_intake_preserves_native_inputs_and_saves_evaluation_only(setup, method, kind):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    data = example(method, kind)
    view = advance(wizard, data)
    assert view["inputs"] == data
    assert view["preview"]["result"] == evaluate_surface_request(data)
    result = view["preview"]["result"]
    assert result["volumes"]["inflow_l"] == (5400 if method == "furrow" else 54000)
    assert result["water_balance_accepted"]
    assert "No cutoff-time recommendation" in render_intake(view)
    assert workflow.list("owner", "field")["calculations"] == []
    saved = wizard.save("owner", "field", view["draft_id"], view["revision"])
    assert saved["calculation"]["task"] == "surface_evaluation"
    assert saved["calculation"]["cycle_id"] == "cycle"
    assert saved["calculation"]["inputs"] == data
    assert saved["calculation"]["result"] == view["preview"]
    assert wizard.save("owner", "field", view["draft_id"], view["revision"]) == saved
    assert "No daily water log or equipment operation" in render_intake(saved)
    assert workflow.list("owner", "field")["water_days"] == []


@pytest.mark.parametrize("key,value", [
    ("geometry.method", "drip"), ("geometry.length_m", "nan"), ("geometry.slope_m_m", "1"),
    ("event.kind", "forecast"), ("event.started_at", "2026-07-06T08:00:00"),
    ("event.started_at", "2026-06-30T08:00:00+00:00"), ("event.cutoff_min", "121"),
    ("event.rainfall_mm", "1"), ("event.initial_surface_water_l", "1"), ("event.surface_drained", "no"),
    ("infiltration.basis", "volume_per_length_m2"), ("infiltration.exponent", "0"),
    ("infiltration.k", "unknown"), ("infiltration.min_opportunity_min", "121"),
    ("stations_count", "1"), ("stations_count", "2.5"), ("stations.0.distance_m", "1"),
    ("stations.0.advance_min", "1"), ("stations.0.recession_min", "121"),
    ("stations.1.distance_m", "100"), ("stations.2.distance_m", "99"), ("stations.2.advance_min", "29"),
    ("inflow_count", "1"), ("inflow.0.start_min", "1"), ("inflow.0.end_min", "89"),
    ("inflow.0.end_min", "91"), ("inflow.0.mean_lps", "0"), ("inflow.1.start_min", "91"),
    ("inflow.1.end_min", "119"), ("inflow.1.mean_lps", "1"), ("tailwater.0.mean_lps", "1"),
    ("tailwater.1.mean_lps", "unknown"), ("assessment.root_zone_deficit_mm", "-1"),
    ("assessment.balance_tolerance_fraction", "21"), ("assessment.source", ""),
])
def test_invalid_answers_do_not_advance_or_fill_gaps(setup, key, value):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, target=key)
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", value, view["revision"])
    assert wizard.get("owner", "field") == view


def test_opportunity_range_is_checked_at_station_recession(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    data = example()
    data["infiltration"]["min_opportunity_min"] = 30
    data["infiltration"]["max_opportunity_min"] = 100
    view = advance(wizard, data, "stations.0.recession_min")
    with pytest.raises(ValueError, match="opportunity"):
        wizard.answer("owner", "field", "120", view["revision"])
    assert wizard.get("owner", "field") == view
    accepted = wizard.answer("owner", "field", "100", view["revision"])
    assert accepted["next_field"] == "stations.0.source"


def test_missing_deficit_is_unknown_and_failed_accounting_withholds_performance(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    data = example()
    data["assessment"]["root_zone_deficit_mm"] = None
    ready = advance(wizard, data)
    assert ready["inputs"]["assessment"]["root_zone_deficit_mm"] is None
    assert ready["preview"]["result"]["performance"]["application_efficiency"] is None
    wizard.cancel("owner", "field", ready["revision"])
    data["inflow"][0]["mean_lps"] = 5
    ready = advance(wizard, data)
    assert ready["preview"]["result"]["performance"] is None
    assert "REVIEW — performance withheld" in render_intake(ready)
    saved = wizard.save("owner", "field", ready["draft_id"], ready["revision"])
    assert not saved["calculation"]["result"]["result"]["water_balance_accepted"]
    assert workflow.list("owner", "field")["water_days"] == []


def test_observed_event_must_be_complete_and_future_scenario_stays_scenario(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    data = example(kind="observed")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    view = advance(wizard, data, "event.started_at")
    with pytest.raises(ValueError, match="completed event"):
        wizard.answer("owner", "field", future, view["revision"])
    just_started = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    view = wizard.answer("owner", "field", just_started, view["revision"])
    with pytest.raises(ValueError, match="finished"):
        wizard.answer("owner", "field", "120", view["revision"])
    assert wizard.get("owner", "field") == view
    wizard.cancel("owner", "field", view["revision"])
    data["event"].update(kind="scenario", started_at=future)
    ready = advance(wizard, data)
    assert ready["preview"]["result"]["event_kind"] == "scenario"
    wizard.save("owner", "field", ready["draft_id"], ready["revision"])


def test_elapsed_minutes_cross_dst_without_reinterpreting_offset(setup):
    workflow, _ = setup
    workflow.db.execute("UPDATE farms SET timezone='Europe/Berlin' WHERE id='farm'")
    wizard = FieldConsultation(workflow.db)
    data = example()
    data["site"]["timezone"] = "Europe/Berlin"
    data["event"]["started_at"] = "2026-10-25T02:30:00+02:00"
    ready = advance(wizard, data)
    assert ready["preview"]["result"]["event_ended_at"] == "2026-10-25T02:30:00+00:00"
    saved = wizard.save("owner", "field", ready["draft_id"], ready["revision"])
    assert saved["calculation"]["inputs"] == data


def test_current_cycle_rechecked_on_save(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    ready = advance(wizard)
    CropRecords(workflow.db).correct_planting("owner", "field", "cycle", "tomato", "2026-07-07", "Fixture",
                                            "Correct planting", request_id="planting", expected_revision=None)
    with pytest.raises(ValueError, match="precedes"):
        wizard.save("owner", "field", ready["draft_id"], ready["revision"])
    assert wizard.get("owner", "field") == ready


def test_restart_backtracking_and_stale_answers_preserve_intervals(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, target="tailwater.1.mean_lps")
    other = _Db(workflow.db.path)
    try:
        resumed = FieldConsultation(other)
        assert resumed.start("owner", "field") == view
        next_view = resumed.answer("owner", "field", "3", view["revision"])
        with pytest.raises(ValueError, match="changed"):
            wizard.answer("owner", "field", "2.5", view["revision"])
        back = resumed.answer("owner", "field", "", next_view["revision"], back=True)
        assert "tailwater.1.mean_lps" not in back["answers"]
        ready = advance(resumed)
        assert ready["inputs"] == example()
    finally:
        other._conn.close()


def test_surface_drafts_export_and_purge_with_owned_unit(setup):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, target="stations.1.source")
    with pytest.raises(ValueError):
        wizard.get("other", "field")
    exported = farms.export("owner")["field_intakes"][0]
    assert exported["data"] == view["answers"]
    other = wizard.start("other", "field3")
    farms.purge("owner")
    assert not wizard.active("owner", "field")
    assert wizard.get("other", "field3") == other


def test_previous_draft_version_keeps_its_original_choices(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    wizard.start("owner", "field")
    payload = json.loads(workflow.db.query("SELECT payload FROM field_intake_drafts WHERE unit_id='field'")[0][0])
    payload["intake_version"] = 4
    workflow.db.execute("UPDATE field_intake_drafts SET payload=? WHERE unit_id='field'", (json.dumps(payload),))
    view = wizard.get("owner", "field")
    assert "Choose 5" not in view["question"]
    with pytest.raises(ValueError, match="listed"):
        wizard.answer("owner", "field", "5", view["revision"])
    assert wizard.get("owner", "field") == view


@pytest.mark.parametrize("variation", ["multiple", "cutoff_at_end", "explicit_no_tailwater"])
def test_valid_alternative_interval_counts_keep_all_supplied_records(setup, variation):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    data = example()
    if variation == "multiple":
        data["inflow"] = [dict(start_min=a, end_min=b, mean_lps=q, source="Synthetic interval")
                          for a, b, q in ((0, 30, 8), (30, 90, 11), (90, 100, 0), (100, 120, 0))]
    elif variation == "cutoff_at_end":
        data["event"]["cutoff_min"] = 120
        data["inflow"] = [dict(start_min=0, end_min=120, mean_lps=7.5, source="Synthetic full-window flow")]
    else:
        data["tailwater"] = [dict(start_min=0, end_min=120, mean_lps=0, source="Explicit no-outflow scenario")]
    ready = advance(wizard, data)
    assert ready["inputs"] == data
    assert ready["preview"]["result"] == evaluate_surface_request(data)


def test_long_station_log_does_not_duplicate_every_prior_draft(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    data = example()
    data["stations"] = [dict(distance_m=i * 100 / 99, advance_min=i * 60 / 99, recession_min=120,
                              source="Synthetic station timing for storage regression") for i in range(100)]
    ready = advance(wizard, data)
    payload = workflow.db.query("SELECT payload FROM field_intake_drafts WHERE unit_id='field'")[0][0]
    assert len(payload.encode()) < 150000  # Full snapshots of every prior answer take several MB here.
    for _ in range(7):
        ready = wizard.answer("owner", "field", "", ready["revision"], back=True)
    assert advance(wizard, data)["inputs"] == data


def test_legacy_full_snapshot_history_can_resume_edit_and_complete(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, target="geometry.length_m")
    payload = json.loads(workflow.db.query("SELECT payload FROM field_intake_drafts WHERE unit_id='field'")[0][0])
    payload.pop("history_format")
    payload["history"] = [{}, {"task": "surface_evaluation"}, {"task": "surface_evaluation", "cycle_id": "cycle"}]
    workflow.db.execute("UPDATE field_intake_drafts SET payload=? WHERE unit_id='field'", (json.dumps(payload),))
    view = wizard.answer("owner", "field", "", view["revision"], back=True)
    assert view["next_field"] == "geometry.method"
    assert view["answers"] == dict(task="surface_evaluation", cycle_id="cycle")
    assert advance(wizard)["inputs"] == example()


def test_stdlib_cli_completes_surface_event_without_model(tmp_path):
    db, path = str(tmp_path / "surface-cli.sqlite3"), tmp_path / "request.json"
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=ROOT,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    command("farm", "--db", db, "create", "--input", str(ROOT / "examples/soil_unit.json"))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path))
    supplied = answers()
    view = operation("start_intake", {})
    while view["status"] != "ready":
        view = operation("answer_intake", dict(answer=supplied[view["next_field"]], expected_revision=view["revision"]))
    saved = operation("save_intake", dict(draft_id=view["draft_id"], expected_revision=view["revision"]))["calculation"]
    expected = example()
    expected["site"] = dict(name="Tomato field", timezone="Africa/Accra")
    assert saved["inputs"] == expected
    assert operation("list", {})["water_days"] == []


@pytest.mark.parametrize("channel,method", [("web", "border"), ("cli", "furrow"), ("whatsapp", "basin")])
def test_channels_complete_surface_evaluation_without_model(tmp_path, channel, method):
    from agronaut_agent.channels.commands import dispatch
    from agronaut_agent.core import AgronautAgent
    from agronaut_agent.tests.test_unit_setup import NoModel
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path / "surface-chat.sqlite3", chat_model=NoModel())
    unit = brain.create_unit(channel, "operator", dict(farm=dict(name="Home", timezone="UTC"),
        unit=dict(name="Field", production_system="soil", environment="open_field", medium="soil",
                  water_strategy="fully_irrigated", irrigation_method="surface")))
    owner = brain._conv.get_or_create_user(channel, "operator")
    def send(message):
        if channel == "web":
            return _route_turn(brain, "operator", message, None)
        if message.startswith("/"):
            return dispatch(brain, channel, "operator", message).text
        return brain.handle_message(channel, "operator", message)
    assert "Choose 5" in send("/fieldplan")
    supplied = answers(example(method))
    view = brain._field_intake.get(owner, unit["id"])
    while view["status"] != "ready":
        reply = send(supplied[view["next_field"]])
        view = brain._field_intake.get(owner, unit["id"])
        assert view["question"] in reply
    assert "No cutoff-time recommendation" in reply
    assert brain.field_workflow(channel, "operator", "list", {})["calculations"] == []
    assert "Saved surface-event evaluation" in send("save")
    records = brain.field_workflow(channel, "operator", "list", {})
    saved = brain.field_workflow(channel, "operator", "get", dict(calculation_id=records["calculations"][0]["id"]))
    assert saved["inputs"] == example(method)
    assert records["water_days"] == []


def test_surface_draft_media_back_cancel_and_reset(tmp_path):
    from agronaut_agent.channels.commands import dispatch
    from agronaut_agent.tests.test_unit_agent import agent_and_units
    from agronaut_agent.tests.test_unit_setup import NoModel

    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit("cli", "one", "field")
    dispatch(brain, "cli", "one", "/fieldplan")
    brain.handle_message("cli", "one", "5")
    view = brain._field_intake.get(owner, "field")
    assert "text reply" in brain.handle_message("cli", "one", "border", fact_text="image of irrigation")
    assert brain._field_intake.get(owner, "field") == view
    assert "Choose 5" in dispatch(brain, "cli", "one", "/back").text
    brain.handle_message("cli", "one", "surface")
    assert "Surface-event" in dispatch(brain, "cli", "one", "/fieldplan").text
    assert "Surface-event intake cancelled" in dispatch(brain, "cli", "one", "/cancel").text
    dispatch(brain, "cli", "one", "/fieldplan")
    brain.handle_message("cli", "one", "5")
    brain.reset("cli", "one")
    assert not brain._field_intake.active(owner, "field")
