"""One computation behind both the Telegram prose and the dashboard.

`/forecast` renders words; the dashboard renders charts. They must never be two
computations — a farmer reading a chart and a farmer reading the bot are looking at the
same pond. Everything either surface needs is produced here once, structured, and
formatted downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TwinSnapshot:
    """The live twin as numbers. `ready` is False for the normal first-run state where the
    profile is still incomplete — `missing` then names exactly what to collect."""

    ready: bool
    missing: list[str] = field(default_factory=list)
    state: object | None = None            # ProductionState as of today
    trajectory: list = field(default_factory=list)   # list[ProductionDay], the forecast
    summary: object | None = None          # ProductionSummary over the forecast
    notes: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)  # past logged readings vs model


def compute(mem, user_id: str, days: int = 7, greenhouse: str = "poly",
            readings=None) -> TwinSnapshot:
    """Advance the stored twin to today and project forward, returning structure.

    Raises nothing for an incomplete profile — that is a state, not an error.
    """
    from agronaut_agent import tools as T

    facts = mem.get_facts(user_id) or {}
    missing = T._mirror_context(facts)
    if missing:
        return TwinSnapshot(ready=False, missing=list(missing))

    state, fc_run, notes = T._advance_mirror(
        mem, user_id, facts, greenhouse=greenhouse,
        forecast_days=max(2, min(int(days), 15)))

    return TwinSnapshot(
        ready=True,
        state=state,
        trajectory=list(fc_run.trajectory) if fc_run is not None else [],
        summary=fc_run.summary if fc_run is not None else None,
        notes=list(notes),
        history=readings.history(user_id) if readings is not None else [],
    )


# What a DRAWING of the system needs beyond what the twin itself needs. The mirror runs on
# the operator's own counts and volumes; the geometry has to be sized, and sizing goes
# through `validate_design_input` like every other number in this project.
SCENE_NEEDS = ("water_budget_lpd", "system_type")


def missing_for_scene(facts: dict) -> list[str]:
    """Profile fields the 3D view needs on top of the twin's own — ask, never assume."""
    return [k for k in SCENE_NEEDS if not str((facts or {}).get(k, "")).strip()]


def scene_for(snapshot, facts: dict) -> dict:
    """Bind this operator's live twin to a drawing of their system — the join #118 is about.

    Two different kinds of thing meet here, and the scene says which is which. The GEOMETRY
    is sized from their stated grow area and system type, so it is a proposed arrangement
    like every other layout this project draws. The STATE is theirs: the persisted mirror,
    advanced through the weather that actually happened, and pulled toward the readings they
    logged. Confusing the two would be the worst outcome of this view, so the subtitle names
    the operator's own numbers and `twin.geometry_note` says the drawing is still a proposal.

    Raises KeyError/ValueError for an unusable profile; the caller turns that into a
    question for the operator rather than a guess.
    """
    from datetime import date, timedelta

    from aqua_model.layout import plan_layout
    from aqua_model.scene3d import to_scene
    from aqua_model.sizing import size_system
    from aqua_model.validate import validate_design_input

    state = snapshot.state
    species_key = str(facts["fish_species"]).strip().lower()
    crop_key = str(facts["crop"]).strip().lower()
    system_type = str(facts.get("system_type", "raft")).strip().lower() or "raft"
    # Their water, not a default: the twin has been carrying the real temperature.
    temp_c = float(facts.get("temperature_c") or state.water_temp_c)

    design = validate_design_input(
        fish_species=species_key, crop=crop_key,
        grow_area_m2=float(facts["grow_area_m2"]), temperature_c=temp_c,
        water_budget_lpd=float(facts["water_budget_lpd"]), system_type=system_type)
    out = size_system(design)
    layout = plan_layout(out, crop_label=crop_key, species_label=species_key)

    # The forecast run starts today and steps one day at a time (see tools._advance_mirror),
    # so the calendar is exactly today onward — derived, not fetched a second time.
    today = date.today()
    dates = [(today + timedelta(days=i)).isoformat()
             for i in range(len(snapshot.trajectory))]

    theirs_l = float(facts.get("tank_volume_l") or 0.0)
    sized_l = float(out.system_volume_l or 0.0)
    subtitle = (f"{state.fish.count} fish @ {state.fish.mean_weight_g:.0f} g "
                f"({state.fish.biomass_kg():.1f} kg) · {float(facts['grow_area_m2']):.0f} m² "
                f"of {crop_key} · {theirs_l:,.0f} L")
    if theirs_l > 0 and sized_l > 0 and abs(sized_l - theirs_l) / theirs_l > 0.25:
        # Worth saying out loud rather than drawing over: their tank and the tank this grow
        # area implies disagree, and which one is wrong is a conversation, not a rounding.
        subtitle += (f" — drawn at the {sized_l:,.0f} L this grow area would need, "
                     f"which is not the {theirs_l:,.0f} L you told me you have")

    return to_scene(
        layout, out, crop=crop_key, species=species_key,
        name=f"Your system — {species_key} + {crop_key}",
        subtitle=subtitle,
        state=state, trajectory=list(snapshot.trajectory), dates=dates,
        today_index=0, as_of=today.isoformat(),
    )
