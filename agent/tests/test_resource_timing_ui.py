"""Timed alternatives through the real no-model resource page."""

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import setup
from agent.tests.test_resource_ui import SCRIPT, click
from agro_model.tests.test_resource_timing import scenario


def start(tmp_path):
    brain, field, _ = setup(tmp_path)
    hydro = brain.create_unit("web", "operator", dict(farm=dict(id=field["farm_id"]), unit=dict(name="Hydro",
        production_system="hydroponics", medium="water", environment="greenhouse", water_strategy="fully_irrigated")))
    plan, timing = scenario()
    plan["jobs"][0]["unit_id"], plan["jobs"][1]["unit_id"] = field["id"], hydro["id"]
    parent = brain.farm_resources("web", "operator", field["farm_id"], "save", dict(data=plan, request_id="allocation"))
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    at.selectbox(key="resource_farm").set_value(field["farm_id"]).run(timeout=15)
    at.selectbox(key="resource_saved").set_value(parent["id"]).run(timeout=15)
    assert not at.exception
    return at, brain, field, parent, timing


def fill(at, timing):
    for work in timing["work"]:
        prefix = "timing_work_new_"
        for key in ("id", "start", "end", "source"):
            at.text_input(key=prefix+key).set_value(work[key])
        at.selectbox(key=prefix+"job_id").set_value(work["job_id"])
        at.number_input(key=prefix+"blocks").set_value(work["blocks"])
        for key, amount in work["concurrent"].items():
            at.number_input(key=prefix+"units_"+key).set_value(amount)
        click(at, "Keep work interval in draft")
    for pool in timing["storage"]:
        prefix = "timing_storage_"+pool["resource_id"]+"_"
        for key in ("capacity_l", "initial_l", "minimum_l", "source"):
            at.text_input(key=prefix+key).set_value(pool[key])
        click(at, "Keep "+pool["resource_id"]+" storage state")
    for pool in timing["availability"]:
        prefix = "timing_availability:"+pool["resource_id"]+"_new_"
        window = pool["windows"][0]
        for key in ("id", "start", "end", "source"):
            at.text_input(key=prefix+key).set_value(window[key])
        at.number_input(key=prefix+"units").set_value(window["units"])
        click(at, "Keep availability for "+pool["resource_id"]+" in draft")
    at.text_input(key="timing_source").set_value(timing["source"])


def test_timing_forms_save_success_and_conflict_as_distinct_alternatives(tmp_path):
    at, brain, field, parent, timing = start(tmp_path)
    fill(at, timing)
    click(at, "Evaluate timing checks")
    assert not at.error
    assert at.session_state["timing_result"]["timing_result"]["declared_checks_pass"]
    assert len(brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]) == 1
    click(at, "Save timed scenario")
    assert not at.error
    success = at.session_state["timing_result"]["id"]
    at.selectbox(key="timing_choice_work").set_value("greenhouse").run(timeout=15)
    at.text_input(key="timing_work_greenhouse_start").set_value("2026-10-20T08:00:00Z")
    at.text_input(key="timing_work_greenhouse_end").set_value("2026-10-20T09:00:00Z")
    click(at, "Keep work interval in draft")
    click(at, "Save timed scenario")
    assert not at.error
    failure = at.session_state["timing_result"]["id"]
    assert failure != success
    assert not at.session_state["timing_result"]["timing_result"]["declared_checks_pass"]
    at.multiselect(key="resource_compare").set_value([success, failure]).run(timeout=15)
    assert not at.exception and not at.error
    table = next(frame.value for frame in at.dataframe if "timing_status" in frame.value.columns)
    assert list(table["timing_checks"]) == [True, False]
    at.selectbox(key="resource_saved").set_value(success).run(timeout=15)
    assert not at.exception
    assert at.session_state["timing_rows"]["work"]["greenhouse"]["start"] == "2026-10-20T09:00:00Z"
    assert brain._farms.export(parent["owner_id"])["water_days"] == []


def test_late_inflow_fails_then_explicit_flow_edit_removes_deficit(tmp_path):
    at, _, _, _, timing = start(tmp_path)
    timing["storage"][0]["initial_l"] = "500"
    fill(at, timing)
    prefix = "timing_inflows:tank_new_"
    for key, value in dict(id="refill", start="2026-10-20T12:00:00Z", end="2026-10-20T13:00:00Z", volume_l="500", source="Synthetic refill").items():
        at.text_input(key=prefix+key).set_value(value)
    click(at, "Keep inflow for tank in draft")
    click(at, "Evaluate timing checks")
    assert not at.error
    result = at.session_state["timing_result"]["timing_result"]
    assert result["storage"][0]["lowest_l"]["exact"] == "-500/1"
    assert not result["declared_checks_pass"]
    at.selectbox(key="timing_choice_inflows:tank").set_value("refill").run(timeout=15)
    at.text_input(key="timing_inflows:tank_refill_start").set_value("2026-10-20T07:00:00Z")
    at.text_input(key="timing_inflows:tank_refill_end").set_value("2026-10-20T08:00:00Z")
    click(at, "Keep inflow for tank in draft")
    click(at, "Evaluate timing checks")
    assert not at.error
    assert at.session_state["timing_result"]["timing_result"]["declared_checks_pass"]


def test_missing_timing_stays_unknown_and_switching_farms_clears_it(tmp_path):
    at, brain, field, _, _ = start(tmp_path)
    at.text_input(key="timing_source").set_value("Incomplete explicit draft")
    click(at, "Save timed scenario")
    assert at.error
    assert len(brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]) == 1
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert "timing_rows" not in at.session_state and "timing_result" not in at.session_state
