"""Streamlit view of the LIVE twin — the same computation `/forecast` speaks as prose.

No LLM is reached from this file. The twin is deterministic by design (that is the whole
point of the command layer), so the dashboard reads `AgronautAgent.twin_snapshot()` and
renders numbers. If a farmer's profile is not yet complete, this view offers the FORM that
starts their twin — the bot's own gate used to answer with instructions addressed to a
model ("call fetch_site_climate"), which no operator can act on.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# The three numbers that separate "a design I sketched" from "the pond I actually run".
_START_FIELDS = ("tank_volume_l", "fish_count", "fish_avg_weight_g")

_SERIES_LABELS = {
    "ammonia_mg_l": "ammonia (NH3/TAN)",
    "nitrite_mg_l": "nitrite (NO2)",
    "nitrate_mg_l": "nitrate (NO3)",
    "water_temp_c": "water temperature",
    "fish_avg_weight_g": "average fish weight",
    "fish_count": "fish count",
}


def render_twin(brain=None, user: str | None = None, channel: str = "web") -> None:
    """Render the My Twin tab. `brain` is an AgronautAgent; `user` the channel identity."""
    st.subheader("My Twin")
    st.caption(
        "Your actual system, mirrored. Deterministic — the same model behind /log and "
        "/forecast on Telegram, with no AI in the path."
    )

    if brain is None or user is None:
        st.info("The twin needs a session. Reload the page.")
        return

    snap = brain.twin_snapshot(channel, user, days=7, greenhouse=_greenhouse())

    if not snap.ready:
        _render_start_form(brain, user, channel, snap)
        return

    _render_state(snap)
    _render_forecast(snap)
    _render_history(snap)
    _render_log_form(brain, user, channel)


def _greenhouse() -> str:
    return st.sidebar.selectbox(
        "Cover", ("shade", "poly", "heated"), index=0,
        help="The envelope you actually run. It changes water temperature, and "
             "temperature usually decides the harvest.",
    )


def _render_start_form(brain, user, channel, snap) -> None:
    """The bootstrap. Everything here writes straight to the profile — no model involved."""
    uid = brain._conv.get_or_create_user(channel, user)
    facts = brain._mem.get_facts(uid) or {}
    blocked = [m for m in snap.missing if not any(f in m for f in _START_FIELDS)]

    st.info(
        "Your twin hasn't started yet. Tell it what you're actually running and it will "
        "begin mirroring today."
    )

    with st.form("twin_start"):
        col1, col2, col3 = st.columns(3)
        with col1:
            tank = st.number_input(
                "Tank volume (L)", min_value=50.0, max_value=1_000_000.0,
                value=float(facts.get("tank_volume_l") or 1000.0), step=100.0,
                key="twin_tank_l")
        with col2:
            count = st.number_input(
                "Number of fish", min_value=1, max_value=1_000_000,
                value=int(float(facts.get("fish_count") or 50)), step=1,
                key="twin_fish_count")
        with col3:
            weight = st.number_input(
                "Average fish weight (g)", min_value=0.1, max_value=50_000.0,
                value=float(facts.get("fish_avg_weight_g") or 100.0), step=10.0,
                key="twin_fish_weight_g")
        started = st.form_submit_button("Start my twin")

    if started:
        brain._mem.set_facts(uid, {"tank_volume_l": float(tank),
                                   "fish_count": int(count),
                                   "fish_avg_weight_g": float(weight)},
                             source="user_stated")
        st.success("Saved. Your twin starts from these numbers.")
        _rerun()

    if blocked:
        st.warning(
            "Still needed before the twin can run: " + ", ".join(blocked) +
            ". The species, crop, growing area and site come from a design "
            "conversation — set them in the Assistant tab, then come back."
        )


def _render_state(snap) -> None:
    if snap.notes:
        st.caption(" · ".join(snap.notes))
    state = snap.state
    biomass = state.fish.count * state.fish.mean_weight_g / 1000.0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fish", f"{state.fish.count} @ {state.fish.mean_weight_g:.0f} g")
    c2.metric("Biomass", f"{biomass:.1f} kg")
    c3.metric("Water", f"{state.water_temp_c:.1f} °C")
    c4.metric("Nitrate", f"{state.nitrogen.no3_mg_l:.0f} mg/L")

    c5, c6, c7 = st.columns(3)
    c5.metric("Ammonia (TAN)", f"{state.nitrogen.tan_mg_l:.2f} mg/L")
    c6.metric("Nitrite", f"{state.nitrogen.no2_mg_l:.2f} mg/L")
    c7.metric("Harvested", f"{state.harvested_fish_kg:.1f} kg fish · "
                           f"{state.harvested_crop_kg:.1f} kg crop")


def _render_forecast(snap) -> None:
    if snap.summary is None or not snap.trajectory:
        st.info("No forecast available for this site right now.")
        return
    s = snap.summary
    st.markdown(f"**Next {s.days} days** — most limiting: **{s.limiting_factor}**")

    rows = []
    for i, day in enumerate(snap.trajectory, start=1):
        rows.append({
            "day": i,
            "ammonia (NH3/TAN)": day.state.nitrogen.tan_mg_l,
            "nitrite (NO2)": day.state.nitrogen.no2_mg_l,
            "nitrate (NO3)": day.state.nitrogen.no3_mg_l,
            "water temperature": day.state.water_temp_c,
        })
    frame = pd.DataFrame(rows).set_index("day")

    st.line_chart(frame[["ammonia (NH3/TAN)", "nitrite (NO2)", "nitrate (NO3)"]],
                  height=240)
    st.line_chart(frame[["water temperature"]], height=180)

    c1, c2, c3 = st.columns(3)
    c1.metric("Fish harvested", f"{s.fish_harvested_kg:.1f} kg",
              help=f"{s.fish_standing_kg:.1f} kg still standing in the tank")
    c2.metric("Crop harvested", f"{s.crop_harvested_kg:.1f} kg")
    c3.metric("Feed used", f"{s.feed_used_kg:.1f} kg",
              help=f"realized FCR {s.realized_fcr:.2f}")

    for w in s.warnings or ():
        st.warning(w)
    if s.not_modelled:
        with st.expander("What this model does NOT include"):
            st.caption(s.not_modelled if isinstance(s.not_modelled, str)
                       else "; ".join(s.not_modelled))
    st.caption(
        "A projection from a model calibrated on literature seeds, not on your system. "
        "The relative comparisons are the useful part, not the absolute kilograms."
    )


def _render_history(snap) -> None:
    """Logged readings against what the twin believed — the drift that makes it a twin."""
    if not snap.history:
        st.caption("No readings logged yet. Log one below and the twin starts learning "
                   "how far off it is.")
        return

    st.markdown("**Your readings vs the twin**")
    rows = []
    for entry in snap.history:
        obs, mod = entry["observed"], entry["modelled"]
        for key, value in obs.items():
            rows.append({
                "when": entry["recorded_at"],
                "reading": _SERIES_LABELS.get(key, key),
                "you measured": value,
                "twin thought": mod.get(key),
                "drift": None if mod.get(key) is None else round(value - mod[key], 2),
            })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_log_form(brain, user, channel) -> None:
    with st.expander("Log a reading"):
        with st.form("twin_log"):
            c1, c2, c3 = st.columns(3)
            with c1:
                ammonia = st.number_input("Ammonia (mg/L)", min_value=0.0, max_value=100.0,
                                          value=0.0, step=0.1, key="log_ammonia")
            with c2:
                nitrite = st.number_input("Nitrite (mg/L)", min_value=0.0, max_value=100.0,
                                          value=0.0, step=0.1, key="log_nitrite")
            with c3:
                nitrate = st.number_input("Nitrate (mg/L)", min_value=0.0, max_value=1000.0,
                                          value=0.0, step=1.0, key="log_nitrate")
            temp = st.number_input("Water temperature (°C)", min_value=0.0, max_value=60.0,
                                   value=0.0, step=0.5, key="log_temp",
                                   help="Leave at 0 for any reading you did not take.")
            submitted = st.form_submit_button("Log it")

        if submitted:
            args = {k: v for k, v in (("ammonia_mg_l", ammonia), ("nitrite_mg_l", nitrite),
                                      ("nitrate_mg_l", nitrate), ("water_temp_c", temp))
                    if v}
            if not args:
                st.warning("Enter at least one reading.")
                return
            st.success(brain.log_readings_direct(channel, user, args))
            _rerun()


def _rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
