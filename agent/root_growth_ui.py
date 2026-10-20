"""Explicit daily rooting depths and newly reached subsoil-water assumptions."""

import streamlit as st


def root_zone_fields(prefix, days, initial=()):
    existing = {r["day"]: r for r in initial}
    st.caption("Depth applies at the start of each day and cannot decrease. Each increase needs the average water "
               "content of the newly reached layer immediately before inclusion, and its source. "
               "Leave both added-layer fields blank on days with unchanged depth. Soil capacity properties remain homogeneous.")
    result = []
    for index, day in enumerate(days):
        old = existing.get(day, {})
        key = f"{prefix}root_day_{index}_"
        st.markdown(f"**Root zone: {day or 'choose the weather window first'}**")
        depth = st.number_input("Daily effective root depth (m)", min_value=0.0, max_value=10.0,
                                value=float(old["root_depth_m"]) if old else None, key=key + "depth")
        source = st.text_input("Source of daily root depth", value=old.get("source", ""), key=key + "source")
        water = old.get("added_layer_water_content")
        theta = st.number_input("Newly reached layer water content (m³/m³; only for an increase)",
                                min_value=0.0, max_value=1.0, value=float(water) if water is not None else None, key=key + "water")
        layer_source = st.text_input("Source and timing of newly reached layer water content",
                                     value=old.get("added_layer_source") or "", key=key + "layer_source")
        result.append(dict(day=day, root_depth_m=depth, source=source,
                           added_layer_water_content=theta, added_layer_source=layer_source or None))
    return result


def _submit_roots(prefix, days, calculation_id, save):
    state = st.session_state
    schedule = []
    if state[prefix + "enabled"]:
        for index, day in enumerate(days):
            key = f"{prefix}root_day_{index}_"
            schedule.append(dict(day=day, root_depth_m=state[key + "depth"], source=state[key + "source"],
                                 added_layer_water_content=state[key + "water"], added_layer_source=state[key + "layer_source"] or None))
    save("revise_roots", dict(calculation_id=calculation_id, root_zone_schedule=schedule, reason=state[prefix + "reason"]))


def render_root_revision(saved, save):
    prefix = f"field_roots_{saved['id']}_"
    days = [w["day"] for w in saved["inputs"]["weather"]]
    initial = saved["inputs"].get("root_zone_schedule", [])
    with st.expander("Review and revise daily root depths"):
        st.caption(f"Initial boundary root depth: {saved['inputs']['soil']['root_depth_m']:g} m. "
                   "Revisions preserve initial soil properties, weather, water logs and measurement evidence. "
                   "If a retained measurement no longer represents the day's full root depth, withdraw its state update first.")
        enabled = st.checkbox("Use daily root depths", value=bool(initial), key=prefix + "enabled")
        with st.form(prefix + "form"):
            if enabled:
                root_zone_fields(prefix, days, initial)
            else:
                st.caption("Every day will use the initial boundary root depth.")
            st.text_input("Reason for revising root depths", key=prefix + "reason")
            st.form_submit_button("Save root-depth revision", on_click=_submit_roots,
                                   args=(prefix, days, saved["id"], save))
