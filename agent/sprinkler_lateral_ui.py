"""Explicit native input for surveyed lateral scenarios; no operational defaults."""

import csv
from io import StringIO

import streamlit as st

from .drip_lateral_ui import CURVE_COLUMNS, parse_inlet_curve

P = "field_sprinkler_lateral_"
COLUMNS = ("identifier", "distance_m", "elevation_m", "upstream_minor_k", "riser_height_m", "riser_length_m", "riser_minor_k", "source")
NUMBERS = {
    "pipe": {"internal_diameter_mm": "Internal pipe diameter (mm)", "roughness_mm": "Absolute pipe roughness (mm)",
             "max_head_m": "Pipe pressure rating (m head)"},
    "nozzle": {"reference_flow_lph": "Nozzle flow at reference pressure (L/h)", "reference_head_m": "Reference pressure head (m)",
                "min_head_m": "Minimum supported curve head (m)",
                "max_head_m": "Maximum supported curve head (m)", "max_static_head_m": "Nozzle static pressure rating (m head)"},
    "riser": {"internal_diameter_mm": "Internal riser diameter (mm)", "roughness_mm": "Absolute riser roughness (mm)",
              "max_head_m": "Riser pressure rating (m head)"},
    "inlet": {"running_head_m": "Running pressure at this lateral inlet (m head)",
              "max_static_head_m": "Maximum static pressure at this inlet (m head)", "max_flow_lph": "Available flow to this lateral (L/h)"},
    "limits": {"viscosity_m2_s": "Kinematic viscosity (m²/s)", "max_velocity_m_s": "Permitted pipe velocity (m/s)",
               "max_flow_variation_fraction": "Permitted hydraulic flow variation (fraction of maximum flow)"},
}
CONDITIONS = {
    "closed_tail": "The lateral ends closed immediately after its last sprinkler",
    "steady_full_pipes": "The lateral and risers stay full under steady conditions at the supplied inlet pressure or curve",
    "surveyed_profile": "The lateral profile is linear between stations with no omitted crests or sags; risers climb monotonically",
    "identical_nozzles": "Every station has one identical nozzle described by the square-root pressure–flow relation",
    "unregulated_atmospheric_outlets": "Nozzles discharge to atmosphere without pressure regulators",
}


def parse_stations(value):
    reader = csv.DictReader(StringIO(value))
    if reader.fieldnames != list(COLUMNS):
        raise ValueError("Station CSV header must be: " + ",".join(COLUMNS))
    rows = []
    for row in reader:
        if len(rows) >= 2000 or set(row) != set(COLUMNS) or any(v is None or not v.strip() for v in row.values()):
            raise ValueError("Provide 1–2000 complete station rows, with no missing or extra columns")
        rows.append({key: row[key].strip() if key in ("identifier", "source") else float(row[key]) for key in COLUMNS})
    if not rows:
        raise ValueError("At least one surveyed nozzle station is required")
    return rows


def render_sprinkler_lateral_intake(active, farm, cycle):
    scope = (active["id"], farm["timezone"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get(P+"scope") != scope:
        for key in list(st.session_state):
            if key.startswith(P):
                del st.session_state[key]
        st.session_state[P+"scope"] = scope
    with st.expander("Evaluate a surveyed sprinkler lateral"):
        st.caption("Experimental pressure and flow scenario for one pipe with identical nozzles. "
                   "Use pressure measured or justified at its inlet after filters/manifolds. "
                   "Supply every riser height, path length and fitting loss. Nominal flows do not establish catch-can uniformity or irrigation duration.")
        mode = st.radio("Inlet supply information", ("Fixed pressure", "Pressure–flow curve"), key=P+"mode")
        if st.session_state.get(P+"previous_mode") != mode:
            for key in list(st.session_state):
                if key.startswith(P+"inlet_") or key == P+"conditions_steady_full_pipes":
                    del st.session_state[key]
            st.session_state[P+"previous_mode"] = mode
        curved = mode == "Pressure–flow curve"
        with st.form(P+"form"):
            records = {}
            for record, fields in NUMBERS.items():
                records[record] = {key: st.number_input(label, min_value=0.0, value=None, format="%.8g", key=P+record+"_"+key)
                                   for key, label in fields.items() if not (curved and key == "running_head_m")}
                records[record]["source"] = st.text_input(f"Evidence for {record}", key=P+record+"_source")
            if curved:
                st.caption("Supply increasing flow points with nonincreasing pressure, for the same source settings, "
                           "water level, upstream equipment and other loads. No extrapolation is allowed. Header: "
                           + ",".join(CURVE_COLUMNS))
                curve_csv = st.text_area("Inlet pressure–flow points (CSV)", key=P+"inlet_curve", max_chars=20000)
                configuration = st.text_input("Source configuration and concurrent loads", key=P+"inlet_configuration")
                reference_confirmed = st.checkbox("These are pressure heads at the lateral inlet after upstream equipment, not raw pump head gain",
                                                  key=P+"inlet_reference")
            st.caption("Enter one row for every nozzle, ordered by cumulative pipe length. "
                       "Elevations are relative to the inlet (negative is lower); all lengths are metres. "
                       "Through-fitting K is at the downstream station and uses incoming lateral velocity; riser K uses branch velocity. "
                       "Riser height is relative to its station base; path length must be at least that height. "
                       "Header: "+",".join(COLUMNS))
            station_csv = st.text_area("Surveyed nozzle stations (CSV)", key=P+"stations", max_chars=200000)
            conditions = {key: st.checkbox(
                label,
                key=P+"conditions_"+key) for key, label in CONDITIONS.items()}
            conditions["source"] = st.text_input("Applicability evidence", key=P+"conditions_source")
            if st.form_submit_button("Calculate and save sprinkler lateral", key=P+"save"):
                try:
                    stations = parse_stations(station_csv)
                    if curved:
                        if not reference_confirmed:
                            raise ValueError("Confirm that the curve describes pressure at this lateral inlet")
                        records["inlet"].update(curve=parse_inlet_curve(curve_csv), configuration=configuration,
                                                head_reference="lateral_inlet_pressure")
                except (ValueError, csv.Error) as exc:
                    st.error(str(exc))
                    return None
                return dict(task="sprinkler_lateral", inputs=dict(schema_version=2 if curved else 1,
                    site=dict(name=active["name"], timezone=farm["timezone"]), **records,
                    conditions=conditions, stations=stations))
    return None
