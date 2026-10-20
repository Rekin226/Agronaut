"""Explicit measurement-backed soil-state revisions, without model inference."""

from datetime import date, timedelta

import streamlit as st


def _submit_state(key, calculation_id, observation_id, expected_review, save):
    state = st.session_state
    day = state[key + "day"]
    save("apply_soil_state", dict(calculation_id=calculation_id, observation_id=observation_id,
        as_of=day.isoformat() if day else None, represented_depth_m=state[key + "depth"],
        representation_note=state[key + "representation"], depletion_adjustment_mm=state[key + "adjustment"],
        adjustment_source=state[key + "source"], reason=state[key + "reason"], expected_review=expected_review))


def _submit_removal(prefix, calculation_id, save):
    save("remove_soil_state", dict(calculation_id=calculation_id, as_of=st.session_state[prefix + "remove_day"],
                                    reason=st.session_state[prefix + "remove_reason"]))


def render_soil_revision(call, saved, save):
    prefix = f"field_soil_{saved['id']}_"
    try:
        review = call("review_soil", dict(calculation_id=saved["id"]))
    except ValueError as exc:
        st.error(str(exc))
        return None
    with st.expander("Reconcile soil state with a measurement", expanded=review["needs_review"]):
        st.caption(review["note"])
        if review["needs_review"]:
            st.warning("A linked soil measurement's quality review has changed. Review it before using this calculation; "
                       "adopt an eligible reading again or remove the old state update.")
        if review["linked_measurements"]:
            st.dataframe([dict(as_of=r["snapshot"]["as_of"], observation_id=r["snapshot"]["observation"]["id"],
                saved_quality=r["snapshot"]["observation"]["effective_quality"],
                current_quality=r["current_quality"], review_changed=r["review_changed"])
                for r in review["linked_measurements"]], hide_index=True)
        if review["updates"]:
            st.dataframe(review["updates"], hide_index=True)
        roots = saved["inputs"].get("root_zone_schedule", [])
        if roots:
            st.caption("Each state update must represent the full root depth on its end-of-day date. "
                       "The initial boundary uses the initial soil depth; subsequent dates use these supplied depths.")
            st.dataframe([dict(day=r["day"], root_depth_m=r["root_depth_m"]) for r in roots], hide_index=True)
        first = date.fromisoformat(saved["inputs"]["weather"][0]["day"]) - timedelta(days=1)
        last = date.fromisoformat(saved["inputs"]["weather"][-1]["day"])
        readings = {r["id"]: r for r in review["measurements"]
                    if r["effective_quality"] != "rejected" and r["provenance"] != "forecast"}
        if readings:
            selected = st.selectbox("Soil measurement", list(readings), key=prefix + "reading",
                format_func=lambda key: f"{readings[key]['observed_at']} · {readings[key]['value']:g} "
                                        f"{readings[key]['unit']} · {key}")
            reading = readings[selected]
            st.caption(f"{reading['provenance']} · {reading['effective_quality']} · {reading['source']}")
            # Scope inputs to both the saved calculation and reading; switching readings
            # must not reuse a spatial/timing declaration made for a different sample.
            key = prefix + selected + "_"
            current_review = reading["quality_review"]["id"] if reading["quality_review"] else None
            with st.form(key + "form"):
                st.date_input("End-of-day state date", value=None, min_value=first, max_value=last, key=key + "day")
                st.number_input("Represented root-zone depth (m)", min_value=0.0, max_value=10.0,
                                        value=None, key=key + "depth")
                st.caption(f"Initial boundary root depth: {saved['inputs']['soil']['root_depth_m']:g} m. "
                           "Explain how sampling represents that whole depth and field area.")
                st.text_input("Sampling coverage and representation", key=key + "representation")
                st.number_input("Depletion adjustment to closing midnight (mm; positive means drier)",
                                             value=None, key=key + "adjustment")
                st.text_input("Timing adjustment source or explicit zero-change assumption", key=key + "source")
                st.text_input("Reason for adopting this soil state", key=key + "reason")
                st.form_submit_button("Save soil-state revision", on_click=_submit_state,
                                       args=(key, saved["id"], selected, current_review, save))
        else:
            st.info("Record a soil depletion or volumetric water-content measurement in the crop journal first. "
                    "Forecast and rejected readings cannot initialize soil state.")
        if review["updates"]:
            with st.form(prefix + "remove_form"):
                st.selectbox("Soil-state date to remove", [r["as_of"] for r in review["updates"]], key=prefix + "remove_day")
                st.text_input("Reason for removing this soil update", key=prefix + "remove_reason")
                st.form_submit_button("Save revision without this soil update", on_click=_submit_removal,
                                       args=(prefix, saved["id"], save))
