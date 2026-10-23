"""Streamlit view for the M1 design calculator.

A structured form (the right UI for fixed engineering inputs) → the validated trust gate
→ size_system() → results + the funder-facing report as a download. No LLM in this path:
every number shown is traceable to a cited coefficient.
"""

from __future__ import annotations

import streamlit as st

from agent import facts
from aqua_model import size_system
from aqua_model.report import to_markdown


def _render_channel_verdict(label: str, c: dict, mode: str) -> None:
    target, dne, median = c["target_band"], c["do_not_exceed_band"], c["median"]
    position = c["median_position"]

    if position == "within target band":
        icon, verdict = "✅", "real ponds sit inside your target band"
    elif dne[0] <= median <= dne[1]:
        icon = "⚠️"
        verdict = f"real ponds run {position.replace(' target band', '')} target (still within safe limits)"
    else:
        icon = "🚩"
        verdict = f"real ponds run {position.replace(' target band', '')} target — outside safe limits"

    st.markdown(f"**{icon} {label}** — {verdict}")
    line = f"real median **{median}**"
    if mode == "summary":
        line += f" (p5–p95: {c['p5']}–{c['p95']})"
    line += f"  ·  your target {target[0]}–{target[1]}  ·  safe {dne[0]}–{dne[1]}"
    if mode == "full":
        line += (
            f"  ·  **{c['frac_in_target'] * 100:.0f}%** of readings in target, "
            f"**{c['frac_in_do_not_exceed'] * 100:.0f}%** within safe limits"
        )
    st.caption(line)


def _render_reality_check(operating_envelope: dict) -> None:
    """Compare this design's envelope against real-pond readings (open dataset)."""
    from aqua_model import datasets

    reality = datasets.envelope_reality_check(operating_envelope)
    if reality is None:
        # The comparison needs an open dataset most installs never download. A panel that
        # only says "run this script" is a developer note, not something a grower can use.
        return
    with st.expander("Reality check — your envelope vs. real ponds"):
        n = reality.get("n_readings")
        scope = f"~{n:,} readings, " if n else ""
        st.caption(
            f"Compared against {scope}{reality['source']}. Only temperature and pH are "
            "cross-checked — turbidity and ammonia sensors in this dataset are saturated and "
            "not trustworthy."
        )
        labels = {"water_temp_c": "Water temperature (°C)", "ph": "pH"}
        for channel, c in reality["channels"].items():
            _render_channel_verdict(labels.get(channel, channel), c, reality["mode"])


def _render_coefficients(out, species: str, crops: list[str]) -> None:
    """Every coefficient the design used, then the seeds checked against published ranges.

    One panel. These used to be a table plus a second panel per crop repeating the same
    seed values, so a mixed bed showed the fish's coefficients three times.
    """
    from aqua_model import calibration as cal

    relevant = [c for c in cal.all_calibrations()
                if c.key.split(".")[0] in (species, *crops)]
    n_out = sum(not c.within for c in relevant)
    title = "Coefficients used (cited)" + (f"  ⚠️ {n_out} outside published range"
                                         if n_out else "")
    with st.expander(title):
        st.table([
            {"name": c.name, "value": c.value, "range": f"{c.low}–{c.high}", "unit": c.unit,
             "source": c.source}
            for c in out.coefficients_used
        ])
        if relevant:
            st.caption("Seed values against peer-reviewed ranges. ✅ in range · ⚠️ outside: "
                       "calibrate against your own system before building.")
            for c in relevant:
                icon = "✅" if c.within else "⚠️"
                st.markdown(f"**{icon} {c.label}**: seed **{c.seed} {c.unit}**, published "
                            f"**{c.emp_low}–{c.emp_high}** · _{c.verdict}_")
                st.caption(c.note + "  \nSources: " + "; ".join(c.sources))


_ENVELOPE_LABELS = {
    "ph_target": ("pH", "target"), "ph_do_not_exceed": ("pH", "never outside"),
    "temperature_target_c": ("Water temperature (°C)", "target"),
    "temperature_do_not_exceed_c": ("Water temperature (°C)", "never outside"),
    "dissolved_oxygen_min_mg_l": ("Dissolved oxygen (mg/L)", "at least"),
    "ammonia_nitrite_target": ("Ammonia and nitrite", "target"),
}


def envelope_rows(envelope: dict) -> list[dict]:
    """The operating envelope as rows a grower can read, instead of a JSON dump."""
    rows = []
    for key, value in envelope.items():
        what, kind = _ENVELOPE_LABELS.get(key, (key.replace("_", " "), ""))
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = f"{value[0]:g}–{value[1]:g}"
        rows.append({"reading": what, "": kind, "value": value})
    return rows


def nitrogen_summary(check: dict) -> str:
    """The nitrogen cross-check in one sentence: does the plant area the feed can support
    agree with the area that was sized?"""
    implied, sized = check.get("n_implied_area_m2"), check.get("frr_grow_area_m2")
    if implied is None or sized is None:
        return "Nitrogen cross-check unavailable for this design."
    gap = abs(check.get("disagreement_fraction") or 0) * 100
    verdict = ("agree" if check.get("agrees") else "DISAGREE")
    line = (f"The nitrogen the fish excrete supports about **{implied:g} m²** of plants; the "
            f"design sizes **{sized:g} m²**. The two methods {verdict} ({gap:.0f}% apart).")
    if check.get("flag"):
        line += f" {check['flag']}"
    return line


_N_FLOWS = {"n_fed_g_day": "fed", "n_retained_g_day": "kept in fish",
            "n_plant_uptake_g_day": "taken up by plants", "n_solids_g_day": "lost to solids",
            "n_water_exchange_g_day": "lost to water exchange",
            "n_denitrification_g_day": "lost to denitrification"}


def render_calculator(heading: bool = True) -> None:
    if heading:
        st.subheader("Design Calculator")
    st.caption(
        "Size one system from fixed inputs. Pure deterministic model — no AI guessing; "
        "every number is traceable to a cited coefficient."
    )

    with st.form("design_form"):
        col1, col2 = st.columns(2)
        with col1:
            species = st.selectbox("Fish species", facts.available_species())
            crops = st.multiselect(
                "Crops", facts.available_crops(), default=["lettuce"],
                help="Pick 2+ for a MIXED BED sharing one system — the grow area is split "
                     "evenly, and the model warns if the crops can't share one water.")
            site = st.text_input("Site / project name (optional)", "")
        with col2:
            grow_area = st.number_input("Grow area (m²)", min_value=0.1, value=6.0, step=0.5)
            temperature = st.number_input("Mean water temp (°C)", min_value=0.0, max_value=45.0, value=26.0, step=0.5)
            water_budget = st.number_input("Water budget (L/day)", min_value=0.0, value=200.0, step=10.0)
            system_type = st.selectbox(
                "Growing method", facts.available_system_types(),
                help="raft/DWC (forgiving, more water), NFT (light, low water, needs reliable "
                     "power), or media bed (robust, also biofilters).")
        submitted = st.form_submit_button("Size system", width="stretch")

    if not submitted:
        st.info("Set your inputs and press **Size system**.")
        return

    if not crops:
        st.error("Pick at least one crop.")
        return

    try:
        if len(crops) > 1:
            design = facts.design_from_form(
                fish_species=species, crop=None, grow_area_m2=None,
                temperature_c=temperature, water_budget_lpd=water_budget,
                system_type=system_type,
                crop_plan=facts.split_area_evenly(crops, grow_area),
            )
        else:
            design = facts.design_from_form(
                fish_species=species, crop=crops[0], grow_area_m2=grow_area,
                temperature_c=temperature, water_budget_lpd=water_budget,
                system_type=system_type,
            )
    except facts.ValidationError as err:
        st.error("Invalid inputs:\n" + "\n".join(f"- {e}" for e in err.errors))
        return

    out = size_system(design)
    _hand_off_to_twin(design, out)

    if out.feasible:
        st.success("Feasible design.")
    else:
        st.warning(f"Not feasible — binding constraint: **{out.binding_constraint}**.")

    if out.crop_plan:
        st.caption("Mixed bed (crops sharing the water): "
                   + ", ".join(f"{p['crop']} {p['area_m2']:g} m²" for p in out.crop_plan))

    for w in out.warnings:
        st.warning(w)

    m1, m2, m3 = st.columns(3)
    m1.metric("Feed", f"{out.feed_g_per_day:g} g/day")
    m2.metric("Fish", f"{out.fish_count} head")
    m3.metric("Biomass", f"{out.fish_biomass_kg:g} kg")
    m4, m5, m6 = st.columns(3)
    m4.metric("System volume", f"{out.system_volume_l:g} L")
    m5.metric("Pump", f"{out.pump_turnover_lph:g} L/h")
    m6.metric("Makeup water", f"{out.makeup_water_lpd:g} L/day")
    if out.footprint_ratio != 1.0:
        st.metric("Floor footprint", f"{out.footprint_m2:g} m²",
                  help=f"{out.grow_area_m2:g} m² of growing area packed onto the floor "
                       f"(~{out.footprint_ratio:g}× via vertical towers).")

    import base64

    from aqua_model.schematic import to_svg
    svg = to_svg(out)
    st.subheader("System schematic")
    # SVG rendered as a data-URI <img> (st.image's PIL path can't parse raw SVG).
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    st.markdown(f'<img src="data:image/svg+xml;base64,{b64}" style="max-width:100%"/>',
                unsafe_allow_html=True)
    st.download_button("Download schematic (SVG)", data=svg,
                       file_name="agronaut_schematic.svg", mime="image/svg+xml")

    with st.expander("Bill of materials"):
        st.table(out.bill_of_materials)
    with st.expander("Operating envelope"):
        st.table(envelope_rows(out.operating_envelope))
    _render_reality_check(out.operating_envelope)
    with st.expander("Nitrogen consistency check"):
        st.markdown(nitrogen_summary(out.nitrogen_check))
        st.table([{"nitrogen": label, "g/day": f"{out.nitrogen_check[key]:g}"}
                  for key, label in _N_FLOWS.items() if key in out.nitrogen_check])
    with st.expander("What is NOT modeled (read before building)"):
        for n in out.not_modeled:
            st.markdown(f"- {n}")
    _render_coefficients(out, species, crops or [])

    report_md = to_markdown(design, out, site=site or None)
    st.download_button(
        "Download design report (Markdown)",
        data=report_md,
        file_name=f"aquaponics-design-{(site or 'system').strip().replace(' ', '-').lower()}.md",
        mime="text/markdown",
        width="stretch",
    )


def _hand_off_to_twin(design, out) -> None:
    """Seed the private Agronaut Twin Studio with a feasible single-crop design, when that
    add-on is installed. Without it this does nothing."""
    try:
        from agronaut_twin.bridge import from_sizing
    except ImportError:
        return
    if not out.feasible or len(design.crop_plan) > 1:
        st.session_state.pop("twin_studio_sized_design", None)
        return
    try:
        st.session_state["twin_studio_sized_design"] = from_sizing(design, out)
        st.caption(
            "Your tank, crop area, fish count and pump duty are ready in Digital Twin "
            "Studio (sidebar). Confirm initial fish weight and equipment transfer rates there."
        )
    except ValueError:
        # Sizing covers scales beyond the physical workbench's envelope.
        st.session_state.pop("twin_studio_sized_design", None)
