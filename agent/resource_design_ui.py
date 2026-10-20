"""Review an owned irrigation calculation before keeping a dated resource job."""

import json
from datetime import date

import streamlit as st


def _preview(call, water, pump):
    day = st.session_state.get("resource_design_day")
    data = {key: st.session_state.get("resource_design_"+key) for key in ("calculation_id", "job_id", "title", "priority", "source")}
    data.update(day=day.isoformat() if day else None, water_resource_id=water, pump_resource_id=pump,
        resources=list(st.session_state.resource_pools.values()),
        other_requirements={key: st.session_state.get("resource_design_amount_"+key)
                            for key in st.session_state.resource_pools if key not in (water, pump)})
    try:
        st.session_state.resource_design_result = call("design_job", dict(data=data))
        st.session_state.resource_design_request = data
    except (ValueError, TypeError) as exc:
        st.session_state.pop("resource_design_result", None)
        st.session_state.resource_error = str(exc)


def _keep(call):
    data = st.session_state.resource_design_request
    try:
        if data["resources"] != list(st.session_state.resource_pools.values()):
            raise ValueError("resource pools changed; preview the irrigation job again")
        refreshed = call("design_job", dict(data=data))
        job = refreshed["job"]
        if job["id"] in st.session_state.resource_jobs:
            raise ValueError("a work entry with that name already exists; use a distinct name or remove the old entry first")
        st.session_state.resource_jobs[job["id"]] = job
        st.session_state.pop("resource_result", None)
        st.session_state.pop("resource_design_result", None)
        st.session_state.resource_notice = "Reviewed irrigation job kept in the local draft. Save the resource scenario to retain its evidence."
    except (ValueError, TypeError) as exc:
        st.session_state.resource_error = str(exc)


def render_evidence(evidence):
    saved = evidence["calculation"]
    st.write(dict(job_id=evidence["job_id"], event_day=evidence["day"], calculation_id=saved["id"],
                  task=saved["task"], model_version=saved["result"]["result"]["model_version"], **evidence["conversion"]))
    for assumption in evidence["assumptions"]:
        st.caption(assumption)


def render_design_job(call, pools):
    water_ids = [key for key, pool in pools.items() if pool["kind"] == "water"]
    pump_ids = [key for key, pool in pools.items() if pool["kind"] == "pump_time"]
    with st.expander("Use a saved irrigation design"):
        if not water_ids or not pump_ids:
            st.info("Add a water pool and a pump-time pool to link an irrigation event.")
            return
        catalog = call("describe", {})
        designs = {row["id"]: row for row in catalog["irrigation_designs"]}
        unit_names = {row["id"]: row["name"] for row in catalog["units"]}
        if not designs:
            st.info("Save a drip or sprinkler equipment calculation for a soil unit in this farm first.")
            return
        st.caption("One block covers the complete supplied event, including maintenance. Choose its date and resource pools. "
                   "Confirm that all supplied operation and maintenance hours belong to the chosen pump pool; "
                   "supply labor, cash and every other requirement independently. Preview does not save or perform work.")
        selected = st.session_state.get("resource_design_calculation_id")
        if selected not in designs:
            st.session_state.resource_design_calculation_id = None
        st.selectbox("Saved irrigation calculation", list(designs), index=None, key="resource_design_calculation_id",
                     format_func=lambda key: f"{unit_names.get(designs[key]['unit_id'], designs[key]['unit_id'])} · {designs[key]['task']} · {designs[key]['created_at']} · {key[:8]}")
        for key, ids, label in (("water", water_ids, "Water pool for this event"), ("pump", pump_ids, "Pump-time pool for this event")):
            if st.session_state.get("resource_design_"+key) not in ids:
                st.session_state["resource_design_"+key] = None
            st.selectbox(label, ids, index=None, key="resource_design_"+key)
        water, pump = (st.session_state.get("resource_design_"+key) for key in ("water", "pump"))
        with st.form("resource_design_form"):
            st.text_input("Irrigation work entry name", key="resource_design_job_id")
            st.text_input("Irrigation work description", key="resource_design_title")
            st.date_input("Planned event date", value=None, min_value=date(1000, 1, 1), max_value=date.max, key="resource_design_day")
            st.number_input("Irrigation work priority", min_value=1, max_value=1_000_000, step=1, value=None, key="resource_design_priority")
            for key, pool in pools.items():
                if key not in (water, pump):
                    st.text_input(f"{key} per full event ({pool['unit']}; explicit zero if unused)", key="resource_design_amount_"+key)
            st.text_input("Applicability, date, resource mapping and additional-requirements source", key="resource_design_source")
            st.form_submit_button("Preview irrigation job", on_click=_preview, args=(call, water, pump))
        if preview := st.session_state.get("resource_design_result"):
            st.caption("Last submitted preview. Check the source inputs and assumptions in the download; preview again after changing inputs.")
            render_evidence(preview["evidence"])
            st.write(preview["job"])
            st.download_button("Download irrigation job evidence", json.dumps(preview, indent=2), "irrigation-job.json", "application/json")
            st.button("Keep reviewed irrigation job in draft", on_click=_keep, args=(call,))
