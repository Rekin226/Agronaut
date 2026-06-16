"""Streamlit view for the M2 optimizer — find the fish/crop ratio for max efficiency.

A structured form → optimize() → the best ratio, the ranked alternatives, the improvement
over a naive even split, and the honesty layer. Pure deterministic search, no LLM.
"""

from __future__ import annotations

import streamlit as st

from aqua_model import optimize, OptimizeInput, OBJECTIVES
from aqua_model.crops import CROPS
from aqua_model.species import SPECIES

_OBJECTIVE_LABELS = {
    "water_efficiency": "Water efficiency (food per m³ water)",
    "food": "Total food (kg/year)",
    "protein": "Total protein (kg/year)",
}


def render_optimizer() -> None:
    st.subheader("Optimize Ratio")
    st.caption(
        "Search fish × crop-mix combinations for the most efficient ratio under your "
        "constraint. Deterministic — no AI guessing; every result is reproducible."
    )

    with st.form("optimize_form"):
        col1, col2 = st.columns(2)
        with col1:
            grow_area = st.number_input("Grow area (m²)", min_value=0.1, value=10.0, step=0.5)
            temperature = st.number_input("Mean water temp (°C)", min_value=0.0, max_value=45.0, value=28.0, step=0.5)
            water_budget = st.number_input("Water budget (L/day)", min_value=0.0, value=5000.0, step=50.0)
        with col2:
            objective = st.selectbox(
                "Maximize", list(OBJECTIVES),
                format_func=lambda o: _OBJECTIVE_LABELS.get(o, o),
            )
            fish_palette = st.multiselect("Fish to consider", sorted(SPECIES), default=sorted(SPECIES))
            crop_palette = st.multiselect("Crops to consider", sorted(CROPS), default=sorted(CROPS))
        submitted = st.form_submit_button("Optimize", use_container_width=True)

    if not submitted:
        st.info("Set inputs and press **Optimize**.")
        return

    if not fish_palette or not crop_palette:
        st.error("Pick at least one fish and one crop.")
        return

    res = optimize(OptimizeInput(
        grow_area_m2=grow_area, temperature_c=temperature, water_budget_lpd=water_budget,
        objective=objective, fish_palette=tuple(fish_palette), crop_palette=tuple(crop_palette),
    ))

    if res.best is None:
        st.warning(
            f"No feasible design within {water_budget:g} L/day. "
            f"Searched {res.searched} combinations, none fit the water budget. "
            "Increase the budget or reduce grow area."
        )
        return

    b = res.best
    st.success(f"Best ratio: **{b.fish_species}** + " + _fmt_mix(b.crop_allocation))
    if res.improvement_vs_baseline_pct is not None:
        st.metric(
            f"{_OBJECTIVE_LABELS.get(objective, objective)}",
            f"{b.score:g}",
            delta=f"{res.improvement_vs_baseline_pct:+g}% vs even split",
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("Food", f"{b.food_kg_yr:g} kg/yr")
    c2.metric("Protein", f"{b.protein_kg_yr:g} kg/yr")
    c3.metric("Makeup water", f"{b.makeup_water_lpd:g} L/day")
    st.caption(f"Searched {res.searched} combinations, {res.feasible_count} feasible.")

    with st.expander("Top alternatives"):
        st.table([
            {
                "fish": c.fish_species,
                "crop mix": _fmt_mix(c.crop_allocation),
                "score": c.score,
                "food kg/yr": c.food_kg_yr,
                "makeup L/day": c.makeup_water_lpd,
            }
            for c in res.ranked[:8]
        ])
    with st.expander("What is NOT optimized (read before quoting outcomes)"):
        for n in res.not_modeled:
            st.markdown(f"- {n}")
    with st.expander("Assumptions"):
        for a in res.assumptions:
            st.markdown(f"- {a}")


def _fmt_mix(alloc: dict[str, float]) -> str:
    return ", ".join(f"{int(round(frac * 100))}% {crop}" for crop, frac in alloc.items())
