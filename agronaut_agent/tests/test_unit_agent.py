import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from agro_model.farm import Farm, ProductionUnit
from agronaut_agent import runtime
from agronaut_agent.core import AgronautAgent
from agronaut_agent.tools import schedule_followup


class Model:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.bindings = []
        self.messages = []

    def bind_tools(self, tools):
        self.bindings.append({t.name for t in tools})
        return self

    def invoke(self, messages):
        self.messages = list(messages)
        return self.replies.pop(0) if self.replies else AIMessage(content="Tell me about this crop.")


def agent_and_units(tmp_path, model=None):
    agent = AgronautAgent(db_path=tmp_path / "agent.sqlite3", chat_model=model or Model())
    owner = agent._conv.get_or_create_user("cli", "one")
    agent._farms.create_farm(Farm("farm", owner, "Home", "UTC"))
    agent._farms.create_unit(owner, ProductionUnit(
        "pond", "farm", "Pond", "aquaponics", "water", "greenhouse", "fully_irrigated"))
    agent._farms.create_unit(owner, ProductionUnit(
        "field", "farm", "Tomatoes", "soil", "soil", "open_field", "fully_irrigated", "drip"))
    agent._farms.create_unit(owner, ProductionUnit(
        "hydro", "farm", "Lettuce", "hydroponics", "water", "greenhouse", "fully_irrigated"))
    return agent, owner


def test_chat_switching_scopes_recall_history_and_questions(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    agent._mem.set_fact(owner, "fish_species", "catfish")
    agent.select_unit("cli", "one", "pond")
    pond = agent._contexts.active(owner)
    agent._mem.set_fact(pond.context_id, "fish_species", "tilapia")
    agent._mem.add_memory(pond.context_id, "Pond had ammonia spike")
    agent._mem.set_summary(pond.context_id, "Pond treatment history")
    agent._conv.append_message(pond.context_id, "user", "Unique pond conversation")
    agent.select_unit("cli", "one", "field")
    reply = agent.set_goal("cli", "one", "design")
    assert "fish" not in reply.lower()
    agent.handle_message("cli", "one", "I grow tomatoes in a field")
    content = "\n".join(str(m.content) for m in agent._base.messages)
    for text in ("tilapia", "catfish", "Pond had ammonia spike", "Unique pond conversation", "Pond treatment history"):
        assert text not in content
    assert "Tomatoes" in content
    assert "size_aquaponics_system" not in agent._base.bindings[-1]
    field = agent._contexts.active(owner)
    assert agent._conv.recent_messages(field.context_id)
    agent.select_unit("cli", "one", "pond")
    assert "tilapia" in agent.profile_text("cli", "one")
    assert agent._mem.get_facts(owner) == {"fish_species": "catfish"}


def test_soil_dispatch_blocks_a_hallucinated_aquatic_call(tmp_path):
    call = {"name": "size_aquaponics_system", "args": {"fish_species": "tilapia", "crop": "lettuce",
            "grow_area_m2": 10, "temperature_c": 27, "water_budget_lpd": 300}, "id": "bad"}
    model = Model([AIMessage(content="", tool_calls=[call]), AIMessage(content="That task is unavailable.")])
    agent, owner = agent_and_units(tmp_path, model)
    agent.select_unit("cli", "one", "field")
    agent.handle_message("cli", "one", "Calculate this field")
    results = agent.tool_results("cli", "one", "size_aquaponics_system")
    assert len(results) == 1 and results[0].startswith("TOOL_ERROR")
    assert "fish_species" not in agent._mem.get_facts(agent._contexts.active(owner).context_id)
    assert "unavailable" in agent.forecast_direct("cli", "one").lower()


@pytest.mark.parametrize("task,example", [("crop_water", "field_water.json"),
                                          ("drip_design", "drip_design.json"),
                                          ("drip_lateral", "drip_lateral.json"),
                                          ("sprinkler_design", "sprinkler_design.json"),
                                          ("surface_advance", "surface_advance.json"),
                                          ("surface_evaluation", "surface_event.json")])
def test_shared_calculator_is_available_in_soil_chat_and_cannot_cross_domain(tmp_path, task, example):
    inputs = json.loads((Path(__file__).resolve().parents[2] / "examples" / example).read_text())
    calls = [
        {"name": "calculate_agriculture", "args": {"production_system": "soil", "task": task, "inputs": inputs}, "id": "ok"},
        {"name": "calculate_agriculture", "args": {"production_system": "hydroponics", "task": "size", "inputs": {}}, "id": "wrong"},
    ]
    agent, owner = agent_and_units(tmp_path, Model([AIMessage(content="", tool_calls=calls), AIMessage(content="Computed result.")]))
    agent.select_unit("cli", "one", "field")
    agent.handle_message("cli", "one", "Use the supplied measurements")
    good, bad = [json.loads(r) for r in agent.tool_results("cli", "one", "calculate_agriculture")]
    assert good["capability"]["id"] == f"soil.{task}"
    if task in ("drip_design", "sprinkler_design"):
        assert good["result"]["feasible"]
        assert len(good["result"]["schedule"]) == 2
    assert bad["status"] == "VALIDATION_FAILED"


def test_hydroponic_consultation_never_requests_fish(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "hydro")
    agent.set_goal("cli", "one", "design")
    scope = agent._contexts.active(owner)
    agent._mem.set_facts(scope.context_id, {"location": "Home", "crop": "lettuce", "grow_area_m2": 10,
                                          "temperature_c": 26, "water_budget_lpd": 100})
    assert "fish_species" not in agent._recall_block(scope.context_id)
    assert "fish" not in agent.set_goal("cli", "one", "design").lower()


@pytest.mark.parametrize("unit", ["field", "hydro", "pond"])
def test_crop_journal_chat_tool_uses_the_selected_unit_across_crop_systems(tmp_path, unit):
    agent, _ = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", unit)
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(
        name="crop_records", id="crop-op", args=dict(operation="create", data=dict(
            crop="lettuce", planted_on="2026-07-01", source="Synthetic chat fixture", request_id="plant")))]),
        AIMessage(content="Planting saved.")]
    agent.handle_message("cli", "one", "Record this completed planting")
    result = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    assert result["cycle"]["unit_id"] == unit
    assert result["cycle"]["crop"] == "lettuce"
    assert len(agent.list_cycles("cli", "one", unit)) == 1
    cycle_id = result["cycle"]["id"]
    from agronaut_agent.tests.test_fertilizer_records import application

    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="fertilizer",
        args=dict(operation="record_fertilizer", data=dict(cycle_id=cycle_id, request_id="fertilizer", data=application())))]),
        AIMessage(content="Application checked.")]
    agent.handle_message("cli", "one", "Record these completed mineral fertilizer quantities")
    applied = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    if unit == "field":
        assert applied["unit_id"] == unit and applied["cycle_id"] == cycle_id
        assert applied["accounting"]["nutrients"]["n"]["declared_total_kg"] == 35
    else:
        assert applied["status"] == "VALIDATION_FAILED" and "soil units only" in applied["error"]
    observations = dict(entry="inspection-1", day="2026-07-06", location="Row 1",
                        source="Synthetic chat inspection", provenance="reported")
    for operation, data in (("describe", {}),
        ("record_stage", dict(cycle_id=cycle_id, request_id="stage", data=dict(observations, stage="flowering"))),
        ("record_scouting", dict(cycle_id=cycle_id, request_id="scout", data=dict(observations,
            symptoms="yellow margins", inspected_count=10, affected_count=2)))):
        agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id=operation,
            args=dict(operation=operation, data=data))]), AIMessage(content="Done.")]
        agent.handle_message("cli", "one", f"Run the requested {operation}")
        response = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
        if operation == "describe":
            assert "record_scouting" in response["operations"]
        else:
            assert response["unit_id"] == unit and response["cycle_id"] == cycle_id
            assert response["data"]["entry"] == "inspection-1"
    revision = None
    for operation in ("close_cycle", "reopen_cycle"):
        args = dict(cycle_id=cycle_id, reason="Explicit operator request", source="Synthetic fixture",
                    request_id=operation, expected_revision=revision)
        if operation == "close_cycle":
            args["ended_on"] = "2026-07-10"
        agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id=operation,
            args=dict(operation=operation, data=args))]), AIMessage(content="Cycle updated.")]
        agent.handle_message("cli", "one", f"Please {operation} for this planting")
        response = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
        assert response["unit_id"] == unit and response["cycle_id"] == cycle_id
        assert response["data"]["status"] == ("closed" if operation == "close_cycle" else "open")
        revision = response["id"]
    from datetime import datetime, timezone

    from agro_model.farm import Observation

    owner = result["record"]["owner_id"]
    at = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    agent._farms.record_observation(owner, Observation("gauge", owner, unit, cycle_id, "rainfall", 5, "mm",
                                                      at, at, "reported", "Synthetic chat fixture"))
    for operation, data in (("list_measurements", {}), ("review_measurement", dict(observation_id="gauge", quality="rejected",
        reason="Wrong timestamp", source="Synthetic notebook", request_id="review", expected_revision=None))):
        agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id=operation,
            args=dict(operation=operation, data=data))]), AIMessage(content="Reviewed.")]
        agent.handle_message("cli", "one", f"Please {operation} for this observation")
        response = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
        if operation == "list_measurements":
            assert response["measurements"][0]["unit_id"] == unit
        else:
            assert response["unit_id"] == unit and response["quality"] == "rejected"
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="season-review",
        args=dict(operation="season_review", data=dict(cycle_id=cycle_id)))]), AIMessage(content="Season reviewed.")]
    agent.handle_message("cli", "one", "Review this planting's recorded outcomes")
    season = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    assert season["unit_id"] == unit and season["journal"]["cycle"]["id"] == cycle_id
    assert season["water"]["applicable"] == (unit == "field")
    from agronaut_agent.tests.test_crop_establishment import transplant

    for operation, data in (("record_transplant", dict(cycle_id=cycle_id, request_id="transplant", data=transplant())),
        ("correct_planting", dict(cycle_id=cycle_id, crop="lettuce", planted_on="2026-07-02",
            source="Synthetic correction", reason="Wrong date", request_id="amend",
            expected_revision=result["record"]["id"]))):
        agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id=operation,
            args=dict(operation=operation, data=data))]), AIMessage(content="Saved.")]
        agent.handle_message("cli", "one", f"Please {operation} with this explicit record")
        response = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
        assert response["unit_id"] == unit and response["cycle_id"] == cycle_id
    assert agent.list_cycles("cli", "one", unit)[0]["planted_on"] == "2026-07-02"
    from agronaut_agent.tests.test_cash_records import cash

    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="cash",
        args=dict(operation="record_cash", data=dict(cycle_id=cycle_id, data=cash(), request_id="cash")))]), AIMessage(content="Saved.")]
    agent.handle_message("cli", "one", "Record this actual paid expense for this planting")
    response = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    assert response["unit_id"] == unit and response["data"]["amount"] == "12.50"
    second = agent.crop_records("cli", "one", "create", dict(crop="tomato", planted_on="2026-08-01", source="Synthetic", request_id="second"))["cycle"]["id"]
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="compare",
        args=dict(operation="compare_seasons", data=dict(cycle_ids=[cycle_id, second])))]), AIMessage(content="Compared.")]
    agent.handle_message("cli", "one", "Compare the recorded outcomes for these two plantings")
    compared = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    assert compared["seasons"][0]["cash"]["totals"][0]["recorded_expense"] == "12.50"
    assert compared["seasons"][1]["cash"]["totals"] == []
    from agronaut_agent.tests.test_crop_calendar import task

    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="task",
        args=dict(operation="plan_task", data=dict(cycle_id=cycle_id, data=task(), request_id="task")))]), AIMessage(content="Planned.")]
    agent.handle_message("cli", "one", "Plan this work window without recording any harvest")
    planned = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    assert planned["unit_id"] == unit and planned["data"]["status"] == "planned"
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="complete-task",
        args=dict(operation="resolve_task", data=dict(record_id=planned["id"], status="completed", completed_on="2026-07-06",
            reason="Operator reports work done", source="Synthetic log", request_id="complete-task")))]), AIMessage(content="Reported.")]
    agent.handle_message("cli", "one", "Mark this task complete based on my report; quantities are unknown")
    resolved = json.loads(agent.tool_results("cli", "one", "crop_records")[-1])
    assert resolved["unit_id"] == unit and resolved["data"]["status"] == "completed"
    journal = agent.crop_records("cli", "one", "get", dict(cycle_id=cycle_id))
    assert journal["calendar"]["counts"]["completed"] == 1
    assert journal["harvest_totals"] == []
    for other in {"field", "hydro", "pond"} - {unit}:
        assert agent.list_cycles("cli", "one", other) == []


@pytest.mark.parametrize("unit", ["field", "hydro", "pond"])
def test_farm_resource_chat_allocates_across_units_in_the_active_farm_only(tmp_path, unit):
    from agro_model.tests.test_resources import plan

    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", unit)
    inputs = plan()
    inputs["period"]["timezone"] = "UTC"
    inputs["jobs"][0].update(unit_id="field", cycle_id=None)
    for operation, data in (("describe", {}), ("evaluate", dict(data=inputs)), ("save", dict(data=inputs, request_id="mixed-farm"))):
        agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="farm_resources", id=operation,
            args=dict(operation=operation, data=data))]), AIMessage(content="Scenario checked.")]
        agent.handle_message("cli", "one", f"Please {operation} this explicit mixed-farm resource scenario")
        result = json.loads(agent.tool_results("cli", "one", "farm_resources")[-1])
        if operation == "describe":
            assert {u["id"] for u in result["units"]} == {"field", "hydro", "pond"}
        else:
            assert [a["allocated_blocks"] for a in result["result"]["allocations"]] == [10, 4, 1]
    assert len(agent._farms.export(owner)["resource_plans"]) == 1
    assert agent._farms.export(owner)["water_days"] == []
    assert agent._farms.export(owner)["crop_records"] == []
    agent._farms.create_farm(Farm("other-farm", owner, "Other", "UTC"))
    agent._farms.create_unit(owner, ProductionUnit("other-field", "other-farm", "Other field", "soil", "soil", "open_field", "rainfed"))
    inputs["jobs"][0]["unit_id"] = "other-field"
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="farm_resources", id="wrong-farm",
        args=dict(operation="save", data=dict(data=inputs, request_id="wrong")))]), AIMessage(content="Choose work within this farm.")]
    agent.handle_message("cli", "one", "Check this scenario")
    rejected = json.loads(agent.tool_results("cli", "one", "farm_resources")[-1])
    assert rejected["status"] == "VALIDATION_FAILED"
    assert len(agent._farms.export(owner)["resource_plans"]) == 1


@pytest.mark.parametrize("unit", ["field", "hydro", "pond"])
def test_farm_resource_chat_checks_and_saves_timed_alternatives(tmp_path, unit):
    from agro_model.tests.test_resource_timing import scenario

    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", unit)
    plan, timing = scenario()
    plan["jobs"][0]["unit_id"] = "field"
    parent = agent.farm_resources("cli", "one", "farm", "save", dict(data=plan, request_id="allocation"))
    for operation in ("evaluate_timing", "save_timed"):
        data = dict(plan_id=parent["id"], data=timing)
        if operation == "save_timed":
            data["request_id"] = "timed"
        agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="farm_resources", id=operation,
            args=dict(operation=operation, data=data))]), AIMessage(content="Declared timing checks pass.")]
        agent.handle_message("cli", "one", f"Please {operation} with these supplied intervals")
        result = json.loads(agent.tool_results("cli", "one", "farm_resources")[-1])
        assert result["timing_result"]["declared_checks_pass"]
    exported = agent._farms.export(owner)
    assert len(exported["resource_plans"]) == 2
    assert exported["water_days"] == exported["crop_records"] == []


def test_soil_chat_saves_logs_and_replays_without_turning_plans_into_actions(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    payload = json.loads((Path(__file__).resolve().parents[2]/"examples/field_water.json").read_text())
    payload["site"]["timezone"] = "UTC"
    payload["weather"][0]["kind"] = "observed"

    def invoke(operation, data):
        agent._base.replies = [AIMessage(content="", tool_calls=[{
            "name": "field_records", "id": "field-op", "args": {"operation": operation, "data": data}}]),
            AIMessage(content="Saved to this field.")]
        agent.handle_message("cli", "one", "Save the supplied field record")
        return json.loads(agent.tool_results("cli", "one", "field_records")[-1])

    before = invoke("save", dict(task="root_zone_budget", inputs=payload, request_id="initial"))
    assert agent.field_workflow("cli", "one", "list", {})["water_days"] == []
    logged = invoke("record_day", dict(request_id="daily", data=dict(day="2026-07-06",
        rainfall_mm=10, net_irrigation_mm=0, runoff_mm=0, source="Synthetic chat fixture",
        provenance="reported", quality="unverified")))
    after = invoke("replay", dict(calculation_id=before["id"], water_record_ids=[logged["id"]], request_id="replay"))
    assert after["parent_id"] == before["id"]
    assert after["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 10
    revised = invoke("revise_weather", dict(calculation_id=after["id"], request_id="weather",
                     weather=[dict(payload["weather"][0], net_radiation_mj_m2=16)]))
    assert revised["parent_id"] == after["id"]
    assert revised["inputs"]["weather"][0]["net_radiation_mj_m2"] == 16
    assert revised["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 10
    assert revised["applied_water_record_ids"] == [logged["id"]]
    export = agent.export_user_data("cli", "one")
    assert len(export["farms"]["calculations"]) == 3
    assert len(export["farms"]["water_days"]) == 1
    agent.select_unit("cli", "one", "hydro")
    agent._base.replies = []
    agent.handle_message("cli", "one", "Check this hydroponic unit")
    assert "field_records" not in agent._base.bindings[-1]


def test_scoped_followup_uses_real_delivery_identity_and_waits_for_its_unit(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    scope = agent._contexts.active(owner)
    runtime.set_current(agent._mem, scope.context_id, agent._followups,
                        owner_id=owner, unit_context=scope)
    try:
        schedule_followup.invoke({"question": "Did you measure flow?", "hours": 1})
    finally:
        runtime.clear_current()
    row = agent._followups.open_for(scope.context_id)
    assert row["channel"] == "cli" and row["channel_user"] == "one"
    agent._farms.db.execute("UPDATE followups SET due_at='2000-01-01' WHERE id=?", (row["id"],))
    assert "Tomatoes" in agent.due_followups("cli")[0]["question"]
    agent.select_unit("cli", "one", "pond")
    assert agent.due_followups("cli") == []


def test_owner_cannot_select_someone_elses_unit(tmp_path):
    agent, _ = agent_and_units(tmp_path)
    with pytest.raises(ValueError):
        agent.select_unit("cli", "other", "field")


def test_reset_is_unit_scoped_and_forget_reaches_every_unit(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    scopes = []
    for name in ("field", "pond"):
        agent.select_unit("cli", "one", name)
        scope = agent._contexts.active(owner)
        scopes.append(scope)
        agent._mem.set_fact(scope.context_id, "location", name)
        agent._conv.append_message(scope.context_id, "user", name)
    agent.reset("cli", "one")
    assert agent._conv.recent_messages(scopes[1].context_id) == []
    assert agent._conv.recent_messages(scopes[0].context_id)
    agent.forget_everything("cli", "one")
    for scope in scopes:
        assert agent._mem.get_facts(scope.context_id) == {}
        assert agent._conv.recent_messages(scope.context_id) == []


def test_knowledge_search_stays_in_the_active_domain(tmp_path, monkeypatch):
    from agronaut_agent import rag
    from agronaut_agent.tools import search_knowledge_base

    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    unit = agent._contexts.active(owner)
    observed = []
    monkeypatch.setattr(rag, "search_with_stats", lambda query, **kwargs: (
        observed.append(kwargs) or "No matching source", {}))
    runtime.set_current(agent._mem, unit.context_id, owner_id=owner, unit_context=unit)
    try:
        assert search_knowledge_base.invoke({"query": "soil water"}) == "No matching source"
    finally:
        runtime.clear_current()
    assert observed == [{"filters": {"domain": ["soil", "irrigation"]}}]


def test_profile_tool_does_not_save_fish_or_aquatic_ph_in_a_soil_field(tmp_path):
    from agronaut_agent.tools import update_profile

    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    unit = agent._contexts.active(owner)
    runtime.set_current(agent._mem, unit.context_id, owner_id=owner, unit_context=unit)
    try:
        update_profile.invoke({"updates": {"fish_species": "tilapia", "ph": 6.5, "soil_ph": 6.5,
                                            "crop": "maize", "crop_stage": "flowering"}})
    finally:
        runtime.clear_current()
    assert agent._mem.get_facts(unit.context_id) == {"soil_ph": "6.5", "crop": "maize", "crop_stage": "flowering"}


def test_unit_commands_reach_the_agent_without_a_model_call(tmp_path):
    from agronaut_agent.channels.commands import dispatch

    agent, owner = agent_and_units(tmp_path)
    assert "field" in dispatch(agent, "cli", "one", "/units").text
    assert "Tomatoes" in dispatch(agent, "cli", "one", "/unit field").text
    assert agent._contexts.active(owner).unit_id == "field"
    assert agent._base.messages == []


def test_selecting_a_legacy_unit_keeps_the_latest_twin_facts(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    agent._farms.db.execute("UPDATE production_units SET legacy=1 WHERE id='pond'")
    agent._mem.set_fact(owner, "tank_volume_l", 1200)
    agent._farms.db.execute("INSERT INTO unit_facts VALUES(?,?,?,?,?)", ("pond", "tank_volume_l", "500", "reported", "2026-01-01"))
    agent._conv.append_message(owner, "user", "Old conversation whose unit is uncertain")
    agent.select_unit("cli", "one", "pond")
    selected = agent._contexts.active(owner)
    assert agent._mem.get_facts(selected.context_id)["tank_volume_l"] == "1200"
    assert agent._conv.recent_messages(selected.context_id) == []


def test_switch_waits_for_an_in_flight_photo_and_photo_history_stays_in_its_field(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    field = agent._contexts.active(owner)
    started, release, switching = Event(), Event(), Event()

    def describe(*args):
        started.set()
        assert release.wait(3)
        return "The lower leaves are yellow."

    def select_pond():
        switching.set()
        return agent.select_unit("cli", "one", "pond")

    agent._describe = describe
    with ThreadPoolExecutor(max_workers=2) as pool:
        photo = pool.submit(agent.handle_image, "cli", "one", b"image", "These are my tomatoes")
        assert started.wait(3)
        switch = pool.submit(select_pond)
        assert switching.wait(3)
        try:
            assert not switch.done()
            assert agent._contexts.active(owner).unit_id == "field"
        finally:
            release.set()
        photo.result(timeout=5)
        switch.result(timeout=5)
    assert any("tomatoes" in r["content"] for r in agent._conv.recent_messages(field.context_id))
    assert agent._conv.recent_messages(agent._contexts.active(owner).context_id) == []


def test_selection_changed_by_another_client_does_not_reassign_a_photo(tmp_path):
    agent, owner = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    field = agent._contexts.active(owner)

    def describe(*args):
        # A separate CLI process writes the selection directly to SQLite.
        agent._contexts.select(owner, "pond")
        return "The lower leaves are yellow."

    agent._describe = describe
    agent.handle_image("cli", "one", b"image", "My tomatoes")
    assert agent._contexts.active(owner).unit_id == "pond"
    assert any("tomatoes" in r["content"] for r in agent._conv.recent_messages(field.context_id))
    assert agent._conv.recent_messages(agent._contexts.active(owner).context_id) == []


def test_soil_chat_nutrient_accounting_saves_a_plan_without_actual_applications(tmp_path):
    from agronaut_agent.tests.test_nutrition_cli import inputs

    agent, _ = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="field_records", id="nutrient-plan",
        args=dict(operation="save", data=dict(task="nutrient_budget", inputs=inputs(), request_id="nutrient-plan")))]),
        AIMessage(content="Declared nutrients compared with supplied targets.")]
    agent.handle_message("cli", "one", "Save a comparison of these product quantities with my supplied application targets")
    result = json.loads(agent.tool_results("cli", "one", "field_records")[-1])
    assert result["task"] == "nutrient_budget" and result["unit_id"] == "field"
    assert result["result"]["result"]["nutrients"]["n"]["excess_kg"] == 5
    assert result["result"]["feasible"] is None
    assert agent.field_workflow("cli", "one", "list", {})["water_days"] == []
