"""Real resource-page forms propose, save, reload and scope scheduling inputs."""

from agent.tests.test_resource_timing_ui import fill, start
from agent.tests.test_resource_ui import click
from agro_model.tests.test_resource_schedule import request


def fill_schedule(at, timing):
    fill(at, timing)
    data = request(timing)
    for row in data["runs"]:
        prefix = "timing_runs_new_"
        for key in ("id", "source"):
            at.text_input(key=prefix+key).set_value(row[key])
        at.selectbox(key=prefix+"job_id").set_value(row["job_id"])
        for key in ("blocks", "duration_seconds"):
            at.number_input(key=prefix+key).set_value(row[key])
        for key, value in row["concurrent"].items():
            at.number_input(key=prefix+"units_"+key).set_value(value)
        click(at, "Keep run in draft")
        prefix = "timing_run_windows:"+row["id"]+"_new_"
        at.text_input(key=prefix+"id").set_value("permitted")
        for key, value in row["windows"][0].items():
            at.text_input(key=prefix+key).set_value(value)
        click(at, "Keep permitted window for "+row["id"]+" in draft")
    at.number_input(key="timing_schedule_step_seconds").set_value(3600)
    at.number_input(key="timing_schedule_search_limit").set_value(100)
    at.text_input(key="timing_schedule_source").set_value(data["source"])


def test_propose_save_reload_compare_and_reuse_supply_without_actual_records(tmp_path):
    at, brain, field, parent, timing = start(tmp_path)
    fill_schedule(at, timing)
    click(at, "Propose start times")
    assert not at.error and not at.exception
    result = at.session_state["timing_schedule_result"]
    assert result["timing_result"]["declared_checks_pass"]
    assert len(brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]) == 1
    click(at, "Save scheduling scenario")
    assert not at.error and not at.exception
    saved = at.session_state["timing_schedule_result"]
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    assert not at.exception
    assert at.session_state["timing_schedule_step_seconds"] == 3600
    assert at.session_state["timing_rows"]["runs"]["field"]["duration_seconds"] == 3600
    assert at.session_state["timing_rows"]["run_windows:field"]["1"]["start"] == "2026-10-20T08:00:00Z"
    click(at, "Propose start times")
    assert at.session_state["timing_schedule_result"]["timing_inputs"] == saved["timing_inputs"]
    at.multiselect(key="resource_compare").set_value([parent["id"], saved["id"]]).run(timeout=15)
    assert not at.exception
    table = next(f.value for f in at.dataframe if "scheduling_status" in f.value.columns)
    assert list(table["scheduling_status"]) == ["not proposed", "feasible_schedule_found"]
    assert brain._farms.export(parent["owner_id"])["water_days"] == []


def test_limited_search_saved_and_reloaded_without_claiming_infeasibility(tmp_path):
    at, _, _, _, timing = start(tmp_path)
    fill_schedule(at, timing)
    at.number_input(key="timing_schedule_search_limit").set_value(1)
    click(at, "Save scheduling scenario")
    assert not at.error and not at.exception
    limited = at.session_state["timing_schedule_result"]
    assert limited["schedule_result"]["status"] == "search_limit_reached"
    assert any("still unknown" in w.value for w in at.warning)
    assert "timing_inputs" not in limited
    at.selectbox(key="resource_saved").set_value(limited["id"]).run(timeout=15)
    assert not at.exception
    assert at.session_state["timing_storage"]["tank"]["initial_l"] == "1000"
    at.number_input(key="timing_schedule_search_limit").set_value(100)
    click(at, "Propose start times")
    assert at.session_state["timing_schedule_result"]["timing_result"]["declared_checks_pass"]


def test_supply_edits_clear_results_and_farm_switch_discards_scheduler_draft(tmp_path):
    at, brain, field, _, timing = start(tmp_path)
    fill_schedule(at, timing)
    click(at, "Propose start times")
    at.text_input(key="timing_storage_tank_initial_l").set_value("500")
    click(at, "Keep tank storage state")
    assert "timing_schedule_result" not in at.session_state
    click(at, "Save scheduling scenario")
    assert not at.error and not at.exception
    assert at.session_state["timing_schedule_result"]["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert "timing_schedule_result" not in at.session_state and "timing_rows" not in at.session_state


def add_dependency(at, bucket, label, predecessor="greenhouse", successor="field", gap=0):
    prefix = "timing_"+bucket+"_new_"
    at.text_input(key=prefix+"id").set_value("prepare-first")
    at.selectbox(key=prefix+"predecessor").set_value(predecessor)
    at.selectbox(key=prefix+"successor").set_value(successor)
    at.number_input(key=prefix+"min_gap_seconds").set_value(gap)
    at.text_input(key=prefix+"source").set_value("Synthetic required order")
    click(at, "Keep "+label+" in draft")


def test_dependency_schedule_reloads_into_both_editors_and_manual_violation_is_visible(tmp_path):
    at, brain, field, _, timing = start(tmp_path)
    fill_schedule(at, timing)
    add_dependency(at, "schedule_dependencies", "run dependency")
    click(at, "Save scheduling scenario")
    assert not at.error and not at.exception
    saved = at.session_state["timing_schedule_result"]
    assert saved["timing_result"]["dependencies"][0]["satisfied"]
    assert saved["timing_inputs"]["work"][0]["id"] == "greenhouse"
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    assert at.session_state["timing_rows"]["dependencies"] == at.session_state["timing_rows"]["schedule_dependencies"]
    # Reverse the two manually supplied intervals, leaving all capacities valid.
    for key, hour in (("field", 8), ("greenhouse", 9)):
        at.selectbox(key="timing_choice_work").set_value(key).run(timeout=15)
        at.text_input(key=f"timing_work_{key}_start").set_value(f"2026-10-20T{hour:02d}:00:00Z")
        at.text_input(key=f"timing_work_{key}_end").set_value(f"2026-10-20T{hour+1:02d}:00:00Z")
        click(at, "Keep work interval in draft")
    click(at, "Save timed scenario")
    assert not at.error and not at.exception
    failed = at.session_state["timing_result"]
    assert {i["code"] for i in failed["timing_result"]["issues"]} == {"dependency_not_satisfied"}
    audit = next(frame.value for frame in at.dataframe if "actual_gap_seconds" in frame.value.columns and "satisfied" in frame.value.columns
                 and not frame.value["satisfied"].all())
    assert audit.iloc[0]["actual_gap_seconds"] == -7200
    assert brain.farm_resources("web", "operator", field["farm_id"], "get", dict(plan_id=saved["id"]))["timing_result"]["declared_checks_pass"]
    at.selectbox(key="resource_saved").set_value(failed["id"]).run(timeout=15)
    assert not at.exception
    assert at.session_state["timing_rows"]["runs"] == {}
    assert at.session_state["timing_rows"]["schedule_dependencies"] == at.session_state["timing_rows"]["dependencies"]


def test_positive_wait_and_cycle_cannot_produce_a_false_success(tmp_path):
    at, _, _, _, timing = start(tmp_path)
    fill_schedule(at, timing)
    add_dependency(at, "schedule_dependencies", "run dependency", gap=1)
    click(at, "Save scheduling scenario")
    assert not at.exception and not at.error
    assert at.session_state["timing_schedule_result"]["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    failed = at.session_state["timing_schedule_result"]
    at.selectbox(key="resource_saved").set_value(failed["id"]).run(timeout=15)
    assert not at.exception
    assert at.session_state["timing_rows"]["work"] == {}
    assert at.session_state["timing_rows"]["dependencies"] == at.session_state["timing_rows"]["schedule_dependencies"]
    prefix = "timing_schedule_dependencies_new_"
    at.text_input(key=prefix+"id").set_value("reverse")
    at.selectbox(key=prefix+"predecessor").set_value("field")
    at.selectbox(key=prefix+"successor").set_value("greenhouse")
    at.number_input(key=prefix+"min_gap_seconds").set_value(0)
    at.text_input(key=prefix+"source").set_value("Synthetic reversed order")
    click(at, "Keep run dependency in draft")
    click(at, "Save scheduling scenario")
    assert not at.exception
    assert any("cycle" in error.value for error in at.error)
    assert "timing_schedule_result" not in at.session_state
