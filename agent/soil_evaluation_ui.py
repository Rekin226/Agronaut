"""Read-only soil-prediction diagnostics with explicit sampling declarations."""

import json
from datetime import date

import streamlit as st

from agronaut_agent.soil_evaluation import render_soil_comparison


def _compare(call, calculation_id, prefix, selected, displayed_reviews):
    # Callback arguments retain the review IDs the operator actually saw, before
    # the form submission reruns the page and reads any newer review revisions.
    entries = []
    for identifier in selected:
        p = prefix+identifier+"_"
        state = st.session_state
        day = state.get(p+"day")
        entries.append(dict(observation_id=identifier, as_of=day.isoformat() if day else None,
            represented_depth_m=state.get(p+"depth"), representation_note=state.get(p+"representation"),
            depletion_adjustment_mm=state.get(p+"adjustment"), adjustment_source=state.get(p+"source"),
            expected_review=displayed_reviews[identifier]))
    request = dict(calculation_id=calculation_id, comparisons=entries, dataset_kind=st.session_state.get(prefix+"kind"),
                   selection_note=st.session_state.get(prefix+"note"))
    st.session_state.pop(prefix+"report", None)
    try:
        st.session_state[prefix+"report"] = dict(request=request, result=call("compare_soil", request))
    except (ValueError, TypeError) as exc:
        st.session_state[prefix+"error"] = str(exc)


def render_soil_evaluation(call, saved):
    prefix = f"field_soil_compare_{saved['id']}_"
    with st.expander("Compare soil predictions with measurements"):
        if error := st.session_state.pop(prefix+"error", None):
            st.error(error)
        st.caption("Compare predictions before same-day soil-state updates. Supply full-root/field sampling coverage "
                   "and a timing adjustment for each reading. This diagnostic does not change the calculation or establish field validation.")
        try:
            review = call("review_soil", dict(calculation_id=saved["id"]))
        except ValueError as exc:
            st.error(str(exc))
            return
        readings = {row["id"]: row for row in review["measurements"] if row["effective_quality"] != "rejected" and row["provenance"] != "forecast"}
        if not readings:
            st.info("Record an eligible soil measurement in the crop journal to compare it with a prediction.")
            return
        key = prefix+"readings"
        if key in st.session_state:
            st.session_state[key] = [identifier for identifier in st.session_state[key] if identifier in readings]
        selected = st.multiselect("Measurements to compare", list(readings), key=key,
            format_func=lambda identifier: f"{readings[identifier]['observed_at']} · {readings[identifier]['value']:g} {readings[identifier]['unit']} · {identifier}")
        first, last = saved["inputs"]["weather"][0]["day"], saved["inputs"]["weather"][-1]["day"]
        with st.form(prefix+"form"):
            kind = st.selectbox("Declared dataset kind", ["synthetic", "field"], index=None, key=prefix+"kind")
            note = st.text_area("Observation selection and independence rationale", key=prefix+"note")
            entries = []
            for identifier in selected:
                reading = readings[identifier]
                p = prefix+identifier+"_"
                st.caption(f"{identifier}: {reading['provenance']} · {reading['effective_quality']} · {reading['source']}")
                day = st.date_input("Prediction end-of-day date", value=None, min_value=date.fromisoformat(first), max_value=date.fromisoformat(last), key=p+"day")
                depth = st.number_input("Represented complete root depth (m)", min_value=0.0, max_value=10.0, value=None, key=p+"depth")
                representation = st.text_input("Root depth and field sampling coverage", key=p+"representation")
                adjustment = st.number_input("Depletion adjustment to closing midnight (mm)", value=None, key=p+"adjustment")
                source = st.text_input("Timing adjustment evidence or explicit zero-change assumption", key=p+"source")
                entries.append(dict(observation_id=identifier, as_of=day.isoformat() if day else None, represented_depth_m=depth,
                    representation_note=representation, depletion_adjustment_mm=adjustment, adjustment_source=source,
                    expected_review=reading["quality_review"]["id"] if reading["quality_review"] else None))
            st.form_submit_button("Compare without changing soil state", on_click=_compare,
                args=(call, saved["id"], prefix, selected, {identifier: readings[identifier]["quality_review"]["id"]
                      if readings[identifier]["quality_review"] else None for identifier in selected}))
        request = dict(calculation_id=saved["id"], comparisons=entries, dataset_kind=kind, selection_note=note)
        cached = st.session_state.get(prefix+"report")
        if cached and cached["request"] == request:
            report = cached["result"]
            st.text(render_soil_comparison(report))
            if report["groups"]:
                st.dataframe([dict(provenance=row["provenance"], quality=row["quality"], weather_kind=row["weather_kind"], **row["metrics"])
                              for row in report["groups"]], hide_index=True)
            st.download_button("Download soil comparison and evidence", json.dumps(report, indent=2),
                file_name=f"soil-comparison-{saved['id']}.json", mime="application/json", key=prefix+"download")
        elif cached:
            st.caption("The selection, declarations or quality reviews changed. Run the comparison again to refresh the report.")
