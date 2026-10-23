"""Laboratory soil-report entry and history, without interpreting reported values."""

import csv
import hashlib
import json
from datetime import date, datetime
from io import StringIO
from zoneinfo import ZoneInfo

import streamlit as st

_QUALIFIERS = {"=": "eq", "<": "lt", "<=": "le", ">": "gt", ">=": "ge", "NR": "not_reported", "ND": "not_detected", "TEXT": "text"}
_LABELS = {v: k for k, v in _QUALIFIERS.items()}


def soil_result_rows(report):
    data = report["data"]
    return [dict(report_id=report["id"], entry=data["entry"], sampled_on=data["sampled_on"], reported_on=data["reported_on"],
        lab=data["lab_name"], lab_report=data["lab_report_id"], sample=data["sample_id"], location=data["location"],
        depth_top_cm=data["depth_top_cm"], depth_bottom_cm=data["depth_bottom_cm"],
        analyte=row["analyte"], qualifier=_LABELS[row["qualifier"]], value=row["value"] if row["value"] is not None else "Unknown / not numeric",
        unit=row["unit"] if row["unit"] is not None else "Unknown", method=row["method"] if row["method"] is not None else "Unknown")
        for row in data["results"]]


def render_soil_summary(section):
    st.subheader("Assigned laboratory soil reports")
    st.caption(section["note"])
    rows = [row for report in section["current"] for row in soil_result_rows(report)]
    if rows:
        st.dataframe(rows)
    else:
        st.info("No current laboratory soil reports assigned to this cycle; soil-test history is unknown.")


def _submit(call, cycle_id, prior, prefix, void=False):
    try:
        reason = st.session_state.get(prefix+"reason") or None
        if void:
            request = dict(report_id=prior["id"], reason=reason)
            operation = "void_soil_test"
        else:
            fields = ("entry", "lab_name", "lab_report_id", "sample_id", "location", "depth_top_cm", "depth_bottom_cm",
                      "subsample_count", "sampling_source", "report_source")
            data = {key: st.session_state.get(prefix+key) for key in fields}
            for key in ("sampled_on", "reported_on"):
                value = st.session_state.get(prefix+key)
                data[key] = value.isoformat() if value else None
            rows = list(csv.reader(StringIO(st.session_state.get(prefix+"results", "").strip()), strict=True))
            if not rows or any(len(row) != 6 for row in rows):
                raise ValueError("Enter six columns for each lab result row; use ? for unknown value, unit or method.")
            data["results"] = []
            for row in rows:
                identifier, analyte, qualifier, value, unit, method = (item.strip() for item in row)
                if qualifier not in _QUALIFIERS:
                    raise ValueError("Use =, <, <=, >, >=, NR (not reported), ND (not detected) or TEXT as the qualifier.")
                data["results"].append(dict(id=identifier, analyte=analyte, qualifier=_QUALIFIERS[qualifier],
                    value=None if value == "?" else value, unit=None if unit == "?" else unit, method=None if method == "?" else method))
            if prior and not prior["voided"] and data == prior["data"] and reason is None:
                st.session_state.field_notice = "No changes to the soil report; no new revision created."
                return
            request = dict(data=data, cycle_id=cycle_id, supersedes_id=prior["id"] if prior else None, reason=reason)
            operation = "record_soil_test"
        request_id = "web-soil-"+hashlib.sha256(json.dumps([operation, request], sort_keys=True, allow_nan=False).encode()).hexdigest()
        saved = call(operation, dict(request, request_id=request_id))
        st.session_state.field_soilreport_choice = saved["id"]
        st.session_state.field_notice = "Soil report voided; history retained." if void else "Soil report saved; no fertilizer targets or applications changed."
    except (ValueError, TypeError, csv.Error) as exc:
        st.session_state.field_error = str(exc)


def render_soil_reports(call, active, farm, cycle_id):
    scope = (active["id"], cycle_id)
    if st.session_state.get("field_soilreport_scope") != scope:
        for key in list(st.session_state):
            if key.startswith("field_soilreport_"):
                del st.session_state[key]
        st.session_state.field_soilreport_scope = scope
    section = call("list_soil_tests", dict(cycle_id=cycle_id))
    with st.expander("Record laboratory soil tests"):
        st.caption(section["note"])
        st.caption("Showing reports assigned to the selected crop cycle." if cycle_id else "Showing unassigned reports for this soil unit.")
        latest = {r["id"]: r for r in section["history"] if not r["superseded"]}
        if st.session_state.get("field_soilreport_choice") not in latest:
            st.session_state.field_soilreport_choice = None
        selected = st.selectbox("Soil report to enter or correct", [None, *latest], key="field_soilreport_choice",
            format_func=lambda key: "New report" if key is None else latest[key]["data"]["entry"]+(" · voided" if latest[key]["voided"] else ""))
        prior = latest.get(selected)
        previous = prior["data"] if prior else {}
        prefix = "field_soilreport_"+(selected or "new")+"_"
        if prior:
            st.caption("A correction retains the entry and cycle assignment. Saving a correction to a voided report restores it.")
        with st.form(prefix+"form"):
            for key, label in (("entry", "Local report entry name"), ("lab_name", "Laboratory name"),
                ("lab_report_id", "Laboratory report identifier"), ("sample_id", "Laboratory sample identifier"), ("location", "Sample location or management zone")):
                st.text_input(label, value=previous.get(key, ""), disabled=key == "entry" and prior is not None, key=prefix+key)
            today = datetime.now(ZoneInfo(farm["timezone"])).date()
            for key, label in (("sampled_on", "Actual sampling date"), ("reported_on", "Laboratory report date")):
                value = date.fromisoformat(previous[key]) if key in previous else None
                st.date_input(label, value=value, min_value=date(1000,1,1), max_value=max(today, value) if value else today, key=prefix+key)
            for key, label in (("depth_top_cm", "Sample depth top (cm); blank if unknown"), ("depth_bottom_cm", "Sample depth bottom (cm); blank if unknown")):
                st.number_input(label, min_value=0.0, max_value=1000.0, value=float(previous[key]) if previous.get(key) is not None else None, key=prefix+key)
            st.number_input("Number of subsamples; blank if unknown", min_value=1, max_value=1000000,
                value=previous.get("subsample_count"), step=1, key=prefix+"subsample_count")
            for key, label in (("sampling_source", "Sampling procedure and source"), ("report_source", "Report source (document, page, or reference)")):
                st.text_input(label, value=previous.get(key, ""), key=prefix+key)
            buffer = StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            for row in previous.get("results", []):
                writer.writerow([row["id"], row["analyte"], _LABELS[row["qualifier"]], *(row[k] if row[k] is not None else "?" for k in ("value", "unit", "method"))])
            st.caption("One comma-separated row per result: row ID, analyte, qualifier, value, unit, method. Use =, <, <=, > or >=; "
                       "NR means not reported and ND means not detected, both with ? for value. TEXT preserves a categorical or descriptive result. Use ? for unknown unit/method. "
                       "Copy numbers and methods as printed; quote text containing commas. No unit conversion or nutrient interpretation is performed.")
            st.text_area("Laboratory result rows", value=buffer.getvalue(), key=prefix+"results")
            st.text_input("Correction or void reason", key=prefix+"reason")
            st.form_submit_button("Save soil report", on_click=_submit, args=(call, cycle_id, prior, prefix))
            if prior and not prior["voided"]:
                st.form_submit_button("Void soil report", on_click=_submit, args=(call, cycle_id, prior, prefix, True))
        if section["history"]:
            history = {r["id"]: r for r in reversed(section["history"])}
            selected_history = st.selectbox("Soil report revision history", list(history), key="field_soilreport_history",
                format_func=lambda key: history[key]["data"]["entry"]+" · "+key[:8]+(" · superseded" if history[key]["superseded"] else " · latest")+(" · voided" if history[key]["voided"] else ""))
            record = history[selected_history]
            st.dataframe(soil_result_rows(record))
            st.write(dict(sampling_source=record["data"]["sampling_source"], report_source=record["data"]["report_source"], reason=record["reason"]))
            st.download_button("Download soil report history", json.dumps(section, indent=2), "soil-reports.json", "application/json")
