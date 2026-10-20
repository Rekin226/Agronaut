"""Deterministic field record page; observations remain separate from proposals."""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from agent.catch_ui import render_catch_intake
from agent.coupled_soil_evaluation_ui import render_coupled_soil_evaluation
from agent.coupled_state_ui import render_coupled_state
from agent.coupled_water_ui import render_coupled_continuation, render_coupled_water_intake
from agent.crop_coefficient_ui import render_crop_revision
from agent.drip_diagnostics_ui import render_drip_diagnostics
from agent.drip_lateral_ui import render_drip_lateral_intake
from agent.drip_zone_ui import render_drip_zone_intake
from agent.emitter_ui import render_emitter_intake
from agent.equipment_revision_ui import render_equipment_revision
from agent.evaporation_ui import render_evaporation_intake
from agent.field_intake import render_field_intake
from agent.nutrition_ui import render_nutrition_intake
from agent.root_growth_ui import render_root_revision
from agent.soil_evaluation_ui import render_soil_evaluation
from agent.soil_profile_ui import render_soil_profile_intake
from agent.soil_report_ui import render_soil_reports
from agent.soil_state_ui import render_soil_revision
from agent.sprinkler_diagnostics_ui import render_sprinkler_diagnostics
from agent.sprinkler_lateral_ui import render_sprinkler_lateral_intake
from agent.surface_advance_ui import render_surface_advance_intake
from agent.surface_diagnostics_ui import render_surface_diagnostics
from agent.surface_water_ui import render_surface_water
from agent.weather_review_ui import render_weather_review
from agronaut_agent.catch_io import render_catch_report
from agronaut_agent.coupled_water_io import render_coupled_water_report
from agronaut_agent.drip_lateral_io import render_drip_lateral_report
from agronaut_agent.drip_manifold_io import render_drip_manifold_report
from agronaut_agent.drip_zone_io import render_drip_zone_report
from agronaut_agent.emitter_io import render_emitter_report
from agronaut_agent.evaporation_io import render_evaporation_report
from agronaut_agent.field_cli import _reject_constant, _unique_keys
from agronaut_agent.field_io import render_field_report
from agronaut_agent.irrigation_io import render_drip_report, render_sprinkler_report
from agronaut_agent.nutrition_io import render_nutrition_report
from agronaut_agent.soil_profile_io import render_soil_profile_report
from agronaut_agent.sprinkler_lateral_io import render_sprinkler_lateral_report
from agronaut_agent.surface_advance_io import render_surface_advance_report
from agronaut_agent.surface_io import render_surface_report

_TASKS = {"root_zone_budget": "Soil-water budget", "crop_water": "Crop-water demand",
          "drip_lateral": "Surveyed drip lateral pressure and flow",
          "sprinkler_lateral": "Surveyed sprinkler nozzle and riser hydraulics",
          "drip_manifold": "Surveyed drip distribution manifold",
          "drip_zone": "Simultaneous drip zone at one junction",
          "drip_design": "Drip equipment design", "sprinkler_design": "Sprinkler equipment design",
          "surface_evaluation": "Surface-irrigation event evaluation", "nutrient_budget": "Mineral nutrient accounting",
          "surface_advance": "Sloping surface advance scenario",
          "sprinkler_catch_test": "Sprinkler catch-grid evaluation", "drip_emitter_test": "Drip-emitter evaluation",
          "soil_profile": "Layered soil-profile storage", "dual_crop_water": "Dual-coefficient crop demand",
          "coupled_soil_water": "Coupled soil-water replay"}


def _request_id(operation, data):
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return f"web-{operation}:{digest}"


def render_field_records(brain, user: str, channel: str = "web"):
    active = next((u for u in brain.list_units(channel, user) if u["active"]), None)
    scope = active["id"] if active else None
    if st.session_state.get("field_scope_unit") != scope:
        # Unit changes can also arrive from chat or another client, without the sidebar callback.
        for key in list(st.session_state):
            if key.startswith("field_"):
                del st.session_state[key]
        st.session_state.field_scope_unit = scope
    st.header("Field records")
    if active is None or active["production_system"] != "soil":
        st.info("Select a soil production unit to save calculations and record irrigation or rainfall.")
        return
    st.caption(f"{active['name']} · Experimental calculations; no field validation")
    from agent.field_guides_ui import render_field_guides
    render_field_guides()
    if active.get("parent_unit_id"):
        st.caption(f"Zone of {active['parent_name']}. Inputs and records on this page apply to this zone; parent-field and sibling-zone records are separate.")
    notice = st.session_state.pop("field_notice", None)
    if notice:
        st.success(notice)
    if error := st.session_state.pop("field_error", None):
        st.error(error)
    next_plan = st.session_state.pop("field_next_plan", None)
    if next_plan:
        st.session_state.field_plan = next_plan

    def call(operation, data):
        return brain.field_workflow(channel, user, operation, data, expected_unit_id=active["id"])

    def save_revision(operation, data):
        # Form callbacks run before rendering. Select the child on that rerun so
        # widgets from the parent and child are never mixed in a partial render.
        try:
            updated = call(operation, dict(data, request_id=_request_id(operation, data)))
            st.session_state.field_next_plan = updated["id"]
            st.session_state.field_notice = "Revision saved as a new calculation version."
        except (ValueError, TypeError) as exc:
            st.session_state.field_error = str(exc)

    try:
        records = call("list", {})
    except ValueError as exc:
        st.error(str(exc))
        return
    farm = next(f for f in brain.list_farms(channel, user) if f["id"] == active["farm_id"])
    yesterday = datetime.now(ZoneInfo(farm["timezone"])).date() - timedelta(days=1)
    cycles = {c["id"]: c for c in brain.list_cycles(channel, user, active["id"])}
    # Keep the chosen ID when corrected crop/date labels change the widget options.
    selected_cycle = st.session_state.get("field_cycle")
    st.session_state.field_cycle = selected_cycle if selected_cycle in cycles else None
    cycle_id = st.selectbox("Crop cycle", [None, *cycles],
                            format_func=lambda key: "No cycle assigned" if key is None
                            else f"{cycles[key]['crop']} · planted {cycles[key]['planted_on']} · {cycles[key]['lifecycle']['status']} · {key[:8]}",
                            key="field_cycle")
    if cycle_id is not None and cycles[cycle_id]["lifecycle"]["status"] == "closed":
        st.info(f"This crop cycle ended on {cycles[cycle_id]['lifecycle']['ended_on']}. "
                "Calculations remain available for retrospective analysis; later actual water records require reopening in Crop records.")
    render_soil_reports(call, active, farm, cycle_id)
    intake = render_field_intake(active, farm, cycles.get(cycle_id), call)
    if intake is not None:
        try:
            data = dict(intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("save", data)))
            st.success("Field-water calculation saved. No irrigation has been recorded as completed.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    nutrient_intake = render_nutrition_intake(active, farm, cycles.get(cycle_id))
    if nutrient_intake is not None:
        try:
            data = dict(nutrient_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("nutrient", data)))
            st.success("Nutrient accounting saved. No fertilizer application has been recorded.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    catch_intake = render_catch_intake(active, farm, cycles.get(cycle_id))
    if catch_intake is not None:
        try:
            data = dict(catch_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("catch", data)))
            st.success("Catch test saved. Daily water logs are unchanged.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    emitter_intake = render_emitter_intake(active, farm, cycles.get(cycle_id))
    if emitter_intake is not None:
        try:
            data = dict(emitter_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("emitter", data)))
            st.success("Emitter test saved. Daily water logs are unchanged.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    profile_intake = render_soil_profile_intake(active, farm, cycles.get(cycle_id))
    if profile_intake is not None:
        try:
            data = dict(profile_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("soil-profile", data)))
            st.success("Soil profile assessment saved.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    evaporation_intake = render_evaporation_intake(active, farm, cycles.get(cycle_id))
    if evaporation_intake is not None:
        try:
            data = dict(evaporation_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("dual-crop-water", data)))
            st.success("Dual-coefficient demand calculation saved.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    lateral_intake = render_drip_lateral_intake(active, farm, cycles.get(cycle_id))
    if lateral_intake is not None:
        try:
            data = dict(lateral_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("drip-lateral", data)))
            st.success("Lateral scenario saved. No completed irrigation was recorded.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    sprinkler_intake = render_sprinkler_lateral_intake(active, farm, cycles.get(cycle_id))
    if sprinkler_intake is not None:
        try:
            data = dict(sprinkler_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("sprinkler-lateral", data)))
            st.success("Sprinkler lateral scenario saved. No completed irrigation was recorded.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    zone_intake = render_drip_zone_intake(active, farm, cycles.get(cycle_id), records, call)
    if zone_intake is not None:
        try:
            data = dict(zone_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id(zone_intake["task"], data)))
            st.success("Simultaneous zone scenario saved. No completed irrigation was recorded.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    advance_intake = render_surface_advance_intake(active, farm, cycles.get(cycle_id))
    if advance_intake is not None:
        try:
            data = dict(advance_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("surface-advance", data)))
            st.success("Advance scenario saved. No completed irrigation was recorded.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    coupled_intake = render_coupled_water_intake(active, farm, cycles.get(cycle_id))
    if coupled_intake is not None:
        try:
            data = dict(coupled_intake, cycle_id=cycle_id)
            saved = call("save", dict(data, request_id=_request_id("coupled-soil-water", data)))
            st.success("Coupled soil-water calculation saved.")
            records = call("list", {})
            st.session_state.field_plan = saved["id"]
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    with st.expander("Import and save a calculation"):
        st.caption("Upload a calculation input JSON file. Equipment and weather sources stay attached to the saved result.")
        task = st.selectbox("Calculation", list(_TASKS), format_func=_TASKS.get, key="field_task")
        uploaded = st.file_uploader("Calculation inputs", type=["json"], key="field_inputs")
        if st.button("Calculate and save", key="field_save"):
            try:
                if uploaded is None:
                    raise ValueError("Choose a calculation input file first")
                payload = json.loads(uploaded.getvalue().decode(), parse_constant=_reject_constant,
                                     object_pairs_hook=_unique_keys)
                data = dict(task=task, inputs=payload, cycle_id=cycle_id)
                saved = call("save", dict(data, request_id=_request_id("save", data)))
                st.success("Calculation saved. No irrigation has been recorded as completed.")
                records = call("list", {})
                st.session_state.field_plan = saved["id"]
            except (ValueError, TypeError, UnicodeError) as exc:
                st.error(str(exc))

    current = [r for r in records["water_days"] if not r["superseded"] and r["cycle_id"] == cycle_id]
    with st.expander("Record a completed day's water"):
        st.caption(f"Daily totals use {farm['timezone']}. Leave unknown amounts blank; enter zero only when known.")
        corrections = {r["id"]: r for r in current}
        correction = st.selectbox("Record type", [None, *corrections],
                                  format_func=lambda key: "New daily totals" if key is None
                                  else f"Correct {corrections[key]['day']}", key="field_correction")
        with st.form("field_water_form"):
            day = st.date_input("Completed day", value=yesterday, max_value=yesterday, key="field_day")
            rainfall = st.number_input("Rainfall (mm)", min_value=0.0, value=None, key="field_rain")
            irrigation = st.number_input("Net irrigation reaching the root zone (mm)", min_value=0.0,
                                         value=None, key="field_irrigation",
                                         help="This is not pumped litres or a proposed irrigation amount.")
            runoff = st.number_input("Rainfall runoff (mm)", min_value=0.0, value=None, key="field_runoff")
            source = st.text_input("Source of these daily totals", key="field_source")
            provenance = st.selectbox("How were the amounts obtained?", ["reported", "measured", "estimated"],
                                      key="field_provenance")
            if st.form_submit_button("Save daily totals"):
                try:
                    data = dict(data=dict(day=day.isoformat(), rainfall_mm=rainfall,
                                          net_irrigation_mm=irrigation, runoff_mm=runoff,
                                          source=source, provenance=provenance, quality="unverified"),
                                cycle_id=cycle_id, supersedes_id=correction)
                    call("record_day", dict(data, request_id=_request_id("day", data)))
                    st.success("Daily totals saved. Recalculate a budget below to use them.")
                    records = call("list", {})
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))

    if records["water_days"]:
        st.subheader("Water log")
        st.dataframe([dict(day=r["day"], **{k: r["data"][k] for k in
                      ("rainfall_mm", "net_irrigation_mm", "runoff_mm", "provenance", "quality")},
                      superseded=r["superseded"]) for r in records["water_days"] if r["cycle_id"] == cycle_id])
    plans = {p["id"]: p for p in records["calculations"] if p["cycle_id"] == cycle_id}
    if not plans:
        st.info("No saved calculations for this crop cycle yet.")
        return
    if st.session_state.get("field_plan") not in plans:
        st.session_state.field_plan = next(reversed(plans))
    plan_id = st.selectbox("Saved calculation", list(plans), key="field_plan",
                           format_func=lambda key: f"{_TASKS[plans[key]['task']]} · {plans[key]['created_at']}")
    saved = call("get", {"calculation_id": plan_id})
    result = saved["result"]["result"]
    renderer = {"drip_design": render_drip_report, "sprinkler_design": render_sprinkler_report,
                "drip_lateral": render_drip_lateral_report,
                "sprinkler_lateral": render_sprinkler_lateral_report,
                "drip_zone": render_drip_zone_report,
                "drip_manifold": render_drip_manifold_report,
                "surface_advance": render_surface_advance_report,
                "surface_evaluation": render_surface_report, "nutrient_budget": render_nutrition_report,
                "sprinkler_catch_test": render_catch_report, "drip_emitter_test": render_emitter_report,
                "soil_profile": render_soil_profile_report, "dual_crop_water": render_evaporation_report,
                "coupled_soil_water": render_coupled_water_report}.get(
        saved["task"], render_field_report)
    st.text(renderer(result))
    render_equipment_revision(call, saved, save_revision)
    render_weather_review(call, saved)
    render_surface_diagnostics(call, saved, records, farm, cycles.get(cycle_id))
    render_drip_diagnostics(call, saved, records, farm, cycles.get(cycle_id))
    render_sprinkler_diagnostics(call, saved, farm, cycles.get(cycle_id))
    render_coupled_state(call, saved, save_revision)
    render_coupled_continuation(saved, active, farm, cycles.get(cycle_id), save_revision)
    render_coupled_soil_evaluation(call, saved)
    render_surface_water(call, saved, save_revision)
    if saved["task"] == "dual_crop_water":
        st.line_chart([dict(day=row["day"], basal_et_mm=row["values"]["basal_et_mm"], soil_evaporation_mm=row["values"]["soil_evaporation_mm"])
                       for row in result["days"]], x="day", y=["basal_et_mm", "soil_evaporation_mm"])
        st.caption("Modeled daily basal ET and soil evaporation in millimeters over the represented field area.")
    if saved.get("water_target"):
        from agronaut_agent.equipment_targets import describe
        st.caption(describe(saved["water_target"]))
        review = call("review_target", dict(calculation_id=plan_id))
        if review["needs_review"]:
            st.warning("The adopted water target needs review: " + "; ".join(review["issues"]))
    st.download_button("Download calculation and evidence", json.dumps(saved, indent=2),
                        file_name=f"field-calculation-{saved['id']}.json", mime="application/json")
    if saved["task"] == "root_zone_budget":
        render_soil_revision(call, saved, save_revision)
        render_soil_evaluation(call, saved)
        render_root_revision(saved, save_revision)
    if saved["task"] in ("crop_water", "root_zone_budget"):
        render_crop_revision(call, saved, save_revision)
    if saved["task"] in ("crop_water", "root_zone_budget", "dual_crop_water"):
        with st.expander("Update weather or extend the planning window"):
            if saved["task"] == "dual_crop_water":
                st.caption("Supply complete weather records for the dates to replace or append; retain weather_sources with provider records. "
                           "Existing basal coefficients, wetting fractions, irrigation, runoff and initial surface state stay unchanged. "
                           "Each appended day needs an explicit dual_days entry with all daily crop and water inputs. "
                           "Review runoff against changed rainfall; this operation does not edit irrigation assumptions or actual-water logs.")
            else:
                st.caption("Supply complete weather records for the dates to replace or append. "
                           "Recorded water totals and existing irrigation assumptions stay attached. "
                           "Each appended budget day needs an explicit irrigation/runoff event, including known zeros. "
                           "The supplied coefficient schedule must cover the extension. With daily root depths, "
                           "also supply root_zone_days for every appended day.")
            # Key by plan so a patch written for one version cannot silently follow
            # the user to another selected plan or crop cycle.
            patch = st.text_area("Weather update JSON", key=f"field_weather_patch_{plan_id}",
                                 placeholder='{"weather": [complete weather records], "dual_days": [new days only]}'
                                 if saved["task"] == "dual_crop_water" else '{"weather": [complete weather records], "events": [new days only]}')
            if st.button("Save weather revision", key="field_revise_weather"):
                try:
                    payload = json.loads(patch, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
                    if not isinstance(payload, dict) or set(payload) - {"weather", "events", "weather_sources", "root_zone_days", "dual_days"}:
                        raise ValueError("Weather update must contain weather and optionally events, weather_sources, root_zone_days and dual_days")
                    data = dict(payload, calculation_id=plan_id)
                    updated = call("revise_weather", dict(data, request_id=_request_id("weather", data)))
                    st.session_state.field_next_plan = updated["id"]
                    st.session_state.field_notice = "Weather revision saved as a new calculation version."
                    st.rerun()
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
    if saved["task"] in ("root_zone_budget", "dual_crop_water"):
        choices = {r["id"]: r for r in records["water_days"] if not r["superseded"]
                   and r["cycle_id"] == cycle_id and r["data"]["quality"] != "rejected"}
        selected = st.multiselect("Daily totals to use", list(choices), key="field_replay_days",
                                  format_func=lambda key: choices[key]["day"])
        st.caption("Recalculation replaces those days' water totals and saves a new version. Other days stay as supplied.")
        if saved["task"] == "dual_crop_water":
            st.caption("Review the retained crop and surface assumptions before replay: daily totals do not establish "
                       "early-day wetting, canopy cover or wetted fractions. Those assumptions stay unchanged; "
                       "the result is modeled potential demand, not measured crop water use or an irrigation prescription.")
        allow_historical = st.checkbox("Use historical weather estimates for selected daily records",
                                        key=f"field_replay_historical_{plan_id}", value=False)
        if allow_historical:
            st.caption("Historical weather and ET remain estimates. Recorded rainfall and irrigation keep their own "
                       "sources and quality labels. Forecasts and hypothetical weather are not accepted for replay.")
        if st.button("Recalculate with selected records", key="field_replay"):
            try:
                data = dict(calculation_id=plan_id, water_record_ids=selected)
                if allow_historical:
                    data["weather_policy"] = "allow_historical"
                updated = call("replay", dict(data, request_id=_request_id("replay", data)))
                st.session_state.field_next_plan = updated["id"]
                st.session_state.field_notice = "New calculation version saved using the selected daily totals."
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
