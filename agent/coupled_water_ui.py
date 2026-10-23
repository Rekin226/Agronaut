"""Native spreadsheet-row intake and continuation for coupled soil-water replay."""

import csv
import json
from datetime import datetime, time, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

import streamlit as st

from agro_model.soil_snapshots import loads_soil_snapshot
from agronaut_agent.coupled_state_workflow import continuation_checkpoint
from agronaut_agent.coupled_water_io import _json_value
from agronaut_agent.field_cli import _reject_constant, _unique_keys
from agronaut_agent.field_io import _date

PATCH = ("patch", "area_fraction", "surface_water_mm", "surface_capacity_mm", "surface_source")
LAYER = ("patch", "layer", "top_m", "bottom_m", "field_capacity", "wilting_point", "saturation", "evaporation_floor",
         "water_content", "tau", "ksat_mm_day", "soil_source", "drainage_source")
EVAPORATION = ("patch", "minimum_depth_m", "maximum_depth_m", "readily_evaporable_mm", "decline_factor",
               "stage_one_remaining_mm", "active_depth_m", "stage_two_start_fraction", "parameter_source", "memory_source")
WEATHER = ("day", "et0_mm_day", "weather_rainfall_mm")
WATER = ("day", "patch", "rainfall_mm", "rainfall_runoff_mm", "irrigation_at_surface_mm", "water_source",
         "wet_soil_coefficient", "ponded_water_coefficient", "evaporation_source")
ROOTS = ("day", "patch", "root_depth_m", "topsoil_depth_m", "stomatal_threshold", "local_shape_factor",
         "root_zone_aeration_factor", "root_parameter_source", "transpiration_coefficient", "cold_stress_factor", "transpiration_source")
ACTIVITY = ("day", "patch", "layer", "maximum_rate_per_day", "aeration_factor", "source")


def _table(raw, columns, keys, label, maximum):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Enter {label}; unknown values cannot be replaced by zero.")
    rows = list(csv.reader(StringIO(raw.strip()), strict=True))
    if not 1 <= len(rows) <= maximum or any(len(row) != len(columns) for row in rows):
        raise ValueError(f"{label} requires 1–{maximum} rows with {len(columns)} columns, without a header.")
    result = {}
    for values in rows:
        row = dict(zip(columns, (v.strip() for v in values)))
        if any(not value for value in row.values()):
            raise ValueError(f"Every column in {label} must be explicit, including zero values and sources.")
        key = tuple(row[k] for k in keys)
        if key in result:
            raise ValueError(f"Duplicate {label} row for {', '.join(key)}.")
        result[key] = row
    return result


def build_initial_rows(patches, layers, evaporation, *, as_of, kind, quality, source):
    patch_rows = _table(patches, PATCH, ("patch",), "patch rows", 100)
    layer_rows = _table(layers, LAYER, ("patch", "layer"), "soil layer rows", 1000)
    evap_rows = _table(evaporation, EVAPORATION, ("patch",), "initial evaporation rows", 100)
    if set(evap_rows) != set(patch_rows) or {(key[0],) for key in layer_rows} != set(patch_rows):
        raise ValueError("Initial layer and evaporation rows must cover exactly the declared patches.")
    result = []
    for (identity,), row in patch_rows.items():
        cells = []
        for (patch, layer), cell in layer_rows.items():
            if patch == identity:
                cells.append(dict(id=layer, **{k: cell[k] for k in LAYER[2:9]}, soil_source=cell["soil_source"],
                    drainage=dict(tau=cell["tau"], saturated_conductivity_mm_day=cell["ksat_mm_day"], source=cell["drainage_source"])))
        evap = evap_rows[(identity,)]
        reference = None if evap["stage_two_start_fraction"] == "-" else evap["stage_two_start_fraction"]
        result.append(dict(id=identity, area_fraction=row["area_fraction"], layers=cells,
            surface_water=dict(stored_mm=row["surface_water_mm"], capacity_mm=row["surface_capacity_mm"], source=row["surface_source"]),
            evaporation_parameters=dict(**{k: evap[k] for k in EVAPORATION[1:5]}, source=evap["parameter_source"]),
            evaporation_memory=dict(stage_one_remaining_mm=evap["stage_one_remaining_mm"], active_depth_m=evap["active_depth_m"],
                                    stage_two_start_fraction=reference, source=evap["memory_source"])))
    return dict(as_of=as_of, kind=kind, quality=quality, source=source, patches=result)


def build_daily_rows(water, roots, activities, *, source):
    water_rows = _table(water, WATER, ("day", "patch"), "daily water and evaporation rows", 36600)
    root_rows = _table(roots, ROOTS, ("day", "patch"), "daily crop and root rows", 36600)
    activity_rows = _table(activities, ACTIVITY, ("day", "patch", "layer"), "daily root activity rows", 366000)
    if set(root_rows) != set(water_rows) or {key[:2] for key in activity_rows} != set(water_rows):
        raise ValueError("Daily water, crop and root activity rows must cover exactly the same dates and patches.")
    grouped_activity = {}
    for (day, patch, layer), row in activity_rows.items():
        grouped_activity.setdefault((day, patch), []).append(dict(id=layer, **{k: row[k] for k in ACTIVITY[3:]}))
    days = {}
    for (day, patch), row in water_rows.items():
        _date(day)
        root = root_rows[(day, patch)]
        activity = grouped_activity[(day, patch)]
        value = dict(id=patch,
            water=dict(**{k: row[k] for k in WATER[2:5]}, source=row["water_source"]),
            evaporation_coefficients=dict(wet_soil_coefficient=row["wet_soil_coefficient"],
                ponded_water_coefficient=row["ponded_water_coefficient"], source=row["evaporation_source"]),
            root_parameters=dict(**{k: root[k] for k in ROOTS[2:7]}, source=root["root_parameter_source"]),
            root_activities=activity, transpiration_coefficients=dict(transpiration_coefficient=root["transpiration_coefficient"],
                cold_stress_factor=root["cold_stress_factor"], source=root["transpiration_source"]))
        days.setdefault(day, dict(day=day, patches=[], source=source))["patches"].append(value)
    return list(days.values())


def build_weather(raw, *, zone, kind, method, source, issued):
    rows = _table(raw, WEATHER, ("day",), "weather rows", 366)
    local = ZoneInfo(zone)
    result = []
    for (stamp,), row in rows.items():
        day = _date(stamp)
        start = datetime.combine(day, time(), local).astimezone(timezone.utc)
        end = datetime.combine(day+timedelta(days=1), time(), local).astimezone(timezone.utc)
        result.append(dict(day=stamp, input_type="supplied_et0", et0_mm_day=float(row["et0_mm_day"]),
            rainfall_mm=None if row["weather_rainfall_mm"] == "?" else float(row["weather_rainfall_mm"]),
            source=source, kind=kind, timezone=zone, period_hours=(end-start).total_seconds()/3600, method=method,
            **(dict(issued_at=issued) if issued.strip() else {})))
    return result


def read_weather_snapshot(uploaded):
    if uploaded is None:
        raise ValueError("Choose a downloaded weather snapshot, or enter weather rows.")
    raw = uploaded.getvalue()
    if len(raw) > 8*1024*1024:
        raise ValueError("Weather snapshot exceeds 8 MiB.")
    data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
    if (not isinstance(data, dict) or not isinstance(data.get("weather"), list)
            or not isinstance(data.get("weather_sources"), list) or not data["weather_sources"]):
        raise ValueError("Weather snapshot requires weather and nonempty weather_sources.")
    return {key: data[key] for key in ("weather", "weather_sources")}


def _context(active, farm, cycle):
    lifecycle = cycle.get("lifecycle", {}) if cycle else {}
    return (active["id"], farm["timezone"], (cycle["id"], cycle["crop"], cycle["planted_on"],
            lifecycle.get("status"), lifecycle.get("ended_on")) if cycle else None)


def _reset(prefix, scope):
    if st.session_state.get(prefix+"scope") != scope:
        for key in list(st.session_state):
            if key.startswith(prefix):
                del st.session_state[key]
        st.session_state[prefix+"scope"] = scope


def _rows_widget(label, columns, key, explanation):
    st.caption(explanation)
    return st.text_area(label, key=key, help="CSV columns, without a header: "+", ".join(columns))


def _daily_widgets(prefix, imported):
    if imported:
        st.file_uploader("Downloaded weather snapshot", type=["json"], key=prefix+"snapshot",
            help="Import retains provider values, dates and evidence. This does not fetch weather.")
    else:
        st.selectbox("Daily ET weather evidence", ["observed", "historical", "forecast", "scenario"], index=None, key=prefix+"weather_kind")
        st.text_input("Grass-reference ET method", key=prefix+"weather_method")
        st.text_input("Weather source", key=prefix+"weather_source")
        st.text_input("Forecast issue timestamp with UTC offset", key=prefix+"issued")
        _rows_widget("Coupled weather rows", WEATHER, prefix+"weather_rows",
            "One row per consecutive local day: date, grass-reference ET0 (mm), weather rainfall (mm or ? if unknown). "
            "Water entering this calculation comes from the patch water rows below.")
    st.text_input("Daily calculation context and source", key=prefix+"daily_source")
    _rows_widget("Daily patch water and evaporation rows", WATER, prefix+"water_rows",
        "One row per date and patch. Rain, rainfall runoff and irrigation are local patch millimeters. "
        "Irrigation arrives at the soil surface after upstream losses. Supply adjusted wet-soil and ponded evaporation coefficients with sources.")
    _rows_widget("Daily crop and root parameter rows", ROOTS, prefix+"root_rows",
        "One row per date and patch. Root/topsoil depths are meters; stress factors and coefficients are dimensionless. "
        "Supply the ET0-adjusted stomatal threshold, local response shape, root-zone aeration factor, crop transpiration coefficient and cold factor.")
    _rows_widget("Daily root activity rows", ACTIVITY, prefix+"activity_rows",
        "One row per date, patch and soil layer. Maximum root sink is m³ water per m³ bulk soil per day, before stress; "
        "aeration is a 0–1 factor. Include explicit zero activity below the day's root depth.")


def _daily_input(prefix, zone):
    state = st.session_state
    days = build_daily_rows(state[prefix+"water_rows"], state[prefix+"root_rows"], state[prefix+"activity_rows"],
                            source=state[prefix+"daily_source"])
    if state[prefix+"weather_mode"] == "Import downloaded weather":
        weather = read_weather_snapshot(state.get(prefix+"snapshot"))
    else:
        weather = dict(weather=build_weather(state[prefix+"weather_rows"], zone=zone, kind=state[prefix+"weather_kind"],
            method=state[prefix+"weather_method"], source=state[prefix+"weather_source"], issued=state[prefix+"issued"]))
    return dict(days=days, **weather)


def render_coupled_water_intake(active, farm, cycle):
    p = "field_coupled_new_"
    context = _context(active, farm, cycle)
    if st.session_state.get(p+"scope") != context:
        _reset("field_coupled_next_", None)
        _reset("field_coupled_compare_", None)
        _reset("field_surface_water_", None)
        _reset("field_coupled_state_", None)
    _reset(p, context)
    with st.expander("Calculate coupled soil water"):
        st.caption("Track drainage, infiltration, evaporation and root uptake together. Enter comma-separated spreadsheet rows without headers; "
                   "each table's help lists the column order. Quote sources containing commas. Use explicit zeros; blanks are unknown. "
                   "Use a decimal point; soil and crop quantities allow up to six decimal places. "
                   "Values require evidence for this crop and soil; this is an experimental calculation.")
        mode = st.radio("Weather for coupled soil water", ["Enter daily weather", "Import downloaded weather"], key=p+"weather_mode")
        # Weather controls outside this section rerun the page. Persist edits as they
        # happen so switching weather mode does not discard an unsubmitted soil draft.
        with st.container():
            crop = st.text_input("Crop for coupled soil water", value=cycle["crop"] if cycle else "", key=p+"crop")
            crop_source = st.text_input("Crop and parameter context source", key=p+"crop_source")
            area = st.text_input("Total represented ground area (m²)", key=p+"area")
            as_of = st.text_input("Initial water state at end of day (YYYY-MM-DD)", key=p+"as_of")
            kind = st.selectbox("Initial soil-water evidence", ["observed", "scenario"], index=None, key=p+"initial_kind")
            quality = st.selectbox("Initial state review", ["unverified", "checked"], index=None, key=p+"initial_quality")
            source = st.text_input("Initial water-state source and represented footprint/time", key=p+"initial_source")
            patches = _rows_widget("Initial patch and surface-water rows", PATCH, p+"patch_rows",
                "One row per disjoint patch: name, area fraction, stored surface water (mm), surface capacity (mm), source. Fractions must sum to one.")
            layers = _rows_widget("Initial soil-layer rows", LAYER, p+"layer_rows",
                "Contiguous layers from the surface, in depth order for each patch. Depths are meters and water contents are m³/m³ of bulk soil. "
                "Tau is dimensionless; saturated conductivity is mm/day. Include boundaries at every root depth and maximum evaporation depth you will use.")
            evap = _rows_widget("Initial evaporation and drying-memory rows", EVAPORATION, p+"evaporation_rows",
                "One row per patch: evaporation depths (m), REW (mm), decline factor and initial drying memory. "
                "Stage-one remainder is mm; active depth is m. Use - for the stage-two reference only while stage-one remainder is positive; "
                "otherwise supply its explicit 0–1 value. This method requires the air-dry floor to equal half wilting point.")
            conditions = {}
            for key, label in (("bulk_soil_properties", "Soil properties represent bulk soil volume"),
                               ("no_capillary_rise", "Capillary rise can be excluded for this calculation"),
                               ("no_frozen_soil", "Snow and frozen soil can be excluded"),
                               ("no_salinity_effects", "Salinity effects can be excluded")):
                conditions[key] = st.checkbox(label, key=p+key)
            conditions["source"] = st.text_input("Evidence for these model conditions", key=p+"conditions_source")
            _daily_widgets(p, mode == "Import downloaded weather")
            submit = st.button("Calculate and save coupled soil water")
        if submit:
            try:
                initial = build_initial_rows(patches, layers, evap, as_of=as_of, kind=kind, quality=quality, source=source)
                return dict(task="coupled_soil_water", inputs=dict(schema_version=1,
                    site=dict(name=farm["name"], timezone=farm["timezone"]), area_m2=area,
                    crop=dict(name=crop, source=crop_source), initial_state=initial, conditions=conditions,
                    **_daily_input(p, farm["timezone"])))
            except (ValueError, TypeError, OverflowError, UnicodeError, csv.Error) as exc:
                st.error(str(exc))
    return None


def render_coupled_continuation(saved, active, farm, cycle, save_revision):
    p = "field_coupled_next_"
    _reset(p, (_context(active, farm, cycle), saved["id"]))
    if saved["task"] != "coupled_soil_water":
        return
    with st.expander("Continue coupled soil water"):
        result = saved["result"]["result"]
        st.caption(f"Continue from the selected ending water state at {result['period_end']}. "
                   "Patch areas, soil properties, surface water and drying memory are retained. Enter consecutive new days with complete crop and water inputs.")
        ending_soil = _json_value(loads_soil_snapshot(continuation_checkpoint(saved), expected_kind="state").soil)
        st.dataframe([{"Patch": patch["id"], "Layer": cell["id"], "Top (m)": cell["top_m"]["value"], "Bottom (m)": cell["bottom_m"]["value"],
                       "Water (m³/m³)": cell["water_content"]["value"]}
                      for patch in ending_soil["patches"] for cell in patch["compartments"]], hide_index=True)
        mode = st.radio("Weather for coupled continuation", ["Enter daily weather", "Import downloaded weather"], key=p+"weather_mode")

        def submit():
            try:
                if st.session_state.get("field_plan") != saved["id"]:
                    raise ValueError("The selected calculation changed; review the continuation draft.")
                if st.session_state.get("field_cycle") != saved["cycle_id"]:
                    raise ValueError("The selected crop cycle changed; review the continuation draft.")
                data = dict(calculation_id=saved["id"], reason=st.session_state[p+"reason"], **_daily_input(p, farm["timezone"]))
                save_revision("continue_coupled_water", data)
            except (ValueError, TypeError, OverflowError, UnicodeError, csv.Error) as exc:
                st.session_state.field_error = str(exc)

        with st.container():
            st.text_input("Continuation purpose and evidence", key=p+"reason")
            _daily_widgets(p, mode == "Import downloaded weather")
            st.button("Continue and save coupled soil water", on_click=submit)
