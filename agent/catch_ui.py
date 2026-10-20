"""Native entry of a complete sprinkler catch grid, without guessed readings."""

import streamlit as st


def render_catch_intake(active, farm, cycle):
    scope = (active["id"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get("field_catch_scope") != scope:
        for key in list(st.session_state):
            if key.startswith("field_catch_"):
                del st.session_state[key]
        st.session_state.field_catch_scope = scope
    prefix = "field_catch_"
    with st.expander("Evaluate sprinkler catch cans"):
        st.caption("Enter calibrated depths in mm from one sprinkler zone and the same full rain-free exposure. "
                   "Each position must represent equal area. This evaluates the test; it does not prescribe runtime or establish irrigation efficiency.")
        with st.form(prefix+"form"):
            zone = st.text_input("Tested sprinkler zone", key=prefix+"zone")
            kind = st.selectbox("Catch-test evidence", ["observed", "scenario"], index=None, key=prefix+"kind")
            started = st.text_input("Test start with timezone offset (YYYY-MM-DDTHH:MM:SS+HH:MM)", key=prefix+"start")
            duration = st.number_input("Full exposure duration (seconds)", min_value=1.0, max_value=86400.0, value=None, key=prefix+"duration")
            rain_free = st.checkbox("The full exposure was rain-free", key=prefix+"rain_free")
            exposure = st.checkbox("Every collector had the same full exposure in this zone", key=prefix+"exposure")
            wind = st.number_input("Wind speed during test (m/s); blank if unknown", min_value=0.0, max_value=100.0, value=None, key=prefix+"wind")
            conditions = st.text_input("Conditions source and notes (wind, pressure, obstructions, calibration)", key=prefix+"conditions")
            rows = st.number_input("Catch-grid rows", min_value=2, max_value=100, value=None, step=1, key=prefix+"rows")
            columns = st.number_input("Catch-grid columns", min_value=2, max_value=100, value=None, step=1, key=prefix+"columns")
            dx = st.number_input("Spacing between columns (m)", min_value=0.0, max_value=1000.0, value=None, key=prefix+"dx")
            dy = st.number_input("Spacing between rows (m)", min_value=0.0, max_value=1000.0, value=None, key=prefix+"dy")
            equal = st.checkbox("All grid positions represent equal areas", key=prefix+"equal")
            layout_source = st.text_input("Layout source and coverage notes", key=prefix+"layout_source")
            st.caption("Use one line per grid row and comma-separated depths for its columns. Use ? for unreadable/unknown, "
                       "and 0 only for an observed zero. Include all positions. The count must be divisible by four, at most 2500; do not discard readings to fit.")
            readings = st.text_area("Catch depths (mm)", key=prefix+"readings")
            source = st.text_input("Reading source (applies to every entered position)", key=prefix+"source")
            submitted = st.form_submit_button("Evaluate and save catch test")
        if submitted:
            try:
                lines = readings.strip().splitlines()
                if rows is None or columns is None or len(lines) != rows or any(len(line.split(",")) != columns for line in lines):
                    raise ValueError("Enter every grid row and column; use ? for unknown depths.")
                catches = [dict(row=r+1, column=c+1, depth_mm=None if value.strip() == "?" else float(value.strip()), source=source)
                           for r, line in enumerate(lines) for c, value in enumerate(line.split(","))]
                return dict(task="sprinkler_catch_test", inputs=dict(schema_version=1,
                    site=dict(name=farm["name"], timezone=farm["timezone"]),
                    event=dict(zone_name=zone, started_at=started, duration_seconds=duration, kind=kind,
                        rain_free=rain_free, same_exposure=exposure, wind_m_s=wind, conditions_source=conditions),
                    grid=dict(rows=rows, columns=columns, spacing_x_m=dx, spacing_y_m=dy, equal_area=equal, source=layout_source), catches=catches))
            except ValueError as exc:
                st.error(str(exc))
    return None
