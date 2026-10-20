"""Scoped soil-profile intake with explicit layer properties and reading quality."""

import csv
from io import StringIO

import streamlit as st


def render_soil_profile_intake(active, farm, cycle):
    scope = (active["id"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get("field_profile_scope") != scope:
        for key in list(st.session_state):
            if key.startswith("field_profile_"):
                del st.session_state[key]
        st.session_state.field_profile_scope = scope
    p = "field_profile_"
    with st.expander("Assess soil layers and water storage"):
        st.caption("Supply contiguous layers from the soil surface through the root depth. Values must represent the same footprint and time. "
                   "This assesses storage by depth; it does not simulate water movement or prescribe irrigation.")
        with st.form(p+"form"):
            name = st.text_input("Profile name and sampling location", key=p+"name")
            kind = st.selectbox("Profile evidence", ["observed", "scenario"], index=None, key=p+"kind")
            observed = st.text_input("Represented time (timestamp with seconds and farm UTC offset)", key=p+"observed")
            root = st.text_input("Effective root depth (m)", key=p+"root")
            representation = st.text_area("Sampling coverage, calibration and common-time rationale", key=p+"representation")
            source = st.text_input("Profile source", key=p+"source")
            st.caption("One CSV row per layer without a header: ID, top (m), bottom (m), field capacity, wilting point, water content, "
                       "soil-properties source, water-reading source, provenance, quality. Volumetric fractions use m3/m3 of the same bulk soil volume. "
                       "Use ? for unknown water content; zero means explicitly dry. Provenance: measured, reported or estimated. "
                       "Quality: unverified, checked or rejected. Quote sources containing commas. No texture-based values are supplied automatically.")
            raw = st.text_area("Soil layer rows", key=p+"layers")
            submit = st.form_submit_button("Assess and save soil profile")
        if submit:
            try:
                rows = list(csv.reader(StringIO(raw.strip()), strict=True))
                if not 1 <= len(rows) <= 100 or any(len(row) != 10 for row in rows):
                    raise ValueError("Supply 1–100 rows with all ten columns; use ? for unknown water content.")
                keys = ("id", "top_m", "bottom_m", "field_capacity", "wilting_point", "water_content", "soil_source", "water_source", "provenance", "quality")
                layers = [dict(zip(keys, (value.strip() for value in row))) for row in rows]
                for row in layers:
                    if row["water_content"] == "?":
                        row["water_content"] = None
                return dict(task="soil_profile", inputs=dict(schema_version=1, site=dict(name=farm["name"], timezone=farm["timezone"]),
                    profile=dict(name=name, observed_at=observed, kind=kind, root_depth_m=root, representation_note=representation, source=source), layers=layers))
            except (ValueError, csv.Error) as exc:
                st.error(str(exc))
    return None
