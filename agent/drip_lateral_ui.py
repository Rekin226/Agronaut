"""Explicit native input for surveyed lateral scenarios; no operational defaults."""

import csv
from io import StringIO

import streamlit as st

P = "field_lateral_"
COLUMNS = ("identifier", "distance_m", "elevation_m", "upstream_minor_k", "source")
CURVE_COLUMNS = ("flow_lph", "pressure_head_m")
NUMBERS = {
    "pipe": {"internal_diameter_mm": "Internal pipe diameter (mm)", "roughness_mm": "Absolute pipe roughness (mm)",
             "max_head_m": "Pipe pressure rating (m head)"},
    "emitter": {"reference_flow_lph": "Emitter flow at reference pressure (L/h)", "reference_head_m": "Reference pressure head (m)",
                "exponent": "Emitter pressure exponent (0–1)", "min_head_m": "Minimum supported curve head (m)",
                "max_head_m": "Maximum supported curve head (m)", "max_static_head_m": "Emitter static pressure rating (m head)"},
    "inlet": {"running_head_m": "Running pressure at this lateral inlet (m head)",
              "max_static_head_m": "Maximum static pressure at this inlet (m head)", "max_flow_lph": "Available flow to this lateral (L/h)"},
    "limits": {"viscosity_m2_s": "Kinematic viscosity (m²/s)", "max_velocity_m_s": "Permitted pipe velocity (m/s)",
               "max_flow_variation_fraction": "Permitted hydraulic flow variation (fraction of maximum flow)"},
}
CONDITIONS = {
    "closed_tail": "The pipe ends closed immediately after its last emitter",
    "surface_emitters": "Emitters discharge at the surface to atmospheric pressure",
    "steady_inlet_head": "The supplied inlet pressure is maintained at the calculated flow",
    "surveyed_profile": "The surveyed profile is linear between stations, with no omitted crests or sags",
    "identical_emitters": "Every station has one identical emitter described by this nominal curve",
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
        raise ValueError("At least one surveyed emitter station is required")
    return rows


def parse_inlet_curve(value):
    reader = csv.DictReader(StringIO(value))
    if reader.fieldnames != list(CURVE_COLUMNS):
        raise ValueError("Inlet curve CSV header must be: " + ",".join(CURVE_COLUMNS))
    rows = []
    for row in reader:
        if len(rows) >= 100 or set(row) != set(CURVE_COLUMNS) or any(v is None or not v.strip() for v in row.values()):
            raise ValueError("Provide 2–100 complete inlet curve rows, with no missing or extra columns")
        rows.append({key: float(row[key]) for key in CURVE_COLUMNS})
    if len(rows) < 2:
        raise ValueError("At least two supplied inlet pressure–flow points are required")
    return rows


def render_drip_lateral_intake(active, farm, cycle):
    scope = (active["id"], farm["timezone"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get(P+"scope") != scope:
        for key in list(st.session_state):
            if key.startswith(P):
                del st.session_state[key]
        st.session_state[P+"scope"] = scope
    with st.expander("Evaluate a surveyed drip lateral"):
        st.caption("Experimental pressure and flow scenario for one pipe with identical emitters. "
                   "Use pressure measured or justified at its inlet after filters/manifolds. "
                   "The result does not determine irrigation duration or whole-network performance.")
        mode = st.radio("Inlet supply information", ("Fixed pressure", "Pressure–flow curve"), key=P+"mode")
        if st.session_state.get(P+"previous_mode") != mode:
            for key in list(st.session_state):
                if key.startswith(P+"inlet_") or key == P+"conditions_steady_inlet_head":
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
            st.caption("Enter one row for every emitter, ordered by cumulative pipe length. "
                       "Elevations are relative to the inlet (negative is lower); all lengths are metres. "
                       "Fitting K applies to the incoming segment velocity and includes insertion losses. "
                       "Header: "+",".join(COLUMNS))
            station_csv = st.text_area("Surveyed emitter stations (CSV)", key=P+"stations", max_chars=200000)
            conditions = {key: st.checkbox(
                "The supplied inlet curve describes a steady source in the stated configuration" if curved and key == "steady_inlet_head" else label,
                key=P+"conditions_"+key) for key, label in CONDITIONS.items()}
            conditions["source"] = st.text_input("Applicability evidence", key=P+"conditions_source")
            if st.form_submit_button("Calculate and save lateral scenario", key=P+"save"):
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
                return dict(task="drip_lateral", inputs=dict(schema_version=2 if curved else 1,
                    site=dict(name=active["name"], timezone=farm["timezone"]), **records,
                    conditions=conditions, stations=stations))
    return None
