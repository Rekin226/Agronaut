"""Published reference aquaponics systems — the open-data acceptance gate.

The trust gate (`tests/test_calibration.py`) was meant to reproduce the founder's own running
system. With no private system available, the model is validated against fully documented
systems from the peer-reviewed literature instead: it must reproduce each system's daily feed
input (= plant area x feeding-rate ratio) within tolerance.

The primary reference is the UVI commercial system (Rakocy et al. 2004; Rakocy 1988) — the most
completely documented small-scale aquaponic system in the literature. NOTE it is also where
several seed coefficients come from (lettuce/basil FRR), so it validates the model's machinery
end-to-end rather than being a fully blind check; each system's `independent` flag records that.
A genuinely independent or private system can be added later by appending to
`data/reference_systems.json` — no code change needed.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = _REPO_ROOT / "data" / "reference_systems.json"

FEED_TOLERANCE = 0.15  # +/-15% on daily feed, matching the original calibration gate


def load() -> list[dict]:
    """Load the curated reference systems (raises if the data file is missing)."""
    return json.loads(DATA.read_text())["systems"]


def check(system: dict) -> dict:
    """Run the model against one reference system and report how closely it reproduces feed."""
    from .sizing import size_system
    from .validate import validate_design_input

    di = validate_design_input(
        fish_species=system["fish_species"],
        crop=system["crop"],
        grow_area_m2=system["grow_area_m2"],
        temperature_c=system["temperature_c"],
        water_budget_lpd=system["water_budget_lpd"],
    )
    out = size_system(di)
    measured = system["measured_feed_g_per_day"]
    feed_error = abs(out.feed_g_per_day - measured) / measured
    return {
        "id": system["id"],
        "model_feed_g_per_day": out.feed_g_per_day,
        "measured_feed_g_per_day": measured,
        "feed_error": feed_error,
        "within_tolerance": feed_error <= FEED_TOLERANCE,
        "feasible": out.feasible,
        "warnings": list(out.warnings),
    }
