"""Explicit event-total comparisons beside saved linked irrigation work."""

import hashlib
import json
from datetime import date

import streamlit as st


def _changed():
    st.session_state.pop("resource_outcome_result", None)


def _submit(call, saved, job_id, prefix, candidates, latest, save):
    day = st.session_state.get(prefix+"day")
    data = dict(actual_day=day.isoformat() if day else None,
                attribution=st.session_state.get(prefix+"attribution"), source=st.session_state.get(prefix+"source"))
    for key in ("water", "pump"):
        identifier = st.session_state.get(prefix+key)
        if identifier and identifier not in candidates:
            st.session_state.resource_outcome_error = "Measurement candidates changed; reload and review the selection."
            return
        data[key] = dict(observation_id=identifier, expected_review_id=candidates[identifier]["expected_review_id"]) if identifier else None
    request = dict(plan_id=saved["id"], job_id=job_id, data=data)
    if save:
        request["supersedes_id"] = latest["id"] if latest else None
        request["request_id"] = "web-outcome-"+hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    try:
        if save and latest and latest["inputs"] == data:
            st.session_state.resource_outcome_result = latest
            st.session_state.resource_outcome_notice = "These inputs already have a saved review. No new revision created."
        else:
            st.session_state.resource_outcome_result = call("save_outcome" if save else "evaluate_outcome", request)
            if save:
                st.session_state.resource_outcome_notice = "Outcome review saved. Measurements, water logs and task status are unchanged."
    except (ValueError, TypeError) as exc:
        _changed()
        st.session_state.resource_outcome_error = str(exc)


def comparison_rows(result):
    return [dict(metric=row["metric"], resource=row["resource_id"], unit=row["unit"],
        allowance=row["planned_allowance"]["value"], observed=row["observed_total"]["value"] if row["observed_total"] else "Unknown",
        difference=row["observed_minus_allowance"]["value"] if row["observed_minus_allowance"] else "Unknown",
        relation=row["relation"], provenance=row["provenance"], quality=row["quality"], observation_id=row["observation_id"])
        for row in result["comparisons"]]


def _result(result):
    st.write(dict(planned_day=result["planned_day"], actual_day=result["actual_day"], timezone=result["timezone"],
                  actual_minus_planned_days=result["actual_minus_planned_days"]))
    st.dataframe(comparison_rows(result))
    st.write("Event attribution", result["inputs"]["attribution"])
    st.write("Review source", result["inputs"]["source"])
    if result["plan_review"]["needs_review"]:
        st.warning("The plan had assumptions needing review when this comparison was made.")
        st.write(result["plan_review"])
    if result.get("review", {}).get("needs_review"):
        st.warning("The saved comparison has current evidence or plan-review flags. Its original values have not changed.")
        st.write(result["review"])
    for limit in result["limits"]:
        st.caption(limit)


def render_season_outcomes(section, cycle_id):
    st.subheader("Irrigation event comparisons")
    st.caption(section["note"])
    if not section["reviews"]:
        st.info("No irrigation outcome reviews assigned to this crop cycle; comparison history is unknown. Create reviews from a saved scenario in Farm resources.")
        return
    st.write("Outcome review counts", section["counts"])
    current = [row for row in section["reviews"] if not row["superseded"]]
    st.dataframe([dict(review_id=row["id"], plan_id=row["plan_id"], job_id=row["job_id"],
        planned_day=row["planned_day"], actual_day=row["actual_day"], timezone=row["timezone"],
        needs_review=row["review"]["needs_review"], **metric) for row in current for metric in comparison_rows(row)])
    if section["counts"]["current_needing_review"]:
        st.warning("Some current irrigation comparisons need evidence or plan-assumption review. Open their history below.")
    by_id = {row["id"]: row for row in reversed(section["reviews"])}
    selector = "crop_outcome_history_"+cycle_id
    if st.session_state.get(selector) not in by_id:
        st.session_state[selector] = next(iter(by_id))
    selected = st.selectbox("Irrigation outcome review history", list(by_id), key=selector,
        format_func=lambda key: f"{by_id[key]['actual_day']} · {by_id[key]['job_id']} · {key[:8]} · "+
        ("superseded" if by_id[key]["superseded"] else "current"))
    st.caption("Selected review is "+("superseded." if by_id[selected]["superseded"] else "current for this plan and job.")+
               " Full source snapshots and history are included in the season download. Make corrections in Farm resources.")
    _result(by_id[selected])


def render_resource_outcomes(call, saved):
    if st.session_state.get("resource_outcome_scope") != saved["id"]:
        for key in list(st.session_state):
            if key.startswith("resource_outcome_"):
                del st.session_state[key]
        st.session_state.resource_outcome_scope = saved["id"]
    eligible = {j["id"]: j for j in saved["inputs"]["jobs"] if "irrigation_design" in j}
    eligible = {r["job_id"]: eligible[r["job_id"]] for r in saved["result"]["allocations"]
                if r["job_id"] in eligible and r["allocated_blocks"] == 1}
    with st.expander("Compare irrigation plan with recorded event totals"):
        if not eligible:
            st.info("This plan needs an allocated job linked to a saved irrigation design before an event-total comparison is available.")
            return
        if notice := st.session_state.pop("resource_outcome_notice", None):
            st.success(notice)
        if error := st.session_state.pop("resource_outcome_error", None):
            st.error(error)
        if st.session_state.get("resource_outcome_job") not in eligible:
            st.session_state.resource_outcome_job = None
        job_id = st.selectbox("Irrigation job to review", list(eligible), index=None, key="resource_outcome_job", on_change=_changed,
                             format_func=lambda key: f"{eligible[key]['title']} · {eligible[key]['irrigation_design']['day']}")
        if job_id is None:
            return
        catalog = call("outcome_inputs", dict(plan_id=saved["id"], job_id=job_id))
        history = [row for row in call("list_outcomes", dict(plan_id=saved["id"]))["reviews"] if row["job_id"] == job_id]
        latest = next((row for row in history if not row["superseded"]), None)
        candidates = {row["id"]: row for row in catalog["observations"] if row["effective_quality"] != "rejected" and row["provenance"] != "forecast"}
        st.caption("Select complete event totals from Crop records → Record a field measurement: irrigation_volume in L and runtime in h. "
                   "Explicitly explain their attribution to this full event, including maintenance and the mapped source/pump. "
                   "Net depth, flow rates, cumulative meter counters and totals covering other work need a different basis; no conversion is inferred.")
        st.caption("Actual dates use the saved plan timezone: "+catalog["timezone"]+". An unselected metric stays unknown. "
                   "Differences from conservative allowances are not savings or proof of irrigation adequacy.")
        if not candidates:
            st.info("No non-rejected event-volume or runtime observations are available in this job's exact unit and crop cycle.")
        prefix = "resource_outcome_"+job_id+"_"
        defaults = latest["inputs"] if latest else {}
        for key, quantity in (("water", "irrigation_volume"), ("pump", "runtime")):
            ids = [identifier for identifier, row in candidates.items() if row["quantity"] == quantity]
            if prefix+key not in st.session_state:
                st.session_state[prefix+key] = (defaults.get(key) or {}).get("observation_id")
            if st.session_state.get(prefix+key) not in ids:
                st.session_state[prefix+key] = None
        with st.form(prefix+"form"):
            previous_day = date.fromisoformat(defaults["actual_day"]) if defaults.get("actual_day") else None
            st.date_input("Actual event date in saved plan timezone", value=previous_day if previous_day and previous_day.year >= 1000 else None,
                          min_value=date(1000, 1, 1), max_value=date.max, key=prefix+"day")
            for key, quantity, label in (("water", "irrigation_volume", "Source-water total"), ("pump", "runtime", "Pump operating-time total")):
                ids = [identifier for identifier, row in candidates.items() if row["quantity"] == quantity]
                selected_id = st.selectbox(label, [None, *ids], key=prefix+key, format_func=lambda identifier:
                    "Unknown / no selected reading" if identifier is None else
                    f"{candidates[identifier]['value']} {candidates[identifier]['unit']} · {candidates[identifier]['observed_at']} · {candidates[identifier]['provenance']} · {identifier[:8]}")
                if selected_id:
                    st.caption(f"{label} selected quality: {candidates[selected_id]['effective_quality']}. Quality is checked again on submission.")
            st.text_input("Why these readings cover this whole event and mapped resources", value=defaults.get("attribution", ""), key=prefix+"attribution")
            st.text_input("Review source and correction reason, if applicable", value=defaults.get("source", ""), key=prefix+"source")
            st.form_submit_button("Evaluate event comparison", on_click=_submit, args=(call, saved, job_id, prefix, candidates, latest, False))
            st.form_submit_button("Save outcome review", on_click=_submit, args=(call, saved, job_id, prefix, candidates, latest, True))
        if latest:
            st.caption("Saving changed inputs creates a revision of the current review; earlier evidence is retained.")
        if result := st.session_state.get("resource_outcome_result"):
            st.subheader("Submitted event comparison")
            st.caption("This is the submitted snapshot. Re-evaluate after changing inputs or measurement reviews.")
            _result(result)
            st.download_button("Download event comparison", json.dumps(result, indent=2), "irrigation-outcome.json", "application/json")
        if history:
            by_id = {row["id"]: row for row in reversed(history)}
            selected = st.selectbox("Saved outcome review history", list(by_id), key=prefix+"history", format_func=lambda key:
                f"{by_id[key]['actual_day']} · {key[:8]} · "+("superseded" if by_id[key]["superseded"] else "current"))
            st.subheader("Saved outcome review")
            _result(by_id[selected])
            st.download_button("Download saved outcome review", json.dumps(by_id[selected], indent=2), f"outcome-{selected}.json", "application/json")
