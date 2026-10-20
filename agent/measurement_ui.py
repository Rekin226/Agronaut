"""Supplied timestamped field readings; quality review is a separate action."""

import streamlit as st

from agro_model.farm import OBSERVATION_UNITS


def render_measurement_entry(call, cycles, request_id):
    with st.expander("Record a field measurement"):
        st.caption("Enter the reading's actual timestamp with UTC offset, quantity and source. "
                   "New readings are unverified. A soil point reading does not automatically represent a field average.")
        quantity = st.selectbox("Measured quantity", list(OBSERVATION_UNITS), key="crop_measure_quantity")
        unit = OBSERVATION_UNITS[quantity]
        with st.form("crop_measure_form"):
            cycle_id = st.selectbox("Measurement crop cycle", [None, *cycles], key="crop_measure_cycle",
                format_func=lambda key: "Unassigned to a crop cycle" if key is None else
                    f"{cycles[key]['crop']} · {cycles[key]['planted_on']} · {key[:8]}")
            value = st.number_input(f"Reading ({unit})", value=None, key=f"crop_measure_value_{quantity}")
            stamp = st.text_input("Observed at (ISO timestamp with UTC offset)", key="crop_measure_stamp",
                                 placeholder="YYYY-MM-DDTHH:MM:SS+HH:MM")
            provenance = st.selectbox("Reading provenance", ["measured", "reported", "estimated", "forecast"],
                                      key="crop_measure_provenance")
            source = st.text_input("Measurement source and sampling location", key="crop_measure_source")
            if st.form_submit_button("Save field measurement"):
                data = dict(cycle_id=cycle_id, data=dict(quantity=quantity, value=value, unit=unit,
                            observed_at=stamp, provenance=provenance, source=source))
                try:
                    call("record_measurement", dict(data, request_id=request_id("measurement", data)))
                    st.session_state.crop_notice = "Measurement saved as unverified. Review it or use it in Field records."
                    st.rerun()
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
    render_measurement_import(call)


def render_measurement_import(call):
    from agronaut_agent.measurement_import import COLUMNS, MAX_CSV_BYTES, csv_rows

    with st.expander("Import field measurements from CSV"):
        st.caption("Map your records to this template. Use explicit UTC offsets and canonical units; "
                   "leave cycle_id empty for unassigned readings. Keep request_id stable for retries. "
                   "Include instrument, sampling location and interval in source. Preview before saving.")
        st.download_button("Download measurement CSV header", ",".join(COLUMNS) + "\n",
                           file_name="measurement-template.csv", mime="text/csv")
        upload = st.file_uploader("Measurement CSV (UTF-8, at most 1,000 rows / 2 MB)",
                                  type=["csv"], key="crop_measure_csv")
        if upload is None:
            st.session_state.pop("crop_measure_import_preview", None)
            return
        try:
            if upload.size > MAX_CSV_BYTES:
                raise ValueError("measurement CSV must be at most 2 MB")
            rows = csv_rows(upload.getvalue().decode("utf-8-sig"))
        except (UnicodeError, ValueError) as exc:
            st.error(str(exc))
            st.session_state.pop("crop_measure_import_preview", None)
            return
        if st.button("Preview measurement import"):
            st.session_state.pop("crop_measure_import_preview", None)
            try:
                preview = call("preview_measurements", {"rows": rows})
                st.session_state.crop_measure_import_preview = dict(rows=rows, preview=preview)
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        saved = st.session_state.get("crop_measure_import_preview")
        if saved is None or saved["rows"] != rows:
            return
        preview = saved["preview"]
        st.write(f"{preview['new_count']} new readings; {preview['existing_count']} already recorded.")
        st.json(preview)
        if st.button("Save reviewed measurement import"):
            try:
                result = call("import_measurements", dict(rows=rows, preview_token=preview["preview_token"]))
                st.session_state.pop("crop_measure_import_preview", None)
                st.session_state.crop_notice = f"Imported {result['new_count']} new readings; reused {result['existing_count']} existing readings."
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
