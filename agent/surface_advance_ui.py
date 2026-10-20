"""Native, explicit-input surface advance scenarios; no equipment operation."""

import streamlit as st

_P = "field_advance_"
_CONDITIONS = {
    "uniform_sloping_field": "The represented strip has a uniform positive slope and uniform soil conditions",
    "normal_inlet_flow": "The inlet supports the assumed normal flow, including wide flow for a border",
    "initially_unponded": "The strip starts without ponded water",
    "no_rain_or_runon": "There is no rain or external runon during advance",
    "constant_inflow": "Inflow stays constant until water reaches the end",
    "no_downstream_backwater": "There is no downstream backwater affecting advance",
}


def _numbers(prefix, fields):
    return {key: st.number_input(label, min_value=0.0, value=None, key=_P+prefix+key)
            for key, label in fields.items()}


def render_surface_advance_intake(active, farm, cycle):
    scope = (active["id"], farm["timezone"],
             (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get(_P+"scope") != scope:
        for key in list(st.session_state):
            if key.startswith(_P):
                del st.session_state[key]
        st.session_state[_P+"scope"] = scope
    with st.expander("Predict surface irrigation advance"):
        st.caption("Experimental scenario for one sloping furrow or wide rectangular border. "
                   "Predicts arrival at half/full length, not when to stop irrigation. "
                   "Supply site-specific hydraulic and intake evidence; no crop or soil defaults are selected.")
        method = st.selectbox("Surface advance method", ["furrow", "border"], index=None, key=_P+"method")
        with st.form(_P+"form"):
            geometry = _numbers("geometry_", {
                "length_m": "Length of the represented strip (m)",
                "width_m": "Furrow spacing or whole border width (m)",
                "slope_m_m": "Uniform longitudinal slope (m/m)",
            })
            geometry.update(method=method, source=st.text_input("Geometry evidence", key=_P+"geometry_source"))
            hyd = _numbers("hydraulics_", {
                "inflow_lps": "Inflow to this one furrow or whole border (L/s)",
                "manning_n": "Manning roughness coefficient n",
                "surface_shape_factor": "Surface-storage shape factor (0–1)",
            })
            if method == "border":
                hyd.update(section_p1=1.0, section_p2=5/3)
                st.caption("The border calculation assumes wide rectangular flow: section p1 = 1, p2 = 5/3.")
            else:
                hyd.update(_numbers("hydraulics_", {"section_p1": "Hydraulic section coefficient p1 (SI fit)",
                                                   "section_p2": "Hydraulic section exponent p2"}))
            hyd["source"] = st.text_input("Hydraulic geometry, roughness and shape-factor evidence", key=_P+"hydraulics_source")
            basis = st.selectbox("Intake coefficient basis", ["depth_mm", "volume_per_length_m2"], index=None,
                                 format_func=lambda key: "Depth (mm)" if key == "depth_mm" else "Volume per length (m³/m)",
                                 key=_P+"basis")
            st.caption("Use coefficients for this strip and its wetted geometry. Depth coefficients cover its represented width; "
                       "volume-per-length coefficients already include that width. Opportunity time must be supported from zero.")
            intake = _numbers("infiltration_", {
                "k": "Intake k (mm/min^a or m²/min^a, matching the selected basis)",
                "exponent": "Intake exponent a (greater than 0, at most 1)",
                "steady_rate": "Steady intake (mm/min or m²/min, matching the selected basis)",
                "max_opportunity_min": "Maximum supported intake opportunity time (min)",
            })
            intake.update(basis=basis, min_opportunity_min=0,
                          source=st.text_input("Intake calibration and time-envelope evidence", key=_P+"infiltration_source"))
            limits = _numbers("limits_", {
                "max_advance_min": "Maximum acceptable advance time (min, at most 1440)",
                "max_inlet_velocity_m_s": "Site-specific inlet velocity limit (m/s)",
                "max_inlet_flow_area_m2": "Maximum supported inlet flow area (m², whole cross-section)",
            })
            limits["source"] = st.text_input("Time, velocity and capacity limit evidence", key=_P+"limits_source")
            if method == "border":
                st.caption("The supported inlet-area limit must also keep water depth small relative to border width.")
            conditions = {key: st.checkbox(label, key=_P+"conditions_"+key) for key, label in _CONDITIONS.items()}
            conditions["source"] = st.text_input("Applicability review and assumptions", key=_P+"conditions_source")
            if st.form_submit_button("Calculate and save advance scenario", key=_P+"save"):
                return dict(task="surface_advance", inputs=dict(schema_version=1,
                    site=dict(name=active["name"], timezone=farm["timezone"]), geometry=geometry,
                    hydraulics=hyd, infiltration=intake, limits=limits, conditions=conditions))
    return None
