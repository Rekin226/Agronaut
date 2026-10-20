"""Reviewed layer observations initialize a new continuation without rewriting fluxes."""

import csv
import json

import streamlit as st

from .coupled_water_ui import _reset, _table

MEMORY = ("id", "stage_one_remaining_mm", "active_depth_m", "stage_two_start_fraction", "source")


def _apply(saved_id, cycle_id, p, selected, reviews, cells, save_revision):
    try:
        s = st.session_state
        if s.get("field_plan") != saved_id:
            raise ValueError("The selected calculation changed; review the soil-state draft.")
        if s.get("field_cycle") != cycle_id:
            raise ValueError("The selected crop cycle changed; review the soil-state draft.")
        readings = []
        for identifier in selected:
            q = p+identifier+"_"
            cell = cells.get(s.get(q+"layer"))
            if cell is None:
                raise ValueError("Select the complete modeled layer represented by each observation.")
            patch, layer = cell
            readings.append(dict(observation_id=identifier, patch_id=patch, layer_id=layer["id"],
                top_m=layer["top_m"]["value"], bottom_m=layer["bottom_m"]["value"],
                representation_note=s[q+"coverage"], water_content_adjustment=s[q+"adjustment"],
                adjustment_source=s[q+"timing"], expected_review=reviews[identifier]))
        mode = s.get(p+"memory_mode")
        if mode == "Retain reviewed current memories":
            memories = [dict(id=patch, retain=True, source=s[p+"memory_source"]) for patch in sorted({r["patch_id"] for r in readings})]
        elif mode == "Supply replacement memories":
            memories = list(_table(s[p+"memories"], MEMORY, ("id",), "reviewed drying memories", 100).values())
            for row in memories:
                if row["stage_two_start_fraction"] == "-":
                    row["stage_two_start_fraction"] = None
        else:
            raise ValueError("Choose how each affected patch's drying memory was reviewed.")
        save_revision("apply_coupled_state", dict(calculation_id=saved_id, readings=readings, evaporation_memories=memories,
            dataset_kind=s[p+"kind"], reason=s[p+"reason"]))
    except (ValueError, TypeError, csv.Error) as exc:
        st.session_state.field_error = str(exc)


def render_coupled_state(call, saved, save_revision):
    p = "field_coupled_state_"
    _reset(p, saved["id"])
    if saved["task"] != "coupled_soil_water":
        return
    if revision := saved.get("coupled_state_revision"):
        st.info(f"Continuation uses a reviewed soil state at {revision['as_of']}. "
                f"Its soil storage differs from the original prediction by {revision['cumulative_storage_adjustment_l']['value']} L. "
                "This adjustment is separate from rainfall, irrigation and the original water balance.")
    with st.expander("Review soil measurements for the continuation state"):
        try:
            review = call("review_coupled_state", dict(calculation_id=saved["id"]))
        except ValueError as exc:
            st.error(str(exc))
            return
        state = review["current_state"]
        st.caption(review["note"]+f" Boundary: {state['as_of']} in {state['timezone']}.")
        for row in review["linked_measurements"]:
            if row["needs_review"]:
                e = row["evidence"]
                st.warning(f"Adopted observation {e['observation']['id']} at {e['as_of']} needs review. "
                           "Revise or withdraw it at that original boundary before continuing.")
        st.dataframe([dict(patch=key, **{k:v["value"] if isinstance(v, dict) else v for k,v in memory.items()})
                      for key, memory in state["evaporation_memories"]], hide_index=True)
        st.caption("Drying memory units: stage-one remainder in mm; active depth in m; stage-two reference is dimensionless. "
                   "Retain a current memory only when its applicability has been reviewed; a soil reading alone cannot establish drying history.")
        eligible = {r["id"]: r for r in review["measurements"] if r["effective_quality"] != "rejected" and r["provenance"] != "forecast"}
        key = p+"readings"
        if key in st.session_state:
            st.session_state[key] = [i for i in st.session_state[key] if i in eligible]
        selected = st.multiselect("Soil observations to adopt", list(eligible), key=key,
            format_func=lambda i: f"{eligible[i]['observed_at']} · {eligible[i]['value']:g} m³/m³ · {i}")
        cells = {json.dumps([patch["id"], cell["id"]]): (patch["id"], cell) for patch in state["soil"]["patches"] for cell in patch["compartments"]}
        reviews = {i: eligible[i]["quality_review"]["id"] if eligible[i]["quality_review"] else None for i in selected}
        with st.form(p+"form"):
            st.selectbox("Adopted soil dataset kind", ["synthetic", "field"], index=None, key=p+"kind")
            st.text_input("Soil-state revision reason and evidence", key=p+"reason")
            for identifier in selected:
                q, r = p+identifier+"_", eligible[identifier]
                st.caption(f"{identifier}: {r['provenance']} · {r['effective_quality']} · {r['source']}")
                st.selectbox("Complete layer represented by this observation", list(cells), index=None, key=q+"layer",
                    format_func=lambda k: f"{cells[k][0]} / {cells[k][1]['id']} · {cells[k][1]['top_m']['value']}–{cells[k][1]['bottom_m']['value']} m")
                st.text_input("Evidence for complete layer and patch sampling coverage", key=q+"coverage")
                st.number_input("Adopted water-content adjustment to closing midnight (m³/m³)", value=None,
                    min_value=-1.0, max_value=1.0, format="%.6f", key=q+"adjustment")
                st.text_input("Adopted timing adjustment evidence or explicit zero-change assumption", key=q+"timing")
            st.selectbox("Drying memory review", ["Retain reviewed current memories", "Supply replacement memories"], index=None, key=p+"memory_mode")
            st.text_input("Evidence for retaining the affected patch memories", key=p+"memory_source")
            st.text_area("Replacement drying-memory rows", key=p+"memories", help="Comma-separated rows without headers: "+", ".join(MEMORY)+
                ". Include exactly affected patches. Use - for the stage-two reference only when stage-one remainder is positive.")
            st.form_submit_button("Save reviewed soil continuation state", on_click=_apply,
                args=(saved["id"], saved["cycle_id"], p, selected, reviews, cells, save_revision))
        if revision:
            with st.form(p+"remove_form"):
                st.text_input("Reason to withdraw all ending-state soil revisions", key=p+"remove_reason")

                def remove():
                    if st.session_state.get("field_plan") != saved["id"]:
                        st.session_state.field_error = "The selected calculation changed; review the withdrawal."
                        return
                    if st.session_state.get("field_cycle") != saved["cycle_id"]:
                        st.session_state.field_error = "The selected crop cycle changed; review the withdrawal."
                        return
                    save_revision("remove_coupled_state", dict(calculation_id=saved["id"], reason=st.session_state[p+"remove_reason"]))

                st.form_submit_button("Restore the modeled ending state in a new version", on_click=remove)
