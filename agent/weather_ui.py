"""Explicit provider preview; fetching never writes a calculation or an actual."""

import json

import streamlit as st


def render_weather_fetch(call, farm, start, end):
    st.caption("Fetch model estimates from Open-Meteo. Clicking Fetch sends these coordinates and dates "
               "to the provider. Review the weather before saving a calculation. "
               "Provider access is subject to its usage terms; a server OPEN_METEO_API_KEY supports customer access.")
    latitude = st.number_input("Latitude (WGS84)", min_value=-90.0, max_value=90.0, value=None,
                               format="%.6f", key="field_provider_latitude")
    longitude = st.number_input("Longitude (WGS84)", min_value=-180.0, max_value=180.0, value=None,
                                format="%.6f", key="field_provider_longitude")
    submitted = st.button("Fetch weather preview", key="field_provider_fetch")
    signature = (latitude, longitude, start, end, farm["timezone"])
    if st.session_state.get("field_provider_signature") != signature:
        st.session_state.pop("field_provider_bundle", None)
        st.session_state.field_provider_signature = signature
    if submitted:
        st.session_state.pop("field_provider_bundle", None)
        try:
            if start is None or end is None:
                raise ValueError("Choose the first weather day before fetching.")
            bundle = call("fetch_weather", dict(latitude=latitude, longitude=longitude,
                                                start=start.isoformat(), end=end.isoformat()))
            st.session_state.field_provider_bundle = bundle
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    bundle = st.session_state.get("field_provider_bundle")
    if bundle:
        st.caption(f"{bundle['provider']} · retrieved {bundle['weather_sources'][0]['retrieved_at']}")
        st.write("Provider grid location and elevation", bundle["grid"])
        st.dataframe([{key: day[key] for key in ("day", "kind", "period_hours", "et0_mm_day", "rainfall_mm")}
                      for day in bundle["weather"]], hide_index=True)
        for assumption in bundle["assumptions"]:
            st.caption(assumption)
        st.download_button("Download weather snapshot", json.dumps(bundle, indent=2),
                            file_name="weather_snapshot.json", mime="application/json", key="field_provider_download")
    return bundle
