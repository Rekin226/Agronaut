"""Native read-only pairing of drip predictions and saved emitter samples."""

import csv
import json
from io import StringIO

import streamlit as st

from agronaut_agent.drip_diagnostics import render_drip_comparison

P = "field_drip_compare_"
COLUMNS = ("sample_id", "station_id", "mapping_note")


def parse_mappings(value, network=False):
    columns = ("sample_id", "lateral_id", "station_id", "mapping_note") if network else COLUMNS
    reader = csv.DictReader(StringIO(value))
    if reader.fieldnames != list(columns):
        raise ValueError("Mapping CSV header must be: " + ",".join(columns))
    rows = []
    for row in reader:
        if len(rows) >= 2500 or set(row) != set(columns) or any(v is None for v in row.values()):
            raise ValueError("Provide complete mapping rows with no extra columns")
        rows.append({key: row[key] if key not in ("station_id", "lateral_id") else row[key] or None for key in columns})
    if not rows:
        raise ValueError("Map every sample; leave both lateral_id and station_id blank only for explicitly unmatched samples" if network else
                         "Map every selected-lateral sample; leave station_id blank only for explicitly unmatched samples")
    return rows


def _draft(saved_id, selected, network=False):
    return dict(calculation_id=saved_id, dataset_kind=st.session_state.get(P+"kind"),
        selection_note=st.session_state.get(P+"note"), comparisons=[dict(test_calculation_id=identifier,
        **{key: st.session_state.get(P+identifier+"_"+key) for key in (
            "lateral", "mappings", "independence", "representation_note", "conditions_note", "water_density_kg_m3", "density_source", "pressure_reference") if not network or key != "lateral"})
        for identifier in selected])


def _compare(call, saved_id, cycle_id, selected, network=False):
    st.session_state.pop(P+"report", None)
    try:
        if st.session_state.get("field_plan") != saved_id or st.session_state.get("field_cycle") != cycle_id:
            raise ValueError("The calculation or crop cycle changed; review the comparison draft")
        draft = _draft(saved_id, selected, network)
        request = dict(draft, comparisons=[dict(row, mappings=parse_mappings(row["mappings"], network)) for row in draft["comparisons"]])
        st.session_state[P+"report"] = dict(draft=draft, result=call("compare_drip_network" if network else "compare_drip_lateral", request))
    except (ValueError, TypeError, csv.Error) as exc:
        st.session_state[P+"error"] = str(exc)


def render_drip_diagnostics(call, saved, records, farm, cycle):
    scope = (saved["id"], saved["cycle_id"], farm["timezone"], json.dumps(cycle, sort_keys=True))
    if st.session_state.get(P+"scope") != scope:
        for key in list(st.session_state):
            if key.startswith(P):
                del st.session_state[key]
        st.session_state[P+"scope"] = scope
    if saved["task"] not in ("drip_lateral", "drip_zone", "drip_manifold"):
        return
    network = saved["task"] != "drip_lateral"
    with st.expander("Compare drip predictions with emitter measurements"):
        if error := st.session_state.pop(P+"error", None):
            st.error(error)
        if saved["result"]["result"]["prediction"] is None:
            st.info("Save a solved drip scenario before comparing emitter measurements")
            return
        if saved["inputs"]["site"]["timezone"] != farm["timezone"]:
            st.info("The farm timezone changed; review the saved scenario before comparing measurements")
            return
        st.caption("Map recorded samples to their actual surveyed stations; unmatched samples stay visible. "
                   "Pressure comparison requires supplied water density. Only tests declared held out enter aggregate metrics. "
                   "No independent field validation or operating approval is implied.")
        if network:
            branches = (saved["inputs"]["laterals"] if saved["task"] == "drip_zone"
                        else [t["lateral"] for t in saved["inputs"]["takeoffs"]])
            st.caption("Map every sample using its modeled lateral_id and station_id. Review the complete active branch set and shared source configuration; this report does not validate total source flow or takeoff pressures.")
            stations = [dict(lateral_id=b["identifier"], station_id=s["identifier"], distance_m=s["distance_m"], elevation_m=s["elevation_m"])
                        for b in branches for s in b["stations"]]
        else:
            stations = [dict(station_id=s["identifier"], distance_m=s["distance_m"], elevation_m=s["elevation_m"])
                        for s in saved["inputs"]["stations"]]
        st.dataframe(stations, hide_index=True)
        tests = {item["id"]: item for item in records["calculations"]
                 if item["task"] == "drip_emitter_test" and item["cycle_id"] == saved["cycle_id"]}
        if not tests:
            st.info("Save an emitter-test evaluation for this unit and crop cycle first")
            return
        if P+"tests" in st.session_state:
            st.session_state[P+"tests"] = [i for i in st.session_state[P+"tests"] if i in tests]
        selected = st.multiselect("Emitter tests to compare", list(tests), key=P+"tests",
                                  format_func=lambda i: f"Saved {tests[i]['created_at']} · {i}", max_selections=100)
        for identifier in selected:
            reference = call("get", dict(calculation_id=identifier))
            st.caption(f"Recorded samples in {identifier}")
            st.dataframe(reference["result"]["result"]["samples"], hide_index=True)
        with st.form(P+"form"):
            st.selectbox("Drip comparison dataset kind", ["synthetic", "field"], index=None, key=P+"kind")
            st.text_area("Test selection rationale", key=P+"note")
            for identifier in selected:
                prefix = P+identifier+"_"
                st.caption(f"Test {identifier}")
                if not network:
                    st.text_input("Recorded lateral name", key=prefix+"lateral")
                header = "sample_id,lateral_id,station_id,mapping_note" if network else "sample_id,station_id,mapping_note"
                st.text_area("Sample mapping CSV: "+header, key=prefix+"mappings", max_chars=200000)
                st.caption("Include every sample in the test; leave both lateral_id and station_id blank for unmatched samples, with a note. IDs match exactly; recorded and modeled lateral identities must map one-to-one." if network else
                           "Include every sample in this lateral; blank station_id means unmatched and requires a note. IDs must match exactly.")
                st.text_input("Evidence for matching geometry and equipment", key=prefix+"representation_note")
                st.text_input("Evidence for matching inlet/source settings and steady conditions", key=prefix+"conditions_note")
                st.selectbox("Recorded pressure reference", ["gauge_at_emitter", "unknown"], index=None,
                             key=prefix+"pressure_reference")
                st.caption("Confirm gauge pressure at the emitter relative to atmosphere; otherwise choose unknown. Document the instrument/reference evidence in the conditions note.")
                st.selectbox("Use of these measurements in model development", ["held_out", "used_for_calibration", "unknown"],
                             index=None, key=prefix+"independence")
                st.number_input("Water density for pressure conversion (kg/m³; leave unknown blank)", min_value=500.0,
                                max_value=2000.0, value=None, key=prefix+"water_density_kg_m3")
                st.text_input("Density source or reason it is unknown", key=prefix+"density_source")
            st.form_submit_button("Compare drip measurements without changing records", on_click=_compare,
                                  args=(call, saved["id"], saved["cycle_id"], selected, network), key=P+"run")
        cached = st.session_state.get(P+"report")
        if cached and cached["draft"] == _draft(saved["id"], selected, network):
            report = cached["result"]
            st.text(render_drip_comparison(report))
            st.dataframe([dict(test=row["test_calculation"]["id"], sample=pair["sample"]["id"],
                station=pair["mapping"]["station_id"], status=pair["status"],
                **(dict(lateral=pair["mapping"]["lateral_id"], recorded_lateral=pair["sample"]["lateral"]) if network else {}),
                recorded_flow_lph=pair["sample"]["flow_l_h"], recorded_pressure_kpa=pair["sample"]["pressure_kpa"],
                predicted_flow_lph=pair["errors"]["flow_lph"]["predicted"] if pair["errors"] else None,
                predicted_pressure_kpa=pair["errors"]["pressure_kpa"]["predicted"] if pair["errors"] else None,
                flow_error_lph=pair["errors"]["flow_lph"]["error"] if pair["errors"] else None,
                pressure_error_kpa=pair["errors"]["pressure_kpa"]["error"] if pair["errors"] else None,
                flow_status=pair["errors"]["flow_lph"]["status"] if pair["errors"] else "unmatched_station",
                pressure_status=pair["errors"]["pressure_kpa"]["status"] if pair["errors"] else "unmatched_station",
                included_test=row["included_in_metrics"], exclusion=row["exclusion_reason"], mapping_note=pair["mapping"]["mapping_note"])
                for row in report["comparisons"] for pair in row["pairs"]], hide_index=True)
            if network:
                st.caption("Modeled stations without mapped samples in each test")
                st.dataframe([dict(test=row["test_calculation"]["id"], **station)
                              for row in report["comparisons"] for station in row["unmeasured_stations"]], hide_index=True)
            st.download_button("Download drip comparison and evidence", json.dumps(report, indent=2),
                file_name=f"drip-comparison-{saved['id']}.json", mime="application/json", key=P+"download")
        elif cached:
            st.caption("Selection or declarations changed. Run the comparison again to refresh the report.")
