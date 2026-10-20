"""Explicit coefficient curves and journal-linked revisions without model inference."""

from datetime import date

import streamlit as st


def coefficient_selector(prefix, initial=None):
    initial = initial or {}
    schedule = initial.get("coefficient_schedule", [])
    mode = st.selectbox("Crop coefficient input", ["Fixed coefficient", "Dated coefficients"],
                        index=1 if schedule else 0, key=prefix + "coefficient_mode")
    count = 0
    if mode == "Dated coefficients":
        count = st.number_input("Number of coefficient periods", min_value=1, max_value=1000,
                                 value=len(schedule) or 1, key=prefix + "period_count")
        st.caption("Enter inclusive dates and Kc values at both endpoints. Equal values give a constant period; "
                   "different values are interpolated linearly by calendar day. Periods must be consecutive "
                   "and cover every weather day. No dates or coefficients are inferred from a crop name.")
    return mode == "Dated coefficients", count


def coefficient_fields(prefix, scheduled, count, initial=None):
    initial = initial or {}
    def number(label, key, value):
        return st.number_input(label, min_value=0.0, max_value=3.0,
                               value=float(value) if value is not None else None, key=prefix + key)
    if not scheduled:
        old = initial.get("coefficient", {})
        stage = st.text_input("Crop stage", value=old.get("stage", ""), key=prefix + "stage")
        value = number("Crop coefficient (Kc)", "kc", old.get("value"))
        source = st.text_input("Source and applicability of crop coefficient", value=old.get("source", ""),
                               key=prefix + "kc_source")
        return dict(coefficient=dict(value=value, stage=stage, source=source))
    old_periods = initial.get("coefficient_schedule", [])
    periods = []
    for index in range(count):
        old = old_periods[index] if index < len(old_periods) else {}
        key = f"kc_period_{index}_"
        st.markdown(f"**Coefficient period {index + 1}**")
        start = st.date_input("Period start (inclusive)", value=date.fromisoformat(old["start"]) if old else None,
                              key=prefix + key + "start")
        end = st.date_input("Period end (inclusive)", value=date.fromisoformat(old["end"]) if old else None,
                            key=prefix + key + "end")
        periods.append(dict(start=start.isoformat() if start else None, end=end.isoformat() if end else None,
            start_value=number("Kc on the first date", key + "start_value", old.get("start_value")),
            end_value=number("Kc on the last date", key + "end_value", old.get("end_value")),
            stage=st.text_input("Stage for this period", value=old.get("stage", ""), key=prefix + key + "stage"),
            source=st.text_input("Source and applicability of dates and Kc values", value=old.get("source", ""),
                                 key=prefix + key + "source")))
    return dict(coefficient_schedule=periods)


def _submit_revision(prefix, scheduled, count, calculation_id, save):
    state = st.session_state
    if scheduled:
        periods = []
        for index in range(count):
            key = f"{prefix}kc_period_{index}_"
            periods.append({name: state[key + name].isoformat() if state[key + name] else None
                            for name in ("start", "end")})
            periods[-1].update({name: state[key + name] for name in ("start_value", "end_value", "stage", "source")})
        coefficients = dict(coefficient_schedule=periods)
    else:
        coefficients = dict(coefficient=dict(value=state[prefix + "kc"], stage=state[prefix + "stage"],
                                             source=state[prefix + "kc_source"]))
    save("revise_crop", dict(calculation_id=calculation_id, coefficients=coefficients,
                             reason=state[prefix + "reason"], stage_record_ids=state[prefix + "stage_records"]))


def render_crop_revision(call, saved, save):
    prefix = f"field_crop_{saved['id']}_"
    try:
        review = call("review_crop", dict(calculation_id=saved["id"]))
    except ValueError as exc:
        st.error(str(exc))
        return None
    cycle_review = review["cycle_review"]
    needs_review = review["needs_stage_review"] or bool(
        cycle_review and (cycle_review["changed_fields"] or cycle_review["conflicts"]))
    with st.expander("Review growth stages and revise crop coefficients", expanded=needs_review):
        st.caption(review["note"])
        st.caption("Recalculate from the saved initial soil state. Weather, recorded water, area, crop identity "
                   "and soil properties stay attached. Supplied root depths stay attached.")
        if review["needs_stage_review"]:
            st.warning("A linked stage report has been corrected or voided. Review the current reports before revising coefficients.")
        if cycle_review and (cycle_review["changed_fields"] or cycle_review["conflicts"]):
            st.warning("The crop or planting date has changed since this calculation was saved.")
            for conflict in cycle_review["conflicts"]:
                st.caption(conflict)
        if review["linked_stage_reports"]:
            st.dataframe([dict(id=entry["snapshot"]["id"], **entry["snapshot"]["data"],
                               superseded=entry["superseded"]) for entry in review["linked_stage_reports"]], hide_index=True)
        scheduled, count = coefficient_selector(prefix, saved["inputs"]["crop"])
        reports = {record["id"]: record for record in review["current_stage_reports"]}
        selected = st.session_state.get(prefix + "stage_records",
                                        [r["id"] for r in saved.get("crop_stage_evidence", [])])
        st.session_state[prefix + "stage_records"] = [key for key in selected if key in reports]
        with st.form(prefix + "form"):
            coefficient_fields(prefix, scheduled, count, saved["inputs"]["crop"])
            st.multiselect("Stage reports supporting this revision (optional)", list(reports),
                format_func=lambda key: f"{reports[key]['data']['day']} · {reports[key]['data']['stage']} · "
                                        f"{reports[key]['data']['location']} · {key[:8]}", key=prefix + "stage_records")
            st.text_input("Reason for changing coefficients or dates", key=prefix + "reason")
            st.form_submit_button("Save crop coefficient revision", on_click=_submit_revision,
                                   args=(prefix, scheduled, count, saved["id"], save))
