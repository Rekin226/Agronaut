"""Explicit ground-area reviews of selected harvest batches, without a model."""

import json

import streamlit as st


def harvest_review_rows(report):
    data = report["data"]
    return [dict(review_id=report["id"], entry=data["entry"], start=data["start"], end=data["end"],
        area_m2=data["area_m2"], location=data["location"], coverage=data["coverage"],
        needs_review=report["review"]["needs_review"], flags=", ".join(report["review"]["flags"]),
        **{k: group[k] for k in ("product", "basis", "grade", "provenance", "unit", "per_ha_unit")},
        recorded_quantity=group["quantity"]["display"], recorded_per_ha=group["per_ha"]["display"],
        exact_per_ha=group["per_ha"]["exact"], batches=len(group["record_ids"])) for group in report["accounting"]["groups"]]


def render_harvest_review_summary(summary):
    st.caption(summary["note"])
    rows = [row for report in summary["current"] for row in harvest_review_rows(report)]
    if rows:
        st.caption("Recorded harvest per hectare of stated harvested ground area. Displays round to six decimal places; exact fractions are retained.")
        st.dataframe(rows)
    else:
        st.info("No current harvested-area reviews; harvest per area remains unknown.")


def _save(call, cycle_id, prior, prefix, selector):
    from .crop_ui import _request_id

    data = {key: st.session_state.get(prefix+key) for key in ("entry", "start", "end", "area_m2", "location",
            "area_source", "coverage", "scope_note", "source", "harvest_record_ids")}
    for key in ("start", "end"):
        data[key] = data[key].isoformat() if data[key] else None
    request = dict(cycle_id=cycle_id, data=data, supersedes_id=prior["id"] if prior else None, reason=st.session_state.get(prefix+"reason") or None)
    if prior and not prior["voided"] and data == prior["data"] and request["reason"] is None:
        st.session_state.crop_notice = "This harvest review is already saved. Supply a reason to reassess its evidence."
        return
    try:
        result = call("record_harvest_review", dict(request, request_id=_request_id("record_harvest_review", request)))
        st.session_state[selector] = result["id"]
        st.session_state.crop_notice = "Harvest review saved with its area, batches and collection coverage."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _void(call, prior_id, prefix, selector):
    from .crop_ui import _request_id

    request = dict(record_id=prior_id, reason=st.session_state.get(prefix+"reason"))
    try:
        result = call("void_harvest_review", dict(request, request_id=_request_id("void_harvest_review", request)))
        st.session_state[selector] = result["id"]
        st.session_state.crop_notice = "Harvest review voided; original evidence preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def render_harvest_reviews(call, journal, cycle_id, planted, last_day):
    from .crop_ui import _journal_date

    summary = journal["harvest_reviews"]
    latest = {r["id"]: r for r in summary["history"] if not r["superseded"]}
    batches = {r["id"]: r for r in journal["records"] if r["kind"] == "harvest" and not r["superseded"] and not r["voided"]}
    with st.expander("Review harvest per area"):
        st.caption("Select whole batches from one harvested ground footprint. Enter that footprint once, even for repeated pickings. "
                   "If a batch mixes areas, correct the batch records before reviewing it. State whether collection is complete for these dates and this footprint.")
        selector = f"crop_harvest_review_choice_{cycle_id}"
        if st.session_state.get(selector) not in (None, *latest):
            st.session_state[selector] = None
        prior_id = st.selectbox("Harvest review", [None, *latest], key=selector,
            format_func=lambda key: "New harvest review" if key is None else f"{latest[key]['data']['entry']} · {key[:8]}")
        prior = latest.get(prior_id)
        old = prior["data"] if prior else {}
        prefix = f"crop_harvest_review_{cycle_id}_{prior_id or 'new'}_"
        # Keep stale IDs visible until the operator explicitly replaces them; never silently drop evidence.
        known = dict(batches)
        if prior:
            known.update({r["id"]: r for r in prior["accounting"]["selected_records"]})
        with st.form(prefix+"form"):
            st.text_input("Harvest review ID", value=old.get("entry", ""), disabled=prior_id is not None, key=prefix+"entry")
            _journal_date("Harvest review start", old.get("start"), planted, last_day, prefix+"start")
            _journal_date("Harvest review end", old.get("end"), planted, last_day, prefix+"end")
            st.multiselect("Whole harvest batches", list(known), default=old.get("harvest_record_ids", []), key=prefix+"harvest_record_ids",
                format_func=lambda key: f"{known[key]['data']['batch']} · {known[key]['data']['day']} · {key[:8]}")
            if any(key not in batches for key in old.get("harvest_record_ids", [])):
                st.warning("A selected batch has been revised or voided. Replace its selection before saving a correction.")
            st.text_input("Harvested ground area (m²)", value=old.get("area_m2", ""), key=prefix+"area_m2",
                          help="Positive decimal, e.g. 250.5. Do not add the same footprint again for successive pickings.")
            st.text_input("Harvested footprint or location", value=old.get("location", ""), key=prefix+"location")
            st.text_input("Area measurement or estimate source", value=old.get("area_source", ""), key=prefix+"area_source")
            options = ["complete", "partial", "unknown"]
            st.selectbox("Harvest collection coverage", options, index=options.index(old["coverage"]) if old else None, key=prefix+"coverage")
            st.text_area("How these batches cover this footprint and period", value=old.get("scope_note", ""), key=prefix+"scope_note")
            st.text_input("Harvest review source", value=old.get("source", ""), key=prefix+"source")
            st.text_input("Reason for correction or void", key=prefix+"reason")
            st.form_submit_button("Save harvest review", on_click=_save, args=(call, cycle_id, prior, prefix, selector))
            if prior and not prior["voided"]:
                st.form_submit_button("Void harvest review", on_click=_void, args=(call, prior_id, prefix, selector))
        render_harvest_review_summary(summary)
        if summary["history"]:
            history = {r["id"]: r for r in summary["history"]}
            selected = st.selectbox("Harvest review history", list(history), key=f"crop_harvest_review_history_{cycle_id}",
                format_func=lambda key: f"{history[key]['data']['entry']} · {key[:8]}" +
                (" · superseded" if history[key]["superseded"] else " · voided" if history[key]["voided"] else " · current"))
            report = history[selected]
            st.dataframe(harvest_review_rows(report))
            st.write({key: report["data"][key] for key in ("area_source", "scope_note", "source")})
            if report["reason"]:
                st.caption(report["reason"])
            st.download_button("Download harvest reviews", json.dumps(summary, indent=2), file_name="harvest-reviews.json",
                               mime="application/json", key=f"crop_harvest_review_download_{cycle_id}")
