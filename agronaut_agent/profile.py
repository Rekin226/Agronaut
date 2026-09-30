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
    "fish_count",            # head currently stocked (running system)
    "fish_avg_weight_g",     # mean weight of the stocked fish (running system)
    "system_type",           # raft | nft | media_bed | vertical_tower
    "climate_site",          # fetched climate slug for the twin (e.g. ouagadougou_2025)
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


# The order a consultant asks in, which is not the order the sizing tool lists its arguments.
# Place first: anyone can answer it, and it settles the water temperature (a weather lookup)
# and the climate context. Then space, then what they want to grow and raise, water last.
_ASK_ORDER = ("temperature_c", "grow_area_m2", "objective", "crop", "fish_species",
              "water_budget_lpd")


def missing_essentials(goal: str | None, profile: dict) -> list[str]:
    """Essential keys for `goal` that are still blank in `profile`, in the order to ask them.
    Empty list when the goal is unknown or has no hard essentials (e.g. troubleshoot)."""
    essentials = GOAL_ESSENTIALS.get((goal or "").strip().lower(), ())
    ordered = sorted(essentials, key=lambda k: _ASK_ORDER.index(k) if k in _ASK_ORDER else 99)
    return [k for k in ordered if not str(profile.get(k, "")).strip()]


# Friendly labels (with units) for recall rendering.
_LABELS: dict[str, str] = {
    "system_stage": "stage",
    "location": "location",
    "fish_species": "fish",
    "crop": "crop",
    "grow_area_m2": "grow area",
    "tank_volume_l": "tank (L)",
    "fish_count": "fish (head)",
    "fish_avg_weight_g": "avg fish weight (g)",
    "system_type": "growing method",
    "climate_site": "climate site",
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
_SYSTEM_ORDER = ("system_stage", "location", "fish_species", "crop", "system_type",
                 "grow_area_m2", "tank_volume_l", "fish_count", "fish_avg_weight_g",
                 "water_budget_lpd", "water_source", "climate_site")
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
    "simulate_season": ("fish_species", "crop", "grow_area_m2", "system_type"),
    "design_system_3d": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                         "water_budget_lpd", "system_type"),
    "estimate_system_cost": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                             "water_budget_lpd", "system_type"),
    "design_full_system": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                           "water_budget_lpd", "system_type"),
}
# Substrings that mark a tool result as a non-success — never persist args from these.
_TOOL_FAILURE_MARKERS = ("VALIDATION_FAILED", "TOOL_ERROR", "Unknown objective", "Unknown tool",
                         "Unknown species", "No climate file", "Unknown region",
                         "No price book")


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


# --- session-mode (/design, /optimize, /troubleshoot) prompt vocabulary -------------
GOAL_HEADERS: dict[str, str] = {
    "design": "🌱 Design mode",
    "optimize": "⚖️ Optimize mode",
    "troubleshoot": "🔧 Troubleshoot mode",
}

# One question at a time, in plain words. A beginner cannot answer "grow area (m²)" or
# "daily water budget", but can say "a small yard" or "borehole, never runs short", and the
# model turns that into a value it confirms back. Experts get the terse form.
_QUESTIONS: dict[str, tuple[str, str]] = {
    "fish_species": ("Which fish would you like to raise? If you're not sure, tilapia is the "
                     "easy choice in warm places, and clarias (African catfish) is very hardy.",
                     "Which fish species?"),
    "crop": ("What would you like to grow? Leafy greens like lettuce or amaranth are the "
             "easiest place to start.",
             "Which crop?"),
    "grow_area_m2": ("How much space do you have for the plants? A rough idea is fine: a "
                     "balcony, a small yard, or a bigger plot.",
                     "Planted area in m²?"),
    "temperature_c": ("Where are you based? I'll use your local weather to work out the "
                      "water temperature.",
                      "Mean water temperature (°C)?"),
    "water_budget_lpd": ("Where will the water come from (tap, well, borehole, rain) and does "
                         "it ever run short?",
                         "Makeup water available per day (L)?"),
    "objective": ("What matters most to you: the most vegetables, the most fish, or using as "
                  "little water as possible?",
                  "Objective: food, protein or water_efficiency?"),
}

# How the model turns a plain answer into the canonical value. Shown only with the question
# it belongs to, so the recall block stays short.
_MAPPING_HINTS: dict[str, str] = {
    "grow_area_m2": "If they describe a space, propose a rough size in m² and confirm it "
                    "with them before saving it.",
    "temperature_c": "Once you know the town, call fetch_site_climate and use the mean "
                     "temperature it reports; tell them you did.",
    "water_budget_lpd": "Never ask for litres. Unless water is clearly scarce, size with "
                        "water treated as not limiting and say so; then tell them the daily "
                        "top-up the design needs and ask whether their source covers it, dry "
                        "months included.",
    "objective": "Map their answer to food, protein or water_efficiency.",
}

_OPENERS: dict[str, str] = {
    "design": "Let's plan it together, one step at a time. ",
    "optimize": "Let's find the best balance for your system. ",
}

GOAL_PROMPTS: dict[str, str] = {
    "design": _OPENERS["design"] + _QUESTIONS["temperature_c"][0],
    "optimize": _OPENERS["optimize"] + _QUESTIONS["grow_area_m2"][0],
    "troubleshoot": "What's going wrong? Tell me what you're seeing, like sick fish, "
                    "yellow leaves or cloudy water.",
}


def _is_expert(facts: dict) -> bool:
    return str(facts.get("experience_level", "")).strip().lower() == "expert"


def _temperature_is_lookup(key: str, facts: dict) -> bool:
    return key == "temperature_c" and bool(str(facts.get("location", "")).strip())


def next_question(goal: str | None, facts: dict) -> str | None:
    """The ONE question to ask next for `goal`, phrased for this user, or None when every
    essential is known (or the goal has no hard essentials, like troubleshoot).

    The consultation asks one thing per message. Choosing that thing here, rather than
    handing the model a list, is what keeps a small model from asking all of them at once.
    """
    missing = missing_essentials(goal, facts)
    if not missing:
        return None
    key = missing[0]
    if _temperature_is_lookup(key, facts):
        # The place is known, so the temperature is a lookup, not a question.
        return (f"Call fetch_site_climate for {facts['location']} and use the mean "
                "temperature it reports, rather than asking; tell them you did.")
    beginner_q, expert_q = _QUESTIONS[key]
    return expert_q if _is_expert(facts) else beginner_q


def next_question_note(goal: str | None, facts: dict) -> str:
    """The recall-block line that steers the next turn, or "" when nothing is missing."""
    q = next_question(goal, facts)
    if q is None:
        return ""
    key = missing_essentials(goal, facts)[0]
    if _temperature_is_lookup(key, facts):
        return f"NEXT STEP: {q}"
    hint = _MAPPING_HINTS.get(key, "")
    note = f"ASK NEXT (only this one question, in your own words and the user's language): {q}"
    return note + (f"\n{hint}" if hint else "")


def essentials_hint(goal: str, facts: dict) -> str:
    """What to say when a goal is picked with /design, /optimize or /troubleshoot.

    troubleshoot -> the symptom prompt (no hard slots). design/optimize -> a friendly opener
    and the first question when nothing is known, the next single question when partly
    known, or a 'ready' line when every essential is present."""
    g = (goal or "").strip().lower()
    if g == "troubleshoot":
        return GOAL_PROMPTS["troubleshoot"]
    missing = missing_essentials(g, facts)
    ready = "I've got what I need. Want me to run the numbers? Just say go."
    if _temperature_is_lookup(missing[0] if missing else "", facts):
        missing = missing[1:]                      # a lookup the model does, not a question
    if not missing:
        return ready
    if len(missing) == len(GOAL_ESSENTIALS.get(g, ())):
        return GOAL_PROMPTS[g]
    return _QUESTIONS[missing[0]][1 if _is_expert(facts) else 0]
