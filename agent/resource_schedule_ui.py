"""Propose starts from explicit run windows and shared timing supply inputs."""

import hashlib
import json

import streamlit as st

from .resource_timing_ui import (
    _changed,
    _dependency_editor,
    _editor,
    _timing_inputs,
    render_timing_result,
)

_STATUS = {
    "feasible_schedule_found": "A proposal passes the declared timing and storage checks.",
    "no_feasible_schedule_on_grid": "No feasible schedule exists on this time grid with these supplied inputs.",
    "search_limit_reached": "Search limit reached. Whether a feasible schedule exists is still unknown.",
}


def _submit(call, saved, save):
    supply = _timing_inputs(saved)
    rows = st.session_state.timing_rows
    data = {key: st.session_state.get("timing_schedule_"+key) for key in ("source", "step_seconds", "search_limit")}
    data.update(storage=supply["storage"], availability=supply["availability"],
        dependencies=list(rows.get("schedule_dependencies", {}).values()),
        runs=[dict(row, windows=[{k: v for k, v in window.items() if k != "id"}
                                for window in rows.get("run_windows:"+row["id"], {}).values()])
              for row in rows.get("runs", {}).values()])
    if "water_delivery" in supply:
        data["water_delivery"] = supply["water_delivery"]
    request = dict(plan_id=saved["id"], data=data)
    operation = "save_schedule" if save else "propose_schedule"
    if st.session_state.get("timing_schedule_equipment_enabled"):
        operation = "save_equipment_schedule" if save else "propose_equipment_schedule"
        request["data"] = dict(schedule=data,
            assignments={row["id"]: st.session_state.get("timing_schedule_equipment_run_"+row["id"], []) for row in data["runs"]},
            source=st.session_state.get("timing_schedule_equipment_source"),
            replaces_booking_id=st.session_state.get("timing_schedule_equipment_replaces"))
    if save:
        request["request_id"] = "web-schedule-"+hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    try:
        st.session_state.timing_schedule_result = call(operation, request)
        if save:
            st.session_state.timing_schedule_notice = "Scheduling scenario saved, including its search outcome. No resources reserved or work recorded."
    except (ValueError, TypeError) as exc:
        _changed()
        st.session_state.timing_schedule_error = str(exc)


def _result(record):
    report = record["schedule_result"]
    show = st.success if report["status"] == "feasible_schedule_found" else st.warning
    show(_STATUS[report["status"]])
    st.caption(f"Grid: every {report['step_seconds']} elapsed seconds from {report['grid_origin']}. "
               f"Attempted placements: {report['attempted_placements']} of {report['search_limit']}; "
               f"complete timing checks: {report['complete_checks']}.")
    if not report["all_requested_blocks_allocated"]:
        st.info("Scheduling covers the saved allocated blocks only. Some requested work is still unallocated.")
    if report.get("water_delivery_constraints_supplied"):
        st.caption(f"Water outlet delivery windows checked. Placements rejected for delivery limits: {report['delivery_pruned']}.")
    else:
        st.caption("This search does not check water outlet delivery capacity.")
    if context := record.get("equipment_schedule"):
        st.caption(f"Equipment calendar checked as of {context['constraints']['not_before']}. "
                   f"Placements rejected for equipment conflicts: {report['equipment_pruned']}.")
        st.caption(context["note"])
        if record.get("equipment_schedule_review", {}).get("changed"):
            st.warning("The equipment calendar snapshot has changed. Review current bookings before reusing this proposal.")
        st.dataframe([dict(equipment_id=a["id"], **a["data"]) for a in context["snapshot"]["equipment"].values()])
    if report["invariant_issues"]:
        st.write("These input constraints fail regardless of start times:")
        st.dataframe(report["invariant_issues"])
    if report["candidates_by_run"]:
        st.dataframe(report["candidates_by_run"])
    if "timing_inputs" in record:
        st.dataframe(record["timing_inputs"]["work"])
        render_timing_result(record["timing_result"])
    for assumption in report["assumptions"]:
        st.caption(assumption)


def _booking_review(record):
    prior = record["equipment_schedule"]["inputs"]["replaces_booking_id"]
    st.session_state.resource_equipment_booking_choice = prior
    st.session_state["resource_equipment_plan_"+(prior or "new")] = record["id"]
    st.session_state["resource_equipment_mode_"+(prior or "new")] = "Timed scenario"
    st.session_state.resource_notice = "Proposal selected in Shared equipment calendar above. Review its assignments and explicitly book or replace; no equipment is reserved yet."


def _equipment_editor(call, saved):
    old = saved.get("equipment_schedule", {}).get("inputs", {})
    enabled = st.checkbox("Consider shared equipment calendar", value=bool(old), key="timing_schedule_equipment_enabled", on_change=_changed)
    if not enabled:
        st.caption("This search checks the supplied timing and storage constraints. It does not check named equipment bookings.")
        return
    discovery = call("equipment_schedule_inputs", dict(plan_id=saved["id"]))
    assets = {r["id"]: r for r in discovery["equipment"] if r["data"]["status"] == "available"}
    from .equipment_calendar_ui import _selection
    st.caption("Assign fixed physical equipment for each run. The search avoids its current bookings and the other runs using it; it does not choose substitute equipment or book anything.")
    for run in st.session_state.timing_rows.get("runs", {}).values():
        key = "timing_schedule_equipment_run_"+run["id"]
        _selection(key, assets, old.get("assignments", {}).get(run["id"], []))
        st.multiselect("Equipment to schedule for "+run["id"], list(assets), key=key, on_change=_changed,
            format_func=lambda key: f"{assets[key]['data']['name']} · {assets[key]['data']['kind']} · {assets[key]['data']['equipment_key']}")
    bookings = {r["id"]: r for r in discovery["family_bookings"]}
    key = "timing_schedule_equipment_replaces"
    if key not in st.session_state:
        st.session_state[key] = old.get("replaces_booking_id")
    if st.session_state[key] not in (None, *bookings):
        st.session_state[key] = None
        st.caption("The previous booking revision is no longer current. Review and select the latest booking explicitly.")
    st.selectbox("Existing booking to replace after review", [None, *bookings], key=key, on_change=_changed,
        format_func=lambda key: "No existing booking" if key is None else f"{bookings[key]['title']} · {key[:8]}")
    if bookings:
        st.caption("This plan family has an active booking. Select it explicitly to exclude its occupied times from this search; the booking stays held until you replace it.")
    st.text_input("Source for equipment choice and suitability", value=old.get("source", ""), key="timing_schedule_equipment_source", on_change=_changed)


def render_resource_schedule(call, saved):
    with st.expander("Propose work start times"):
        if notice := st.session_state.pop("timing_schedule_notice", None):
            st.success(notice)
        if error := st.session_state.pop("timing_schedule_error", None):
            st.error(error)
        if "schedule_result" in saved:
            st.subheader("Saved scheduling outcome")
            _result(saved)
            if "equipment_schedule" in saved and saved["schedule_result"]["status"] == "feasible_schedule_found":
                st.button("Review equipment booking for saved proposal", on_click=_booking_review, args=(saved,))
        st.caption("First enter storage, external flows, and pump/labor availability in the timing section above. "
                   "The proposal uses those saved draft entries. Define runs below to cover exactly the allocated blocks; "
                   "each run needs a duration, concurrent resource counts, and permitted windows. "
                   "A whole run must fit one window. No windows means no permitted start times.")
        st.caption("Both dependency lists initially retain the saved parent's constraints; edits then stay in separate drafts. "
                   "Define work/run names that match those references, or explicitly revise the dependencies. "
                   "Saving an empty dependency list explicitly declares none for that alternative.")
        fields = [("id", "Run name", "text", None), ("job_id", "Allocated job for run", "choice",
                   [row["job_id"] for row in saved["result"]["allocations"] if row["allocated_blocks"]]),
                  ("blocks", "Blocks in run", "integer", None),
                  ("duration_seconds", "Run duration in elapsed seconds", "integer", None)]
        fields += [("units_"+r["id"], r["id"]+" units occupied by run (zero if unused)", "concurrent", None)
                   for r in saved["inputs"]["resources"] if r["kind"] in ("pump_time", "labor_time")]
        fields.append(("source", "Source for duration, crew and permitted windows", "text", None))
        _editor("runs", "Run", fields)
        for key in st.session_state.timing_rows["runs"]:
            _editor("run_windows:"+key, "Permitted window for "+key, [
                ("id", "Window name", "text", None),
                ("start", "Earliest permitted start (with farm UTC offset)", "text", None),
                ("end", "Latest permitted finish (with farm UTC offset)", "text", None)])
        _dependency_editor("schedule_dependencies", "runs", "Run dependency")
        _equipment_editor(call, saved)
        with st.form("timing_schedule_submit"):
            st.number_input("Start-time grid step (elapsed seconds)", min_value=1, max_value=1_000_000,
                            step=1, value=None, key="timing_schedule_step_seconds")
            st.number_input("Maximum attempted placements", min_value=1, max_value=10000,
                            step=1, value=None, key="timing_schedule_search_limit")
            st.text_input("Scheduling source and assumptions", key="timing_schedule_source")
            st.form_submit_button("Propose start times", on_click=_submit, args=(call, saved, False))
            st.form_submit_button("Save scheduling scenario", on_click=_submit, args=(call, saved, True))
        if result := st.session_state.get("timing_schedule_result"):
            st.subheader("Submitted scheduling outcome")
            st.caption("This result reflects the submitted inputs. Submit again after changing the grid or search limit.")
            _result(result)
            if result.get("id") and "equipment_schedule" in result and result["schedule_result"]["status"] == "feasible_schedule_found":
                st.button("Review equipment booking for proposal", on_click=_booking_review, args=(result,))
            st.download_button("Download scheduling result", json.dumps(result, indent=2), "resource-schedule.json", "application/json")
