"""Deterministic crop journal for the selected production unit."""

import hashlib
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

from .calendar_ui import render_calendar_summary, render_crop_calendar
from .cash_ui import render_cash_records, render_cash_totals
from .fertilizer_ui import render_fertilizer_records, render_fertilizer_totals
from .harvest_review_ui import render_harvest_review_summary, render_harvest_reviews
from .season_comparison_ui import render_season_comparison
from .soil_report_ui import render_soil_summary


def _request_id(operation, data):
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return f"web-crop-{operation}:{digest}"


def _journal_date(label, previous, planted, last_day, key):
    value = date.fromisoformat(previous) if previous else None
    selected = st.session_state.get(key, value)
    if selected is not None and not planted <= selected <= last_day:
        st.session_state[key] = None
        st.caption("The previous date is outside this cycle's dates. Enter the actual date, or update the cycle first.")
    if value is not None and not planted <= value <= last_day:
        value = None
    return st.date_input(label, value=value, min_value=planted, max_value=last_day, key=key)


def _save_observation(call, cycle_id, kind, prior_id, prefix):
    # Run before the page renders so revisions cannot leave stale form widgets behind.
    fields = {"stage": ("stage",), "scouting": ("symptoms", "inspected_count", "affected_count"),
              "transplant": ("origin", "plant_count")}[kind]
    data = {key: st.session_state.get(prefix+key) for key in
            ("entry", "day", "location", "source", "provenance", *fields)}
    data["day"] = data["day"].isoformat() if data["day"] else None
    request = dict(cycle_id=cycle_id, supersedes_id=prior_id, data=data)
    operation = f"record_{kind}"
    try:
        call(operation, dict(request, request_id=_request_id(operation, request)))
        st.session_state.crop_notice = "Transplant saved; history preserved." if kind == "transplant" else "Observation saved; history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _void_observation(call, selector, operation="void_observation"):
    data = dict(record_id=st.session_state.get(selector), reason=st.session_state.get(selector+"_reason"))
    try:
        call(operation, dict(data, request_id=_request_id(operation, data)))
        st.session_state.crop_notice = "Entry voided; history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _save_harvest(call, cycle_id, prior_id, prefix):
    data = {key: st.session_state.get(prefix+key) for key in
            ("batch", "day", "quantity", "unit", "product", "basis", "grade", "source", "provenance")}
    data["day"] = data["day"].isoformat() if data["day"] else None
    request = dict(cycle_id=cycle_id, supersedes_id=prior_id, data=data)
    try:
        call("record_harvest", dict(request, request_id=_request_id("harvest", request)))
        st.session_state.crop_notice = "Harvest batch saved; totals use its latest revision."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _void_harvest(call, cycle_id):
    data = dict(record_id=st.session_state.get(f"crop_void_record_{cycle_id}"),
                reason=st.session_state.get(f"crop_void_reason_{cycle_id}"))
    try:
        call("void_harvest", dict(data, request_id=_request_id("void", data)))
        st.session_state.crop_notice = "Batch voided; history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _correct_planting(call, cycle_id, revision, prefix):
    data = dict(cycle_id=cycle_id, expected_revision=revision,
                **{key: st.session_state.get(prefix+key) for key in ("crop", "planted_on", "source", "reason")})
    data["planted_on"] = data["planted_on"].isoformat() if data["planted_on"] else None
    try:
        call("correct_planting", dict(data, request_id=_request_id("correct_planting", data)))
        st.session_state.crop_notice = "Planting corrected; original history and saved calculations preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _planting_controls(call, journal, cycle_id, today):
    state, cycle = journal["planting"], journal["cycle"]
    with st.expander("Correct this planting"):
        st.caption("Correct a mistaken crop name or actual planting date. Use a new cycle for a new planting. "
                   "Current activities and fertilizer phases must remain consistent with the date. "
                   "Saved calculations keep their original inputs; review them after a correction.")
        prefix = f"crop_planting_correction_{cycle_id}_{state['revision_id'] or 'initial'}_"
        with st.form(prefix+"form"):
            st.text_input("Correct crop name", value=cycle["crop"], key=prefix+"crop")
            _journal_date("Correct planting date", cycle["planted_on"], date(1000, 1, 1), today, prefix+"planted_on")
            st.text_input("Planting correction source", key=prefix+"source")
            st.text_input("Reason for planting correction", key=prefix+"reason")
            st.form_submit_button("Save planting correction", on_click=_correct_planting,
                                  args=(call, cycle_id, state["revision_id"], prefix))
        history = [r for r in journal["records"] if r["kind"] == "planting"]
        if history:
            st.dataframe([dict(r["data"], record_id=r["id"], recorded_at=r["created_at"],
                              reason=r["reason"], superseded=r["superseded"]) for r in history])
        else:
            st.caption("This imported cycle has no planting-source record. Its current values will be preserved with the first correction.")


def _change_lifecycle(call, cycle_id, revision, operation, prefix):
    data = dict(cycle_id=cycle_id, expected_revision=revision,
                reason=st.session_state.get(prefix+"reason"), source=st.session_state.get(prefix+"source"))
    if operation == "close_cycle":
        ended = st.session_state.get(prefix+"ended_on")
        data["ended_on"] = ended.isoformat() if ended else None
    try:
        call(operation, dict(data, request_id=_request_id(operation, data)))
        st.session_state.crop_notice = "Cycle closed; records retained." if operation == "close_cycle" else "Cycle reopened; history retained."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _review_measurement(call, observation_id, revision, prefix):
    data = dict(observation_id=observation_id, expected_revision=revision,
                **{key: st.session_state.get(prefix+key) for key in ("quality", "reason", "source")})
    try:
        call("review_measurement", dict(data, request_id=_request_id("review_measurement", data)))
        st.session_state.crop_notice = "Measurement review saved; original evidence and review history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _measurement_reviews(call):
    listing = call("list_measurements", {})
    rows = {r["id"]: r for r in listing["measurements"]}
    with st.expander("Review field measurements"):
        st.caption("Review recorded field measurements in this unit, including those without a crop cycle. "
                   "These are separate from scouting entries and daily water totals.")
        if not rows:
            st.info("No field measurements recorded in this unit.")
            return
        st.dataframe([{"Observed at": r["observed_at"], "Quantity": r["quantity"], "Value": r["value"], "Unit": r["unit"],
                       "Provenance": r["provenance"], "Source": r["source"], "Original quality": r["quality"],
                       "Current quality": r["effective_quality"], "Crop cycle": r["cycle_id"]} for r in rows.values()])
        st.download_button("Download field measurements", json.dumps(listing, indent=2), file_name="field-measurements.json",
                           mime="application/json", key="crop_measurements_download")
        selector = "crop_measurement"
        if st.session_state.get(selector) not in rows:
            st.session_state[selector] = None
        observation_id = st.selectbox("Measurement to review", list(rows), index=None, key=selector,
            format_func=lambda key: f"{rows[key]['observed_at']} · {rows[key]['quantity']} · {rows[key]['value']} {rows[key]['unit']} · {key[:8]}")
        if observation_id is None:
            return
        row = rows[observation_id]
        revision = row["quality_review"]["id"] if row["quality_review"] else None
        prefix = f"crop_measurement_review_{observation_id}_{revision or 'initial'}_"
        st.caption("Checked means you reviewed the evidence; Agronaut does not independently validate it. "
                   "Rejected observations stop constraining cycle closure. Reviews do not change values, dates or provenance.")
        with st.form(prefix+"form"):
            st.selectbox("Reviewed quality", ["unverified", "checked", "rejected"], index=None, key=prefix+"quality")
            st.text_input("Review reason", key=prefix+"reason")
            st.text_input("Review source", key=prefix+"source")
            st.form_submit_button("Save measurement review", on_click=_review_measurement,
                                  args=(call, observation_id, revision, prefix))
        if row["quality_history"]:
            st.dataframe([{"Reviewed at": r["created_at"], "Quality": r["quality"], "Reason": r["reason"], "Source": r["source"]}
                          for r in row["quality_history"]])


def _lifecycle_controls(call, journal, cycle_id, planted, today):
    state = journal["lifecycle"]
    closed = state["status"] == "closed"
    st.caption(f"Closed · ended {state['ended_on']}" if closed else "Open · not marked ended")
    if closed:
        st.info("Late crop activity records and corrections through the end date are allowed. Reopen this cycle or correct its end date "
                "before recording later activity. Use a new cycle for a new planting.")
    with st.expander("Manage crop cycle"):
        st.caption("Close only when this planting has ended, including an unsuccessful or abandoned crop. "
                   "Closure preserves records and does not imply a successful harvest.")
        prefix = f"crop_close_{cycle_id}_{state['revision_id'] or 'initial'}_"
        with st.form(prefix+"form"):
            st.date_input("Cycle ended on", value=date.fromisoformat(state["ended_on"]) if closed else None,
                          min_value=planted, max_value=today, key=prefix+"ended_on")
            st.text_input("Reason for closing or correcting", key=prefix+"reason")
            st.text_input("Closure source", key=prefix+"source")
            st.form_submit_button("Correct cycle end" if closed else "Close cycle", on_click=_change_lifecycle,
                args=(call, cycle_id, state["revision_id"], "close_cycle", prefix))
        if closed:
            prefix = f"crop_reopen_{cycle_id}_{state['revision_id']}_"
            with st.form(prefix+"form"):
                st.text_input("Reason for reopening", key=prefix+"reason")
                st.text_input("Reopening source", key=prefix+"source")
                st.form_submit_button("Reopen cycle", on_click=_change_lifecycle,
                    args=(call, cycle_id, state["revision_id"], "reopen_cycle", prefix))
        history = [r for r in journal["records"] if r["kind"] == "lifecycle"]
        if history:
            st.dataframe([dict(r["data"], recorded_at=r["created_at"], superseded=r["superseded"])
                          for r in history])


def _observations(call, journal, cycle_id, planted, today):
    for kind, label in (("stage", "Growth stage"), ("scouting", "Scouting"), ("transplant", "Transplant")):
        rows = [r for r in journal["records"] if r["kind"] == kind]
        current = {r["id"]: r for r in rows if not r["superseded"]}
        selector = f"crop_{kind}_correction_{cycle_id}"
        if st.session_state.get(selector) not in (None, *current):
            st.session_state[selector] = None
        with st.expander(f"Record or correct {label.lower()}"):
            st.caption("Record completed transfers into this planting, including replacements or partial batches. "
                       "Counts stay separate; this does not move another unit's inventory or change the planting date."
                       if kind == "transplant" else "Record what was observed, with its date, location and source. "
                       "Corrections preserve history; correcting a voided entry restores it.")
            prior_id = st.selectbox(f"{label} entry", [None, *current], key=selector,
                format_func=lambda key, current=current: "New entry" if key is None else
                f"Correct {current[key]['data']['entry']}" + (" (voided)" if current[key]["voided"] else ""))
            old = current[prior_id]["data"] if prior_id else {}
            prefix = f"crop_{kind}_{cycle_id}_{prior_id or 'new'}_"
            with st.form(prefix+"form"):
                st.text_input(f"{label} entry ID", value=old.get("entry", ""),
                                      disabled=prior_id is not None, key=prefix+"entry")
                _journal_date("Transplant date" if kind == "transplant" else "Observation date", old.get("day"), planted, today, prefix+"day")
                st.text_input("Transplant location or extent" if kind == "transplant" else "Observed location or extent",
                              value=old.get("location", ""), key=prefix+"location")
                if kind == "stage":
                    st.text_input("Observed growth stage", value=old.get("stage", ""), key=prefix+"stage")
                    st.caption("Stage labels do not automatically change irrigation coefficients.")
                elif kind == "transplant":
                    st.text_input("Seedling origin or nursery batch", value=old.get("origin", ""), key=prefix+"origin")
                    st.number_input("Plants transplanted (blank if unknown)", value=old.get("plant_count"),
                                    min_value=1, max_value=1_000_000_000, step=1, key=prefix+"plant_count")
                else:
                    st.text_area("Observed symptoms", value=old.get("symptoms", ""),
                        key=prefix+"symptoms", help="Describe symptoms, or explicitly enter none observed. This is not a diagnosis.")
                    st.caption("Count plants in this sample only. Leave both counts blank if unknown. Samples are kept separate.")
                    for key, title in (("inspected_count", "Plants inspected"), ("affected_count", "Plants with described symptoms")):
                        st.number_input(title, value=old.get(key), min_value=0, max_value=1_000_000_000,
                                                      step=1, key=prefix+key)
                provenance = ["measured", "reported", "estimated"] if kind == "transplant" else ["observed", "reported"]
                st.selectbox("Transplant provenance" if kind == "transplant" else "Observation provenance", provenance,
                    index=provenance.index(old["provenance"]) if old else None, key=prefix+"provenance")
                st.text_input("Transplant record source" if kind == "transplant" else "Observation source", key=prefix+"source")
                st.form_submit_button(f"Save {label.lower()}", on_click=_save_observation,
                                      args=(call, cycle_id, kind, prior_id, prefix))
        eligible = {key: row for key, row in current.items() if not row["voided"]}
        if eligible:
            selector = f"crop_{kind}_void_{cycle_id}"
            if st.session_state.get(selector) not in eligible:
                st.session_state[selector] = next(iter(eligible))
            with st.expander(f"Void an incorrect {label.lower()} entry"):
                st.selectbox("Entry to void", list(eligible), key=selector,
                                         format_func=lambda key, eligible=eligible: eligible[key]["data"]["entry"])
                st.text_input("Reason for voiding", key=selector+"_reason")
                st.button(f"Void {label.lower()}", key=selector+"_submit", on_click=_void_observation,
                          args=(call, selector, "void_transplant" if kind == "transplant" else "void_observation"))
        if rows:
            st.subheader(f"{label} history")
            st.dataframe([dict(r["data"], record_id=r["id"], superseded=r["superseded"], voided=r["voided"], reason=r["reason"])
                          for r in rows])
    if journal["latest_stage_reports"]:
        st.subheader("Latest reported growth stages")
        st.caption("All current stage reports on the latest reported date, with their locations. These may cover only part of the crop.")
        st.dataframe([r["data"] for r in journal["latest_stage_reports"]])


def _season_review(call, cycle_id):
    from .resource_outcome_ui import render_season_outcomes
    with st.expander("Review this season"):
        try:
            review = call("season_review", dict(cycle_id=cycle_id))
            calculations = {r["id"]: r for r in review["calculations"] if r["task"] == "root_zone_budget"}
            selector = f"crop_review_calculation_{cycle_id}"
            if st.session_state.get(selector) not in (None, *calculations):
                st.session_state[selector] = None
            selected = st.selectbox("Compare water records with a calculation", [None, *calculations], key=selector,
                format_func=lambda key: "No comparison selected" if key is None else
                f"{calculations[key]['weather_start']} to {calculations[key]['weather_end']} · {calculations[key]['created_at']} · {key[:8]}")
            if selected:
                review = call("season_review", dict(cycle_id=cycle_id, calculation_id=selected))
        except ValueError as exc:
            st.error(str(exc))
            return
        st.caption(f"Recorded outcomes through {review['period']['through']} · {review['period']['timezone']}. "
                   "Cash entries retain their separate payment dates. This snapshot does not establish a complete season or forecast yield.")
        st.write("Current crop records", review["current_record_counts"])
        surface = review.get("surface_water", {})
        if surface.get("history"):
            st.caption(surface["note"])
            st.dataframe([dict(day=r["day"], id=r["id"], quality=r["data"]["quality"],
                provenance=r["data"]["provenance"], source=r["data"]["source"])
                for r in surface["current_records"]], hide_index=True)
        render_calendar_summary(review["journal"]["calendar"])
        if review["journal"]["harvest_totals"]:
            st.dataframe(review["journal"]["harvest_totals"])
        else:
            st.info("No current harvest quantities recorded; yield is unknown.")
        if review["water"]["applicable"]:
            render_fertilizer_totals(review["journal"]["fertilizer"])
        render_cash_totals(review["journal"]["cash"])
        render_harvest_review_summary(review["journal"]["harvest_reviews"])
        render_soil_summary(review["soil_tests"])
        render_season_outcomes(review["irrigation_outcomes"], cycle_id)
        coverage = review["water"]["coverage"]
        if coverage:
            st.write(f"Water logs cover {coverage['recorded_days']} of {coverage['expected_days']} completed days. "
                     f"{coverage['missing_days']} days have no accepted daily totals.")
            if coverage["missing_ranges"]:
                st.dataframe(coverage["missing_ranges"])
            if review["water"]["totals"]:
                st.caption("Water depths in mm, for recorded days only. Provenance and quality remain separate.")
                st.dataframe([{k: v for k, v in row.items() if k != "record_ids"} for row in review["water"]["totals"]])
        else:
            st.info("Daily soil-water log coverage does not apply to this production system.")
        if review["calculations"]:
            st.caption("Saved calculation versions; creating a calculation does not record completed irrigation or fertilizer application. "
                       "Cycle changes and crop/date conflicts refer to the current planting; historical inputs remain unchanged.")
            if any(r["cycle_changed_fields"] or r["cycle_conflicts"] for r in review["calculations"]):
                st.warning("Some saved calculations differ from the current planting. Review their crop, dates and assumptions before reuse.")
            st.dataframe(review["calculations"])
        if review["comparison"]:
            st.caption(review["comparison"]["interpretation"])
            rows = []
            for day in review["comparison"]["days"]:
                row = dict(day=day["day"], status=day["status"], weather_kind=day["weather_kind"],
                           contains_replayed_actual=day["replayed_record_id"] is not None)
                for key in ("rainfall_mm", "net_irrigation_mm", "runoff_mm"):
                    row["input_"+key] = day["calculation_inputs"][key]
                    row["recorded_"+key] = day["recorded"][key] if day["status"] == "matched" else None
                    row["difference_"+key] = day["recorded_minus_input"][key] if day["recorded_minus_input"] else None
                rows.append(row)
            st.dataframe(rows)
        st.download_button("Download season review", json.dumps(review, indent=2),
                           file_name=f"season-{cycle_id}.json", mime="application/json", key=f"crop_review_download_{cycle_id}")


def render_crop_records(brain, user, channel="web"):
    from agent.measurement_ui import render_measurement_entry
    active = next((u for u in brain.list_units(channel, user) if u["active"]), None)
    scope = active["id"] if active else None
    if st.session_state.get("crop_scope_unit") != scope:
        for key in list(st.session_state):
            if key.startswith("crop_"):
                del st.session_state[key]
        st.session_state.crop_scope_unit = scope
    st.header("Crop records")
    if active is None or active["production_system"] not in ("soil", "hydroponics", "aquaponics"):
        st.info("Select a production unit to record crop plantings and harvested batches.")
        return
    st.caption(active["name"])
    if notice := st.session_state.pop("crop_notice", None):
        st.success(notice)
    if error := st.session_state.pop("crop_error", None):
        st.error(error)

    def call(operation, data):
        return brain.crop_records(channel, user, operation, data, expected_unit_id=active["id"])

    try:
        listing = call("list", {})
    except ValueError as exc:
        st.error(str(exc))
        return
    farm = next(f for f in brain.list_farms(channel, user) if f["id"] == active["farm_id"])
    today = datetime.now(ZoneInfo(farm["timezone"])).date()
    render_measurement_entry(call, {c["id"]: c for c in listing["cycles"]}, _request_id)
    _measurement_reviews(call)
    with st.expander("Record a planting"):
        st.caption(f"Use the actual planting date in {farm['timezone']}. Each planting creates a separate crop cycle.")
        with st.form("crop_create_form"):
            crop = st.text_input("Crop", key="crop_name")
            planted = st.date_input("Planted on", value=None, max_value=today, key="crop_planted")
            source = st.text_input("Planting record source", key="crop_planting_source")
            if st.form_submit_button("Save planting"):
                try:
                    data = dict(crop=crop, planted_on=planted.isoformat() if planted else None, source=source)
                    created = call("create", dict(data, request_id=_request_id("create", data)))
                    st.session_state.crop_cycle = created["cycle"]["id"]
                    listing = call("list", {})
                    st.success("Planting saved. This cycle is also available in Field records.")
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
    cycles = {c["id"]: c for c in listing["cycles"]}
    if not cycles:
        st.info("No crop cycles in this unit yet.")
        return
    render_season_comparison(call, cycles)
    selected_cycle = st.session_state.get("crop_cycle")
    if selected_cycle not in cycles:
        open_cycles = [key for key, cycle in cycles.items() if cycle["lifecycle"]["status"] == "open"]
        selected_cycle = open_cycles[-1] if open_cycles else next(reversed(cycles))
    # A changed crop/date/status label must not switch to a different planting.
    st.session_state.crop_cycle = selected_cycle
    cycle_id = st.selectbox("Crop cycle", list(cycles), key="crop_cycle",
                            format_func=lambda key: f"{cycles[key]['crop']} · planted {cycles[key]['planted_on']} · "
                            f"{cycles[key]['lifecycle']['status']} · {key[:8]}")
    journal = call("get", dict(cycle_id=cycle_id))
    _planting_controls(call, journal, cycle_id, today)
    render_crop_calendar(call, journal, cycle_id, today)
    render_cash_records(call, journal, cycle_id, today)
    last_day = date.fromisoformat(journal["lifecycle"]["ended_on"]) if journal["lifecycle"]["status"] == "closed" else today
    if active["production_system"] == "soil":
        render_fertilizer_records(call, journal, cycle_id, last_day)
    if date.fromisoformat(cycles[cycle_id]["planted_on"]) > today:
        st.info("This existing cycle has a future planting date. Crop observation and harvest entry become available after that date.")
        st.download_button("Download crop journal", json.dumps(journal, indent=2),
                            file_name=f"crop-{cycle_id}.json", mime="application/json")
        return
    planted = date.fromisoformat(cycles[cycle_id]["planted_on"])
    if active["production_system"] == "soil":
        render_harvest_reviews(call, journal, cycle_id, planted, last_day)
    _lifecycle_controls(call, journal, cycle_id, planted, today)
    _observations(call, journal, cycle_id, planted, last_day)
    harvests = [r for r in journal["records"] if r["kind"] == "harvest"]
    current = {r["id"]: r for r in harvests if not r["superseded"]}
    correction_key = f"crop_correction_{cycle_id}"
    if st.session_state.get(correction_key) not in (None, *current):
        st.session_state[correction_key] = None
    with st.expander("Record or correct a harvest"):
        st.caption("Enter one completed batch. Keep its batch ID when correcting it. "
                   "Unknown quantities stay blank; forecasts belong in planning.")
        prior_id = st.selectbox("Harvest entry", [None, *current], key=correction_key,
                                format_func=lambda key: "New batch" if key is None else
                                f"Correct {current[key]['data']['batch']}" + (" (voided)" if current[key]["voided"] else ""))
        old = current[prior_id]["data"] if prior_id else {}
        prefix = f"crop_harvest_{cycle_id}_{prior_id or 'new'}_"
        with st.form(prefix + "form"):
            st.text_input("Batch ID", value=old.get("batch", ""), disabled=prior_id is not None, key=prefix+"batch")
            _journal_date("Harvest date", old.get("day"), planted, last_day, prefix+"day")
            st.number_input("Harvested quantity", value=float(old["quantity"]) if old else None,
                                        min_value=0.0, key=prefix+"quantity")
            for name, label, choices in (("unit", "Quantity unit", ["kg", "g", "t", "count"]),
                                         ("basis", "Produce basis", ["fresh", "dry", "as_harvested"]),
                                         ("provenance", "How was the amount obtained?", ["measured", "reported", "estimated"])):
                st.selectbox(label, choices, index=choices.index(old[name]) if old else None, key=prefix+name)
            st.text_input("Harvested product", value=old.get("product", ""), key=prefix+"product",
                                    help="For counts, specify what is counted, such as heads or bunches.")
            st.text_input("Grade", value=old.get("grade", ""), key=prefix+"grade", help="Enter ungraded if no grade was assigned.")
            st.text_input("Harvest record source", key=prefix+"source")
            st.form_submit_button("Save harvest batch", on_click=_save_harvest,
                                  args=(call, cycle_id, prior_id, prefix))
    eligible = {key: row for key, row in current.items() if not row["voided"]}
    if eligible:
        void_key = f"crop_void_record_{cycle_id}"
        if st.session_state.get(void_key) not in eligible:
            st.session_state[void_key] = next(iter(eligible))
        with st.expander("Void an incorrect harvest batch"):
            st.caption("Voiding excludes the batch from totals and keeps its history. A later correction can restore it.")
            st.selectbox("Batch to void", list(eligible), format_func=lambda key: eligible[key]["data"]["batch"],
                                     key=void_key)
            st.text_input("Reason for voiding", key=f"crop_void_reason_{cycle_id}")
            st.button("Void harvest batch", key="crop_void", on_click=_void_harvest, args=(call, cycle_id))
    st.subheader("Harvest totals")
    st.caption("Mass converts to kg. Counts, products, produce basis, grades and provenance stay separate. "
               "Totals exclude voided and superseded records; they are not yield forecasts.")
    if journal["harvest_totals"]:
        st.dataframe(journal["harvest_totals"])
    else:
        st.info("No current harvest quantities recorded for this cycle.")
    if harvests:
        st.subheader("Harvest history")
        st.dataframe([dict(r["data"], record_id=r["id"], superseded=r["superseded"], voided=r["voided"], reason=r["reason"])
                      for r in harvests])
    st.download_button("Download crop journal", json.dumps(journal, indent=2),
                        file_name=f"crop-{cycle_id}.json", mime="application/json")
    _season_review(call, cycle_id)
