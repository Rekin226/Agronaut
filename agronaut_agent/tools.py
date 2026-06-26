"""Agronaut's LLM-callable tools — thin wrappers over the deterministic `aqua_model`
core plus knowledge retrieval. Each tool returns a STRING (serialized result) so the
agent loop is model-agnostic and every result stays auditable.

The trust boundary is preserved: `size_aquaponics_system` routes through
`validate_design_input` (the only door into the model), so a hallucinated argument is
rejected loudly instead of producing a confidently-wrong design.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from aqua_model import (
    size_system,
    optimize,
    OptimizeInput,
    validate_design_input,
    ValidationError,
    OBJECTIVES,
)
from aqua_model.species import SPECIES, get_species
from aqua_model.crops import CROPS
from aqua_model import datasets, report

from . import profile as profile_mod, rag, runtime, serialize

log = logging.getLogger(__name__)


def _clean_optional(text: str | None) -> str | None:
    """LLMs often pass the literal string 'null'/'none'/'' for an absent optional arg.
    Coerce those back to None so they don't become a bogus note."""
    if text is None:
        return None
    t = str(text).strip()
    return None if t.lower() in {"", "null", "none", "n/a"} else t


@tool
def size_aquaponics_system(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    source_water_note: str | None = None,
) -> str:
    """Size ONE aquaponics system deterministically from fixed inputs. Returns tank,
    biofilter and pump sizing, fish count/biomass/feed, bill of materials, operating
    envelope, the nitrogen consistency check, the CITED coefficients used, and what is
    NOT modeled. Use this for any sizing question — never state sizing numbers yourself.

    fish_species: one of tilapia, clarias, channel_catfish, trout.
    crop: one of lettuce, basil, tomato.
    grow_area_m2: planted raft/DWC area (the anchor).
    temperature_c: mean water temperature.
    water_budget_lpd: makeup water available per day, litres.
    source_water_note: optional salinity/quality caveat.
    """
    try:
        design = validate_design_input(
            fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd,
            _clean_optional(source_water_note),
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    return serialize.serialize_design_output(size_system(design))


@tool
def optimize_fish_crop_ratio(
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    objective: str = "water_efficiency",
) -> str:
    """Search fish species x crop-area allocations for the best ratio under a goal, by
    bounded enumeration. Returns the best ratio, ranked alternatives, and improvement vs
    a naive even split. objective: one of food, protein, water_efficiency.
    """
    obj = (objective or "water_efficiency").strip().lower()
    if obj not in OBJECTIVES:
        return f"Unknown objective {objective!r}. Use one of: {', '.join(OBJECTIVES)}."
    res = optimize(
        OptimizeInput(
            grow_area_m2=grow_area_m2,
            temperature_c=temperature_c,
            water_budget_lpd=water_budget_lpd,
            objective=obj,
        )
    )
    return serialize.serialize_optimize_result(res)


@tool
def list_supported_species_and_crops() -> str:
    """List the fish species, crops, and optimization objectives Agronaut supports. Call
    this before sizing if unsure whether something the user named is supported."""
    fish = ", ".join(sorted(SPECIES))
    crops = ", ".join(sorted(CROPS))
    objs = ", ".join(OBJECTIVES)
    return f"Fish species: {fish}\nCrops: {crops}\nOptimization objectives: {objs}"


@tool
def design_envelope_reality_check(model_envelope: dict) -> str:
    """Compare a computed operating envelope (the 'operating_envelope' dict from a prior
    size_aquaponics_system result) against empirical field-pond data, if available, and
    report where the design's target bands agree with or diverge from real ponds."""
    result = datasets.envelope_reality_check(model_envelope)
    if result is None:
        return ("No empirical dataset available for cross-check "
                "(raw pond data not fetched). Report the design envelope as-is.")
    return str(result)


@tool
def render_design_report(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    site: str | None = None,
) -> str:
    """Render a full Markdown build report (BOM, envelope, maintenance, cited coefficients,
    not-modeled) for a system. Use when the user wants the complete writeup or a shareable
    document. Same inputs as size_aquaponics_system."""
    try:
        design = validate_design_input(
            fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    out = size_system(design)
    return report.to_markdown(design, out, site=site)


@tool
def search_knowledge_base(query: str) -> str:
    """Retrieve passages from Agronaut's curated aquaponics knowledge (local docs + cited
    sources) for qualitative troubleshooting and husbandry guidance (symptoms, water
    quality, pests). Use for explanation — NOT for sizing numbers (use the sizing tool)."""
    return rag.search(query)


@tool
def remember_about_user(note: str, category: str = "profile") -> str:
    """Save a durable note about THIS user's system or history so you recall it in future
    conversations. Use when you learn something lasting: their tank size/location/setup
    (category 'profile'), something that happened ('event', e.g. 'had an ammonia spike in
    June, fixed with a 30% water change'), how they like answers ('preference'), or a fix
    that worked for them ('learning'). Keep each note one short sentence. Do NOT save
    transient chit-chat or anything the user asked you to forget."""
    cur = runtime.get_current()
    if cur is None:
        return "Memory unavailable right now."
    mem, user_id = cur
    saved = mem.add_memory(user_id, note, category)
    return "Noted — I'll remember that." if saved else "Already in my memory."


@tool
def update_profile(updates: dict) -> str:
    """Save typed facts about THIS user's system to their profile so you recall and reuse
    them across the conversation and future sessions. Pass a dict of canonical fields you
    have learned, e.g. {"goal": "optimize", "objective": "protein", "grow_area_m2": 10,
    "tank_volume_l": 1000, "dissolved_oxygen_mgl": 5.5}. Canonical keys: system_stage,
    fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd, ph, tank_volume_l,
    dissolved_oxygen_mgl, ammonia_mgl, water_source, location, goal, goal_detail,
    objective, experience_level. Unknown keys are ignored. Call this whenever the user
    reveals a durable fact — do not wait for the end of the conversation."""
    cur = runtime.get_current()
    if cur is None:
        return "Profile unavailable right now."
    mem, user_id = cur
    updates = updates or {}
    accepted = {k: v for k, v in updates.items()
                if k in profile_mod.PROFILE_KEYS and str(v).strip() not in ("", "None")}
    rejected = [k for k in updates if k not in profile_mod.PROFILE_KEYS]
    if rejected:
        log.debug("update_profile dropped unknown keys: %s", rejected)
    if not accepted:
        return "No recognized profile fields to save."
    mem.set_facts(user_id, accepted, source="user_stated")
    return "Saved to your profile: " + ", ".join(f"{k}={v}" for k, v in accepted.items())


AGRONAUT_TOOLS = [
    size_aquaponics_system,
    optimize_fish_crop_ratio,
    list_supported_species_and_crops,
    design_envelope_reality_check,
    render_design_report,
    search_knowledge_base,
    remember_about_user,
    update_profile,
]
