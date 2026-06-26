"""The System Profile — a typed view over the user's aquaponics system and current
consultation goal. Stored as canonical keys in the existing `user_facts` table; this
module owns the vocabulary, the goal->essentials map, the 'what's still missing'
steering helper, and the recall rendering.
"""

from __future__ import annotations

# Canonical profile fields. update_profile accepts only these keys.
PROFILE_KEYS: tuple[str, ...] = (
    "system_stage",          # planning | building | running
    "fish_species",
    "crop",
    "grow_area_m2",
    "temperature_c",
    "water_budget_lpd",
    "ph",
    "tank_volume_l",         # actual tank on a running system (input, not computed)
    "dissolved_oxygen_mgl",
    "ammonia_mgl",
    "water_source",
    "location",
    "goal",                  # design | optimize | troubleshoot
    "goal_detail",
    "objective",             # food | protein | water_efficiency
    "experience_level",      # beginner | intermediate | expert
)

GOALS: tuple[str, ...] = ("design", "optimize", "troubleshoot")

# Essentials required before a first-cut recommendation, per goal. troubleshoot is
# judgment-based (no hard slots) — the prompt drives it from symptoms + water params.
GOAL_ESSENTIALS: dict[str, tuple[str, ...]] = {
    "design": ("fish_species", "crop", "grow_area_m2", "temperature_c", "water_budget_lpd"),
    "optimize": ("grow_area_m2", "temperature_c", "water_budget_lpd", "objective"),
    "troubleshoot": (),
}


def missing_essentials(goal: str | None, profile: dict) -> list[str]:
    """Essential keys for `goal` that are still blank in `profile`. Empty list when the
    goal is unknown or has no hard essentials (e.g. troubleshoot)."""
    essentials = GOAL_ESSENTIALS.get((goal or "").strip().lower(), ())
    return [k for k in essentials if not str(profile.get(k, "")).strip()]
