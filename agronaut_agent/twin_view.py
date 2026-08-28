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
