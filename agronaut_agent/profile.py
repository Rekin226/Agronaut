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


# Friendly labels (with units) for recall rendering.
_LABELS: dict[str, str] = {
    "system_stage": "stage",
    "location": "location",
    "fish_species": "fish",
    "crop": "crop",
    "grow_area_m2": "grow area",
    "tank_volume_l": "tank (L)",
    "temperature_c": "temp (°C)",
    "ph": "pH",
    "dissolved_oxygen_mgl": "DO (mg/L)",
    "ammonia_mgl": "ammonia (mg/L)",
    "water_budget_lpd": "water budget (L/day)",
    "water_source": "water source",
    "goal": "goal",
    "goal_detail": "goal detail",
    "objective": "objective",
    "experience_level": "experience",
}

# Default display order: system spec first, then water params, then goal.
_SYSTEM_ORDER = ("system_stage", "location", "fish_species", "crop", "grow_area_m2",
                 "tank_volume_l", "water_budget_lpd", "water_source")
_WATER_ORDER = ("temperature_c", "ph", "dissolved_oxygen_mgl", "ammonia_mgl")
_GOAL_ORDER = ("goal", "goal_detail", "objective", "experience_level")


def render_profile(profile: dict, goal: str | None = None) -> str:
    """Compact, goal-aware recall block. Empty string when nothing is known."""
    if (goal or "").strip().lower() == "troubleshoot":
        order = _WATER_ORDER + _SYSTEM_ORDER + _GOAL_ORDER
    else:
        order = _SYSTEM_ORDER + _WATER_ORDER + _GOAL_ORDER

    lines = []
    for key in order:
        val = str(profile.get(key, "")).strip()
        if val:
            lines.append(f"• {_LABELS[key]}: {val}")
    if not lines:
        return ""
    return "YOUR SYSTEM (what I remember)\n" + "\n".join(lines)


# Tools whose validated arguments ARE the user's system facts. The args map 1:1 to
# canonical profile keys, so a successful call deterministically fills the profile.
_TOOL_PROFILE_ARGS: dict[str, tuple[str, ...]] = {
    "size_aquaponics_system": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                               "water_budget_lpd"),
    "render_design_report": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                             "water_budget_lpd"),
    "optimize_fish_crop_ratio": ("grow_area_m2", "temperature_c", "water_budget_lpd",
                                 "objective"),
}
# Substrings that mark a tool result as a non-success — never persist args from these.
_TOOL_FAILURE_MARKERS = ("VALIDATION_FAILED", "TOOL_ERROR", "Unknown objective", "Unknown tool")


def profile_updates_from_tool(name: str, args: dict, result: str) -> dict:
    """Profile facts to persist from a successful fact-carrying tool call. Empty dict for
    non-fact tools or when the result shows a failure marker (so bad/ rejected inputs are
    never remembered)."""
    keys = _TOOL_PROFILE_ARGS.get(name)
    if not keys:
        return {}
    if any(marker in (result or "") for marker in _TOOL_FAILURE_MARKERS):
        return {}
    args = args or {}
    return {k: args[k] for k in keys
            if k in args and str(args[k]).strip() not in ("", "None")}
