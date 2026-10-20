"""Native timed-volume intake for a scoped drip-emitter test."""

import csv
from io import StringIO

import streamlit as st


def render_emitter_intake(active, farm, cycle):
    scope = (active["id"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get("field_emitter_scope") != scope:
        for key in list(st.session_state):
            if key.startswith("field_emitter_"):
                del st.session_state[key]
        st.session_state.field_emitter_scope = scope
    p = "field_emitter_"
    with st.expander("Evaluate drip emitter flows"):
        st.caption("Record a complete selection of distinct surface-drip emitters in one zone. Keep lateral/position labels and operating pressure. "
                   "Each collected volume is divided by its own collection time. Results describe this sample and do not prescribe runtime or diagnose clogging.")
        with st.form(p+"form"):
            zone = st.text_input("Sampled drip zone", key=p+"zone")
            kind = st.selectbox("Emitter-test evidence", ["observed", "scenario"], index=None, key=p+"kind")
            started = st.text_input("Test window starts (ISO timestamp with offset)", key=p+"start")
            ended = st.text_input("Test window ends (ISO timestamp with offset)", key=p+"end")
            stable = st.checkbox("Zone operation was stable throughout sampling", key=p+"stable")
            conditions = st.text_input("Operating conditions source and notes", key=p+"conditions")
            count = st.number_input("Expected distinct emitters", min_value=4, max_value=2500, step=4, value=None, key=p+"count")
            same = st.checkbox("Sampled emitters share one specification", key=p+"same")
            equal = st.checkbox("Each sampled emitter has equal representation weight", key=p+"equal")
            sampling = st.text_input("Sampling plan and coverage source (include head/tail and elevation coverage)", key=p+"sampling")
            st.caption("One comma-separated row per emitter, without a header: ID, lateral, position, volume (mL), collection time (seconds), pressure (kPa). "
                       "Use ? for unknown volume or pressure, and 0 only for explicit zero. Quote labels containing commas. Include every expected emitter; do not drop missing readings.")
            raw = st.text_area("Emitter sample rows", key=p+"samples")
            source = st.text_input("Measurement source for these sample rows", key=p+"source")
            submit = st.form_submit_button("Evaluate and save emitter test")
        if submit:
            try:
                rows = list(csv.reader(StringIO(raw.strip()), strict=True))
                if count is None or len(rows) != count or any(len(row) != 6 for row in rows):
                    raise ValueError("Supply six columns for every expected emitter; use ? for unknown volume or pressure.")
                samples = []
                for row in rows:
                    identifier, lateral, position, volume, duration, pressure = (v.strip() for v in row)
                    samples.append(dict(id=identifier, lateral=lateral, position=position,
                        volume_ml=None if volume == "?" else float(volume), duration_seconds=float(duration),
                        pressure_kpa=None if pressure == "?" else float(pressure), source=source))
                return dict(task="drip_emitter_test", inputs=dict(schema_version=1,
                    site=dict(name=farm["name"], timezone=farm["timezone"]),
                    event=dict(zone_name=zone, started_at=started, ended_at=ended, kind=kind, stable_operation=stable, conditions_source=conditions),
                    sampling=dict(expected_count=count, same_emitter_type=same, equal_representation=equal, source=sampling), samples=samples))
            except (ValueError, csv.Error) as exc:
                st.error(str(exc))
    return None
