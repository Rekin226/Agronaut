"""Read-only native comparison of saved advance scenarios with event records."""

import json

import streamlit as st

from agronaut_agent.surface_diagnostics import render_surface_advance_comparison

_P = "field_advance_compare_"


def _request(saved_id, selected):
    return dict(calculation_id=saved_id, comparisons=[dict(event_calculation_id=identifier,
        independence=st.session_state.get(_P+identifier+"_independence"),
        representation_note=st.session_state.get(_P+identifier+"_representation")) for identifier in selected],
        dataset_kind=st.session_state.get(_P+"kind"), selection_note=st.session_state.get(_P+"note"))


def _compare(call, saved_id, cycle_id, selected):
    st.session_state.pop(_P+"report", None)
    try:
        if st.session_state.get("field_plan") != saved_id or st.session_state.get("field_cycle") != cycle_id:
            raise ValueError("The selected calculation or crop cycle changed; review the comparison draft.")
        request = _request(saved_id, selected)
        st.session_state[_P+"report"] = dict(request=request, result=call("compare_surface_advance", request))
    except (ValueError, TypeError) as exc:
        st.session_state[_P+"error"] = str(exc)


def render_surface_diagnostics(call, saved, records, farm, cycle):
    scope = (saved["id"], saved["cycle_id"], farm["timezone"], json.dumps(cycle, sort_keys=True))
    if st.session_state.get(_P+"scope") != scope:
        for key in list(st.session_state):
            if key.startswith(_P):
                del st.session_state[key]
        st.session_state[_P+"scope"] = scope
    if saved["task"] != "surface_advance":
        return
    with st.expander("Compare advance predictions with surface events"):
        if error := st.session_state.pop(_P+"error", None):
            st.error(error)
        if saved["result"]["result"]["prediction"] is None:
            st.info("A solved advance scenario is required before comparing arrival times.")
            return
        if farm["timezone"] != saved["inputs"]["site"]["timezone"]:
            st.info("The farm timezone changed; review this saved scenario before comparing events.")
            return
        st.caption("Compare recorded half/full-length arrival times for the same strip and inflow. "
                   "Only events declared held out from calibration enter metrics. Missing half-length readings stay missing; "
                   "a failed modeled water balance does not exclude an arrival observation. Independence remains unverified.")
        events = {item["id"]: item for item in records["calculations"]
                  if item["task"] == "surface_evaluation" and item["cycle_id"] == saved["cycle_id"]}
        if not events:
            st.info("Save a surface-irrigation event evaluation for this unit and crop cycle to compare its recorded arrival times.")
            return
        if _P+"events" in st.session_state:
            st.session_state[_P+"events"] = [i for i in st.session_state[_P+"events"] if i in events]
        selected = st.multiselect("Surface events to compare", list(events), key=_P+"events",
            format_func=lambda i: f"Saved {events[i]['created_at']} · {i}")
        with st.form(_P+"form"):
            st.selectbox("Advance comparison dataset kind", ["synthetic", "field"], index=None, key=_P+"kind")
            st.text_area("Event selection rationale", key=_P+"note")
            for identifier in selected:
                st.caption(f"Event {identifier}")
                st.text_input("Evidence that the event represents this strip and modeled conditions", key=_P+identifier+"_representation")
                st.selectbox("Use of these arrival measurements in model development", ["held_out", "used_for_calibration", "unknown"],
                             index=None, key=_P+identifier+"_independence")
            st.form_submit_button("Compare advance times without changing records", on_click=_compare,
                                  args=(call, saved["id"], saved["cycle_id"], selected), key=_P+"run")
        cached = st.session_state.get(_P+"report")
        if cached and cached["request"] == _request(saved["id"], selected):
            report = cached["result"]
            st.text(render_surface_advance_comparison(report))
            st.dataframe([dict(event=row["event_calculation"]["id"], anchor=pair["anchor"],
                predicted_min=pair["predicted_min"], recorded_min=pair["reference_min"], error_min=pair["error_min"],
                status=pair["status"], included_in_metrics=row["included_in_metrics"] and pair["status"] == "compared",
                exclusion=row["exclusion_reason"], event_model_balance_accepted=row["reference_water_balance_accepted"])
                for row in report["comparisons"] for pair in row["anchors"]], hide_index=True)
            st.download_button("Download advance comparison and evidence", json.dumps(report, indent=2),
                file_name=f"surface-advance-comparison-{saved['id']}.json", mime="application/json", key=_P+"download")
        elif cached:
            st.caption("Selection or declarations changed. Run the comparison again to refresh the report.")
