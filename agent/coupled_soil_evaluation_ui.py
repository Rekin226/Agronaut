"""Native comparison of saved coupled profiles against declared sampling intervals."""

import json
from datetime import date
from fractions import Fraction

import streamlit as st

from agronaut_agent.coupled_soil_evaluation import render_coupled_soil_comparison


def _entry(prefix, identifier, review_id):
    state, p = st.session_state, prefix+identifier+"_"
    day = state.get(p+"day")
    return dict(observation_id=identifier, as_of=day.isoformat() if day else None,
        patch_id=state.get(p+"patch"), top_m=state.get(p+"top"), bottom_m=state.get(p+"bottom"),
        representation_note=state.get(p+"representation"), water_content_adjustment=state.get(p+"adjustment"),
        adjustment_source=state.get(p+"source"), expected_review=review_id, independence=state.get(p+"independence"))


def _compare(call, saved_id, prefix, selected, reviews):
    st.session_state.pop(prefix+"report", None)
    try:
        if st.session_state.get("field_plan") != saved_id:
            raise ValueError("The selected calculation changed; review the comparison draft.")
        request = dict(calculation_id=saved_id, comparisons=[_entry(prefix, i, reviews[i]) for i in selected],
            dataset_kind=st.session_state.get(prefix+"kind"), selection_note=st.session_state.get(prefix+"note"))
        st.session_state[prefix+"report"] = dict(request=request, result=call("compare_soil", request))
    except (ValueError, TypeError) as exc:
        st.session_state[prefix+"error"] = str(exc)


def render_coupled_soil_evaluation(call, saved):
    prefix = "field_coupled_compare_"
    if st.session_state.get(prefix+"scope") != saved["id"]:
        for key in list(st.session_state):
            if key.startswith(prefix):
                del st.session_state[key]
        st.session_state[prefix+"scope"] = saved["id"]
    if saved["task"] != "coupled_soil_water":
        return
    with st.expander("Compare coupled soil water with measurements"):
        if error := st.session_state.pop(prefix+"error", None):
            st.error(error)
        st.caption("Compare saved end-of-day water content over an explicit depth interval and patch footprint. "
                   "A point reading needs evidence that it represents this volume. Only readings declared held out from "
                   "initialization and calibration enter metrics; independence remains unverified.")
        try:
            review = call("review_soil", dict(calculation_id=saved["id"]))
        except ValueError as exc:
            st.error(str(exc))
            return
        readings = {r["id"]: r for r in review["measurements"] if r["effective_quality"] != "rejected" and r["provenance"] != "forecast"}
        if not readings:
            st.info("Record an eligible soil-water-content measurement in the crop journal to compare it with this prediction.")
            return
        key = prefix+"readings"
        if key in st.session_state:
            st.session_state[key] = [i for i in st.session_state[key] if i in readings]
        selected = st.multiselect("Soil-water-content measurements to compare", list(readings), key=key,
            format_func=lambda i: f"{readings[i]['observed_at']} · {readings[i]['value']:g} m³/m³ · {i}")
        result = saved["result"]["result"]
        patches = [p["id"] for p in result["days"][0]["ending_soil"]["patches"]]
        reviews = {i: readings[i]["quality_review"]["id"] if readings[i]["quality_review"] else None for i in selected}
        with st.form(prefix+"form"):
            kind = st.selectbox("Coupled comparison dataset kind", ["synthetic", "field"], index=None, key=prefix+"kind")
            note = st.text_area("Coupled observation selection rationale", key=prefix+"note")
            for identifier in selected:
                p, r = prefix+identifier+"_", readings[identifier]
                st.caption(f"{identifier}: {r['provenance']} · {r['effective_quality']} · {r['source']}")
                st.date_input("Coupled prediction end-of-day date", value=None, min_value=date.fromisoformat(result["period_start"]),
                    max_value=date.fromisoformat(result["period_end"]), key=p+"day")
                st.selectbox("Represented soil patch", patches, index=None, key=p+"patch")
                st.text_input("Sample interval top (m, decimal point)", key=p+"top")
                st.text_input("Sample interval bottom (m, decimal point)", key=p+"bottom")
                st.text_input("Evidence for depth interval and patch sampling coverage", key=p+"representation")
                st.number_input("Water-content adjustment to closing midnight (m³/m³, positive wetter)", value=None,
                    min_value=-1.0, max_value=1.0, format="%.6f", key=p+"adjustment")
                st.text_input("Timing adjustment evidence or explicit zero-change assumption", key=p+"source")
                st.selectbox("Use of this reading in model development", ["held_out", "used_for_initialization", "used_for_calibration", "unknown"],
                    index=None, key=p+"independence")
            st.form_submit_button("Compare coupled predictions without changing state", on_click=_compare,
                args=(call, saved["id"], prefix, selected, reviews))
        request = dict(calculation_id=saved["id"], comparisons=[_entry(prefix, i, reviews[i]) for i in selected], dataset_kind=kind, selection_note=note)
        cached = st.session_state.get(prefix+"report")
        if cached and cached["request"] == request:
            report = cached["result"]
            st.text(render_coupled_soil_comparison(report))
            if report["groups"]:
                st.dataframe([{"Patch": row["patch_id"], "Top (m)": float(Fraction(row["top_m_exact"])),
                    "Bottom (m)": float(Fraction(row["bottom_m_exact"])), "Provenance": row["provenance"],
                    "Quality": row["quality"], "Weather": row["weather_kind"], "Count": row["metrics"]["count"],
                    "Bias (m³/m³)": row["metrics"]["bias"], "MAE (m³/m³)": row["metrics"]["mae"],
                    "RMSE (m³/m³)": row["metrics"]["rmse"]} for row in report["groups"]], hide_index=True)
            st.download_button("Download coupled soil comparison and evidence", json.dumps(report, indent=2),
                file_name=f"coupled-soil-comparison-{saved['id']}.json", mime="application/json", key=prefix+"download")
        elif cached:
            st.caption("The selection, declarations or quality reviews changed. Run the coupled comparison again to refresh the report.")
