"""Manual field-water intake. All scientific validation stays in the shared calculator."""

from datetime import timedelta

import streamlit as st

from agent.crop_coefficient_ui import coefficient_fields, coefficient_selector
from agent.root_growth_ui import root_zone_fields
from agent.weather_ui import render_weather_fetch

_PREFIX = "field_intake_"
_TASKS = {"crop_water": "Crop-water demand", "root_zone_budget": "Soil-water budget"}
_KINDS = {"observed": "Observed measurements", "forecast": "Forecast",
          "historical": "Historical reference", "scenario": "Hypothetical scenario"}


def _number(label, key, *, low=0.0, high=None, help=None):
    return st.number_input(label, min_value=low, max_value=high, value=None,
                           key=_PREFIX + key, help=help)


def _text(label, key, **kwargs):
    return st.text_input(label, key=_PREFIX + key, **kwargs)


def _fraction(value):
    return None if value is None else value / 100


def render_field_intake(unit, farm, cycle, call=None):
    """Return a submitted calculation request, or None. Never save observations here."""
    scope = (unit["id"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get(_PREFIX + "scope") != scope:
        for key in list(st.session_state):
            if key.startswith(_PREFIX):
                del st.session_state[key]
        st.session_state[_PREFIX + "scope"] = scope
    with st.expander("Build a field-water calculation"):
        st.caption("Enter supplied measurements or explicit scenario assumptions. Numeric fields start blank. "
                   "Crop demand estimates water use; a soil-water budget also needs soil and application inputs. "
                   "If soil properties are unknown, choose crop-water demand. "
                   "Saving a calculation does not record irrigation as completed.")
        task = st.selectbox("What do you want to calculate?", list(_TASKS), format_func=_TASKS.get,
                             key=_PREFIX + "task")
        start = st.date_input("First weather day", value=None, key=_PREFIX + "start",
                              help=f"Calendar dates use the farm timezone: {farm['timezone']}.")
        count = st.number_input("Number of consecutive days", min_value=1, max_value=31, value=1,
                                 key=_PREFIX + "count", help="For longer windows, use the JSON import below.")
        use_provider = False
        bundle = None
        if call is not None:
            use_provider = st.selectbox("Weather input", ["Enter meteorology", "Fetch Open-Meteo"],
                                         key=_PREFIX + "weather_input") == "Fetch Open-Meteo"
        if use_provider:
            bundle = render_weather_fetch(call, farm, start, start + timedelta(days=count - 1) if start else None)
        solar_mode = False
        if not use_provider:
            solar_mode = st.selectbox("Radiation input", ["Net radiation", "Solar radiation — estimate net radiation"],
                key=_PREFIX + "radiation_input") != "Net radiation"
            if solar_mode:
                st.caption("Estimate daily net radiation using FAO-56 and grass-reference albedo 0.23. "
                           "Supply daily temperature/humidity extremes and daily incoming solar energy. "
                           "Interval averages do not establish daily extremes. Results retain this estimation basis.")
        window = (start, count, use_provider, solar_mode)
        old_window = st.session_state.get(_PREFIX + "window")
        if old_window != window:
            # Changing a date must not relabel previously entered meteorology or
            # an initial soil measurement. Non-date-specific site/crop inputs stay.
            for key in list(st.session_state):
                if (key.startswith(_PREFIX + "weather_") and key != _PREFIX + "weather_input") or key.startswith((_PREFIX + "kc_period_", _PREFIX + "root_day_")) or key in (
                        _PREFIX + "depletion", _PREFIX + "initial_source"):
                    del st.session_state[key]
            st.session_state[_PREFIX + "window"] = window
        scheduled, period_count = coefficient_selector(_PREFIX)
        daily_roots = st.checkbox("Use daily root depths", value=False, key=_PREFIX + "daily_roots") if task == "root_zone_budget" else False
        st.caption("Supply coefficients, root depths and dates applicable to this crop. "
                   "This is an experimental water budget, "
                   "not a pump runtime or a field-validated irrigation schedule.")
        with st.form(_PREFIX + "form"):
            tabs = st.tabs(["Field and crop", "Daily weather and water"] +
                           (["Soil and application"] if task == "root_zone_budget" else []))
            with tabs[0]:
                area = _number("Cultivated area", "area")
                area_unit = st.selectbox("Area unit", ["m²", "hectares"], key=_PREFIX + "area_unit")
                crop = _text("Crop", "crop", value=cycle["crop"] if cycle else "", disabled=cycle is not None)
                coefficients = coefficient_fields(_PREFIX, scheduled, period_count)
                if not use_provider:
                    elevation = _number("Site elevation above sea level (m)", "elevation", low=-500.0, high=9000.0)
                    wind_height = _number("Wind measurement height (m)", "wind_height", low=0.5, high=100.0)
                    if solar_mode:
                        latitude = _number("Latitude (decimal degrees; south negative)", "latitude", low=-90.0, high=90.0)
            weather, events = [], []
            with tabs[1]:
                for index in range(count):
                    day = start + timedelta(days=index) if start is not None else None
                    prefix = f"weather_{index}_"
                    st.markdown(f"**{day.isoformat() if day else f'Day {index + 1}'}**")
                    if use_provider:
                        row = bundle["weather"][index] if bundle else dict(day=day.isoformat() if day else None)
                        if bundle:
                            st.caption(f"{row['kind']} · reference ET {row['et0_mm_day']:.3f} mm · rainfall {row['rainfall_mm']:.3f} mm")
                        weather.append(row)
                        if task == "root_zone_budget":
                            events.append(dict(day=row["day"],
                                net_irrigation_mm=_number("Assumed net irrigation (mm)", prefix + "irrigation"),
                                runoff_mm=_number("Assumed rainfall runoff (mm)", prefix + "runoff"),
                                source=_text("Source of irrigation and runoff assumptions", prefix + "event_source")))
                        continue
                    kind = st.selectbox("Weather type", list(_KINDS), index=None, format_func=_KINDS.get,
                                         key=_PREFIX + prefix + "kind", placeholder="Choose the source type")
                    issued_at = _text("Forecast issued at (with timezone)", prefix + "issued_at",
                                      placeholder="2026-07-05T12:00:00+00:00",
                                      help="Required for forecasts; leave blank for other records unless known.")
                    row = dict(day=day.isoformat() if day else None, kind=kind,
                               t_min_c=_number("Minimum air temperature (°C)", prefix + "t_min_c", low=-80.0, high=60.0),
                               t_max_c=_number("Maximum air temperature (°C)", prefix + "t_max_c", low=-80.0, high=60.0),
                               rh_min_pct=_number("Minimum relative humidity (%)", prefix + "rh_min_pct", high=100.0),
                               rh_max_pct=_number("Maximum relative humidity (%)", prefix + "rh_max_pct", high=100.0),
                               wind_m_s=_number("Wind speed (m/s)", prefix + "wind_m_s", high=100.0),
                               wind_height_m=wind_height, elevation_m=elevation,
                               rainfall_mm=_number("Daily rainfall (mm)", prefix + "rainfall",
                                                   help="Required for a budget. Enter zero only if known or explicitly assumed."),
                               source=_text("Source of weather, elevation and wind height", prefix + "source"))
                    if solar_mode:
                        row.update(input_type="solar_radiation_fao56", latitude_deg=latitude,
                            solar_radiation_mj_m2=_number("Daily incoming solar radiation (MJ/m²/day)",
                                prefix + "solar_radiation", high=60.0,
                                help="Integrated shortwave energy over the full day, not an instantaneous W/m² reading."))
                    else:
                        row["net_radiation_mj_m2"] = _number("Daily net radiation (MJ/m²/day)",
                            prefix + "radiation", low=-50.0, high=60.0,
                            help="Net radiation, not incoming solar radiation.")
                    if issued_at.strip():
                        row["issued_at"] = issued_at.strip()
                    weather.append(row)
                    if task == "root_zone_budget":
                        events.append(dict(day=row["day"],
                            net_irrigation_mm=_number("Assumed net irrigation (mm)", prefix + "irrigation",
                                                      help="Water reaching the root zone. A proposed amount is a scenario, not a completed action."),
                            runoff_mm=_number("Assumed rainfall runoff (mm)", prefix + "runoff"),
                            source=_text("Source of irrigation and runoff assumptions", prefix + "event_source")))
            payload = dict(schema_version=1, site=dict(name=unit["name"], timezone=farm["timezone"]),
                           area_m2=area * 10000 if area is not None and area_unit == "hectares" else area,
                           crop=dict(name=crop, **coefficients),
                           weather=weather)
            if bundle:
                payload["weather_sources"] = bundle["weather_sources"]
            depletion = initial_source = None
            if task == "root_zone_budget":
                with tabs[2]:
                    st.caption("Soil water contents are volumetric percentages: for example, 32 means 0.32 m³/m³. "
                               "A source label preserves provenance; it does not validate the supplied values.")
                    payload["soil"] = dict(
                        field_capacity=_fraction(_number("Field capacity (volume %)", "fc", high=100.0)),
                        wilting_point=_fraction(_number("Wilting point (volume %)", "wp", high=100.0)),
                        root_depth_m=_number("Root depth (m)", "root_depth", high=10.0),
                        depletion_fraction=_fraction(_number("Readily available fraction of total available water (%)",
                                                             "readily_available", high=100.0)),
                        source=_text("Source and applicability of soil properties", "soil_source"))
                    if daily_roots:
                        st.caption("The root depth above describes the initial boundary, before the first weather day.")
                        payload["root_zone_schedule"] = root_zone_fields(_PREFIX,
                            [(start + timedelta(days=i)).isoformat() if start else None for i in range(count)])
                    payload["application_efficiency"] = dict(
                        value=_fraction(_number("Application efficiency (%)", "efficiency", high=100.0)),
                        source=_text("Source of application efficiency", "efficiency_source"))
                    initial_day = start - timedelta(days=1) if start else None
                    st.caption(f"Initial soil state: end of {initial_day.isoformat() if initial_day else 'the day before the window'}. "
                               "Leave depletion and its source blank if unknown. Without this state, refill amounts stay unknown.")
                    depletion = _number("Initial root-zone depletion (mm; optional)", "depletion",
                                        help="Water missing relative to field capacity, not a moisture percentage. "
                                             "Zero means the root zone is at field capacity; do not assume zero if unknown.")
                    initial_source = _text("Source of initial soil state (if supplied)", "initial_source")
                    payload["events"] = events
                    if depletion is not None:
                        payload["initial_state"] = dict(as_of=initial_day.isoformat() if initial_day else None,
                                                        depletion_mm=depletion, source=initial_source)
            if st.form_submit_button("Calculate and save field water"):
                if start is None:
                    st.error("Choose the first weather day before calculating.")
                elif use_provider and bundle is None:
                    st.error("Fetch and review weather for the selected dates before calculating.")
                elif depletion is None and initial_source:
                    st.error("Supply initial depletion with its source, or leave both blank if unknown.")
                else:
                    return dict(task=task, inputs=payload)
    return None
