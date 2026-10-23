"""Daily surface-evaporation intake with explicit wetting and coefficient evidence."""

import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

import streamlit as st

from agronaut_agent.field_cli import _reject_constant, _unique_keys


def render_evaporation_intake(active, farm, cycle):
    scope = (active["id"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None, farm["timezone"])
    if st.session_state.get("field_dual_scope") != scope:
        for key in list(st.session_state):
            if key.startswith("field_dual_"):
                del st.session_state[key]
        st.session_state.field_dual_scope = scope
    p = "field_dual_"
    with st.expander("Calculate basal demand and soil evaporation"):
        st.caption("Track the surface layer drying after early rain or irrigation. Supply basal Kcb, Kc max and daily fractions with their evidence. "
                   "Surface depletion is separate from root-zone depletion. This calculates potential demand, not an irrigation requirement.")
        weather_mode = st.radio("Weather input for dual crop demand", ["Enter daily weather", "Import weather snapshot"], key=p+"weather_mode")
        imported = weather_mode == "Import weather snapshot"
        with st.form(p+"form"):
            crop = st.text_input("Crop for dual-coefficient calculation", value=cycle["crop"] if cycle else "", key=p+"crop")
            area = st.number_input("Represented field area (m²)", min_value=0.0, value=None, key=p+"area")
            soil = {}
            for key, label in (("field_capacity", "Surface field capacity (m³/m³)"), ("wilting_point", "Surface wilting point (m³/m³)"),
                               ("depth_m", "Evaporating soil depth (m)"), ("readily_evaporable_mm", "Readily evaporable water REW (mm)")):
                soil[key] = st.number_input(label, min_value=0.0, value=None, key=p+key)
            soil["source"] = st.text_input("Surface soil properties and REW source", key=p+"soil_source")
            as_of = st.text_input("Initial surface state as of (YYYY-MM-DD, preceding the first day)", key=p+"as_of")
            depletion = st.number_input("Initial surface depletion (mm)", min_value=0.0, value=None, key=p+"depletion")
            initial_source = st.text_input("Initial surface-state evidence", key=p+"initial_source")
            conditions = {}
            for key, label in (("basal_unstressed", "Basal crop ET can be treated as unstressed"),
                               ("surface_transpiration_negligible", "Root extraction from the evaporating surface layer is negligible"),
                               ("no_mulch_or_frozen_soil", "There is no mulch, snow or frozen soil")):
                conditions[key] = st.checkbox(label, key=p+key)
            conditions["source"] = st.text_input("Applicability evidence and early-wetting assumption", key=p+"conditions_source")
            if imported:
                st.caption("Paste a downloaded Agronaut weather snapshot containing weather and weather_sources. "
                           "ET, rain, dates and forecast labels will be retained exactly and checked against the snapshot. "
                           "This import does not fetch weather. Supply crop and irrigation rows for exactly the same dates.")
                snapshot_json = st.text_area("Weather snapshot JSON", key=p+"snapshot")
            else:
                kind = st.selectbox("ET and rainfall evidence", ["observed", "historical", "forecast", "scenario"], index=None, key=p+"kind")
                method = st.text_input("Grass-reference ET method", key=p+"method")
                weather_source = st.text_input("ET and rainfall source", key=p+"weather_source")
                issued = st.text_input("Forecast issue timestamp with offset (required for forecasts)", key=p+"issued")
            source = st.text_input("Crop, daily coefficients, wetting fractions and water-event evidence", key=p+"source")
            columns = "date, " + ("" if imported else "ET0 (mm), rain (mm), ")
            st.caption("One CSV row per consecutive day, without a header: " + columns + "net irrigation (mm), runoff (mm), "
                       "Kcb, Kc max, canopy cover fraction, physical wetted fraction, exposed-and-wetted fraction. "
                       "Use explicit zeros. Fractions are 0–1; exposed/wetted fraction must fit within both wetting and exposure. "
                       "Daily ET periods use the farm's local calendar, including daylight-saving changes.")
            raw = st.text_area("Dual-coefficient daily rows", key=p+"rows")
            submit = st.form_submit_button("Calculate and save dual crop demand")
        if submit:
            try:
                rows = list(csv.reader(StringIO(raw.strip()), strict=True))
                count = 8 if imported else 10
                if not 1 <= len(rows) <= 1000 or any(len(row) != count for row in rows):
                    raise ValueError(f"Supply 1–1000 daily rows with all {count} columns and explicit zero-water values.")
                weather, days = [], []
                extra = {}
                if imported:
                    bundle = json.loads(snapshot_json, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
                    if (not isinstance(bundle, dict) or not isinstance(bundle.get("weather"), list)
                            or not isinstance(bundle.get("weather_sources"), list) or not bundle["weather_sources"]):
                        raise ValueError("Weather snapshot requires weather and a nonempty weather_sources array.")
                    weather = bundle["weather"]
                    extra["weather_sources"] = bundle["weather_sources"]
                zone = ZoneInfo(farm["timezone"])
                for row in rows:
                    stamp = row[0].strip()
                    day = date.fromisoformat(stamp)
                    values = [float(v.strip()) for v in row[1:]]
                    if not imported:
                        et0, rain, *values = values
                        start = datetime.combine(day, time(), zone).astimezone(timezone.utc)
                        end = datetime.combine(day+timedelta(days=1), time(), zone).astimezone(timezone.utc)
                        weather.append(dict(day=stamp, input_type="supplied_et0", et0_mm_day=et0, rainfall_mm=rain, source=weather_source,
                            kind=kind, timezone=farm["timezone"], period_hours=(end-start).total_seconds()/3600, method=method,
                            **(dict(issued_at=issued) if issued.strip() else {})))
                    irrigation, runoff, kcb, kcmax, cover, wet, exposed = values
                    days.append(dict(day=stamp, kcb=kcb, kc_max=kcmax, cover_fraction=cover, wetted_fraction=wet,
                        exposed_wetted_fraction=exposed, net_irrigation_mm=irrigation, runoff_mm=runoff, source=source))
                return dict(task="dual_crop_water", inputs=dict(schema_version=1, site=dict(name=farm["name"], timezone=farm["timezone"]),
                    area_m2=area, crop=dict(name=crop, source=source), surface_soil=soil,
                    initial_state=dict(as_of=as_of, depletion_mm=depletion, source=initial_source),
                    conditions=conditions, weather=weather, days=days, **extra))
            except (ValueError, OverflowError, csv.Error) as exc:
                st.error(str(exc))
    return None
