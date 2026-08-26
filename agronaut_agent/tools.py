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
    size_hydroponic_system,
    optimize,
    OptimizeInput,
    validate_design_input,
    validate_hydroponic_input,
    ValidationError,
    OBJECTIVES,
)
from aqua_model.species import SPECIES, get_species
from aqua_model.crops import CROPS
from aqua_model import datasets, report

from . import profile as profile_mod, rag, runtime, serialize

log = logging.getLogger(__name__)


def _calibration_note(user_id, species=None, crop=None) -> str:
    """A one-line-per-coefficient note of which coefficients were calibrated from the operator's
    own measurements. Empty string if none applied. When species/crop are given, only coefficients
    whose key prefix matches the design's species or crop are included."""
    cal = runtime.get_calibration()
    if cal is None:
        return ""
    applied = [r for r in cal.calibration_report(user_id) if r.get("applied")]
    if species is not None or crop is not None:
        scope = {str(species).strip().lower(), str(crop).strip().lower()}
        applied = [r for r in applied if r["coefficient"].rpartition(".")[0] in scope]
    if not applied:
        return ""
    lines = "\n".join(
        f"- {r['coefficient']}: {r['mean']} — calibrated from your {r['n']} measurements "
        f"(literature seed {r['seed']})"
        for r in applied
    )
    return "\n\nCalibrated from YOUR data (bounded to the published range):\n" + lines


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
    system_type: str = "raft",
) -> str:
    """Size ONE aquaponics system deterministically from fixed inputs. Returns tank,
    biofilter and pump sizing, fish count/biomass/feed, bill of materials, operating
    envelope, the nitrogen consistency check, the CITED coefficients used, and what is
    NOT modeled. Use this for any sizing question — never state sizing numbers yourself.

    fish_species: one of tilapia, clarias, channel_catfish, trout, carp.
    crop: one of the supported crops (30+, from leafy greens and herbs to fruiting crops
        like tomato, cucumber, strawberry). Call list_supported_species_and_crops if unsure.
    grow_area_m2: planted area (the anchor).
    temperature_c: mean water temperature.
    water_budget_lpd: makeup water available per day, litres.
    source_water_note: optional salinity/quality caveat.
    system_type: the GROWING METHOD, matching the user's preference: 'raft' (deep-water
        culture, the default — forgiving, high water volume), 'nft' (nutrient film — light,
        low water, needs reliable power), 'media_bed' (flood & drain — robust, also provides
        biofiltration), or 'vertical_tower' (stacked — packs ~3x the growing area onto the
        floor space, for land-scarce sites; best for leafy greens/herbs). Ask the user which
        they want if they have a preference.
    """
    try:
        design = validate_design_input(
            fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd,
            _clean_optional(source_water_note), system_type,
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    cur = runtime.get_current()
    overrides, note = None, ""
    if cur is not None:
        _mem, user_id = cur
        cal = runtime.get_calibration()
        if cal is not None:
            overrides = cal.overrides_for(user_id) or None
            note = _calibration_note(user_id, fish_species, crop)
    try:
        sized = serialize.serialize_design_output(size_system(design, overrides=overrides))
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    return sized + note


@tool
def size_hydroponic_system_tool(
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    source_water_note: str | None = None,
    system_type: str = "raft",
) -> str:
    """Size ONE HYDROPONIC (soil-less, NO fish) system deterministically. Use this when the
    user wants plants only — nutrients dosed as salts, not from fish. Returns the nutrient
    reservoir volume, ET-driven daily water use, pump sizing, the nutrient-solution target
    (EC band + elemental N/day + pH), bill of materials, operating envelope, the CITED
    coefficients used, and what is NOT modeled. For fish+plants use size_aquaponics_system.

    crop: one of the supported crops (call list_supported_species_and_crops if unsure).
    grow_area_m2: planted area (the anchor).
    temperature_c: mean ambient/solution temperature.
    water_budget_lpd: makeup water available per day, litres.
    source_water_note: optional salinity/quality caveat.
    system_type: growing method — 'raft' (deep-water culture, default), 'nft' (nutrient
        film — light, low water), or 'media_bed'. Match the user's preference.
    """
    try:
        design = validate_hydroponic_input(
            crop, grow_area_m2, temperature_c, water_budget_lpd,
            _clean_optional(source_water_note), system_type,
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    return serialize.serialize_hydroponic_output(size_hydroponic_system(design))


@tool
def size_mixed_bed_aquaponics(
    fish_species: str,
    crop_plan: list[dict],
    temperature_c: float,
    water_budget_lpd: float,
    source_water_note: str | None = None,
    system_type: str = "raft",
) -> str:
    """Size ONE aquaponics system whose beds grow SEVERAL crops sharing the same water (a mixed
    bed). Use this instead of size_aquaponics_system whenever the user wants more than one crop
    in one system — e.g. "lettuce and basil and a bit of tomato". Feed is summed from each
    crop's own feeding-rate ratio over its own area, and the system is sized for the total.
    Returns the same artifacts as size_aquaponics_system, plus a check that WARNS if the crops
    cannot share one water chemistry (e.g. their pH bands don't overlap) — never averages it
    away silently. Never state sizing numbers yourself; call this tool.

    fish_species: one of tilapia, clarias, channel_catfish, trout, carp.
    crop_plan: the mix, as a list of {"crop": <name>, "area_m2": <planted m2>} entries — one per
        crop. Each crop must be supported (call list_supported_species_and_crops if unsure); each
        area must be > 0. The total grow area is their sum.
    temperature_c: mean water temperature.
    water_budget_lpd: makeup water available per day, litres.
    source_water_note: optional salinity/quality caveat.
    system_type: the GROWING METHOD — 'raft' (default), 'nft', or 'media_bed' — matching the
        user's preference, exactly as in size_aquaponics_system.
    """
    try:
        design = validate_design_input(
            fish_species, None, None, temperature_c, water_budget_lpd,
            _clean_optional(source_water_note), system_type, crop_plan=crop_plan,
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    cur = runtime.get_current()
    overrides, note = None, ""
    if cur is not None:
        _mem, user_id = cur
        cal = runtime.get_calibration()
        if cal is not None:
            overrides = cal.overrides_for(user_id) or None
            note = _calibration_note(user_id, fish_species)
    try:
        sized = serialize.serialize_design_output(size_system(design, overrides=overrides))
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    return sized + note


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
    cur = runtime.get_current()
    overrides = None
    if cur is not None:
        _mem, user_id = cur
        cal = runtime.get_calibration()
        if cal is not None:
            overrides = cal.overrides_for(user_id) or None
    try:
        res = optimize(
            OptimizeInput(
                grow_area_m2=grow_area_m2,
                temperature_c=temperature_c,
                water_budget_lpd=water_budget_lpd,
                objective=obj,
            ),
            overrides=overrides,
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    note = ""
    if cur is not None:                      # `cur` is the runtime.get_current() already fetched for overrides
        _mem, user_id = cur
        note = _calibration_note(user_id)
    return serialize.serialize_optimize_result(res) + note


@tool
def list_supported_species_and_crops() -> str:
    """List the fish species, crops, growing methods, and optimization objectives Agronaut
    supports. Call this before sizing if unsure whether something the user named is supported."""
    from aqua_model.system_types import SYSTEM_TYPES
    fish = ", ".join(sorted(SPECIES))
    crops = ", ".join(sorted(CROPS))
    objs = ", ".join(OBJECTIVES)
    methods = ", ".join(f"{k} ({SYSTEM_TYPES[k].name})" for k in sorted(SYSTEM_TYPES))
    return (f"Fish species: {fish}\nCrops: {crops}\nGrowing methods: {methods}\n"
            f"Optimization objectives: {objs}")


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
def render_pilot_proposal(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    site: str,
    organization: str,
    ask_amount: float,
    currency: str = "USD",
    beneficiaries: str | None = None,
    context: str | None = None,
    duration_months: int = 12,
) -> str:
    """Render a FUNDER-READY pilot proposal (Markdown) for a grant/NGO application: the
    proposed system, the funding ask, projected annual food & water outcomes, and the data
    the install will produce for monitoring & evaluation — plus the cited design + honesty
    layer. Use when the user is preparing a proposal for a funder, NGO, or program officer.
    Needs the system inputs (as for sizing) PLUS site, organization, and ask_amount."""
    from aqua_model.pilot import PilotInfo, to_pilot_proposal
    try:
        design = validate_design_input(
            fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    pilot = PilotInfo(
        site=site, organization=organization, ask_amount=ask_amount, currency=currency,
        beneficiaries=_clean_optional(beneficiaries), context=_clean_optional(context),
        duration_months=duration_months,
    )
    return to_pilot_proposal(design, size_system(design), pilot)


@tool
def render_system_schematic(
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    fish_species: str | None = None,
    system_type: str = "raft",
) -> str:
    """DRAW a labeled diagram of the system and send it to the user as an image. Use when the
    user asks to see, draw, or picture their system, or wants a schematic/diagram. Provide
    fish_species for an AQUAPONIC system (fish + plants); omit it for a HYDROPONIC one
    (plants only). system_type is the growing method ('raft', 'nft', 'media_bed',
    'vertical_tower') — the diagram labels reflect it (towers also show the floor footprint). Same sizing inputs as the sizing tools. The image is generated
    deterministically from the sized design — you do not describe it, just call this."""
    import os
    import tempfile
    from aqua_model.schematic import to_png
    fish = _clean_optional(fish_species)
    try:
        if fish:
            design = validate_design_input(fish, crop, grow_area_m2, temperature_c,
                                           water_budget_lpd, None, system_type)
            out = size_system(design)
        else:
            design = validate_hydroponic_input(crop, grow_area_m2, temperature_c,
                                               water_budget_lpd, None, system_type)
            out = size_hydroponic_system(design)
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    fd, path = tempfile.mkstemp(prefix="agronaut_schematic_", suffix=".png")
    with os.fdopen(fd, "wb") as fh:
        fh.write(to_png(out))
    runtime.add_attachment(path)
    kind = "aquaponic" if fish else "hydroponic"
    return (f"Rendered a {kind} system schematic (attached as an image). Tell the user the "
            "diagram is on its way and offer to size or refine it further.")


@tool
def search_knowledge_base(query: str) -> str:
    """Retrieve passages from Agronaut's curated aquaponics knowledge (local docs + cited
    sources) for qualitative troubleshooting and husbandry guidance (symptoms, water
    quality, pests). Use for explanation — NOT for sizing numbers (use the sizing tool)."""
    text, stats = rag.search_with_stats(query)
    try:
        from .analytics import Analytics
        cur = runtime.get_current()
        Analytics().record("retrieval", user_id=cur[1] if cur else None, **stats)
    except Exception:  # noqa: BLE001 — telemetry must never break a live turn
        pass
    return text


@tool
def triage_visual_symptoms(description: str) -> str:
    """Turn a DESCRIPTION OF VISIBLE SYMPTOMS into a deterministic, cited differential —
    ranked candidate causes, each with the checks that would tell them apart and fish-safe
    first actions. Use whenever the user describes or photographs what they can SEE (leaf
    colour and where on the plant, root appearance, water colour, fish behaviour or marks,
    visible pests).

    Pass the symptom description in the user's own words, or the visual observation of a
    photo. Prefer this over search_knowledge_base for visible symptoms: it returns a ranked
    differential with sources rather than loose passages. It reads only qualitative features
    — it never infers pH, DO, ammonia or any measurement, and it never states a dose."""
    from agent.observation_features import extract_observation_features
    from aqua_model.triage import format_triage, triage_symptoms

    features = extract_observation_features(description or "")
    result = triage_symptoms(features)
    if result.is_empty():
        return ("VISUAL_TRIAGE: nothing diagnostic in that description. Ask which part is "
                "affected (older vs newer leaves, roots, water, fish behaviour) or for a "
                "closer photo — then call this again.")
    return format_triage(result)


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
    fish_count, fish_avg_weight_g, system_type, climate_site, dissolved_oxygen_mgl,
    ammonia_mgl, water_source, location, goal, goal_detail, objective, experience_level.
    Unknown keys are ignored. Call this whenever the user reveals a durable fact — do not
    wait for the end of the conversation. For a user with a RUNNING system, fish_count,
    fish_avg_weight_g, tank_volume_l and climate_site are what simulate_my_system needs."""
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


@tool
def schedule_followup(question: str, hours: float, about: str = "") -> str:
    """Schedule a proactive check-in with the user to learn whether your advice worked.
    Use ONLY after giving an actionable fix (e.g. a water change, a pH adjustment) — not for
    plans or trivia. `question` is what you'll ask them later (e.g. "did the 30% water change
    bring the ammonia down?"). `hours` is when to check back — pick it to match the fix (a
    water change ~24h, cycling ~a week); must be between 1 and 336 (14 days). `about` is a
    short label of the issue. Only one check-in can be pending per user."""
    cur = runtime.get_current()
    fs = runtime.get_followups()
    if cur is None or fs is None:
        return "Can't schedule a follow-up right now."
    _mem, user_id = cur
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "Follow-up delay must be a number of hours between 1 and 336."
    if not (1.0 <= h <= 336.0):
        return "Follow-up delay must be between 1 hour and 14 days (336 hours)."
    from datetime import datetime, timedelta, timezone
    channel, _, channel_user = user_id.partition(":")
    due_at = (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()
    ok = fs.schedule(user_id, channel, channel_user, question, about or "", due_at)
    return ("Got it — I'll check back on that." if ok
            else "I already have a check-in pending with you; I'll follow up on that first.")


@tool
def nominate_shared_insight(insight: str, topic: str = "") -> str:
    """Nominate a GENERALIZED, PII-STRIPPED lesson for the shared community knowledge pool so it
    can help OTHER operators — after the owner approves it. Call this when a learning you just
    recorded would help operators in general, not one person's specific system. Write `insight`
    as a single general sentence with NO personal or identifying details (no location, names, or
    specific tank IDs): e.g. "a partial (~30%) water change commonly clears an acute ammonia
    spike". `topic` is a short tag like "ammonia" or "dissolved oxygen". The owner reviews and
    approves before anything is ever shared."""
    cur = runtime.get_current()
    cs = runtime.get_community()
    if cur is None or cs is None:
        return "Can't nominate a shared insight right now."
    mem, user_id = cur
    text = (insight or "").strip()
    if not text:
        return "Nothing to nominate."
    if len(text) > 500:
        return "That insight is too long to share — summarize it in one sentence."
    learnings = [m["content"] for m in mem.get_memories(user_id) if m["category"] == "learning"]
    original = learnings[-1] if learnings else ""
    ok = cs.nominate(user_id, original, text, topic or "")
    return ("Thanks — I've nominated that for the shared knowledge base (pending the owner's "
            "review)." if ok else "That insight is already in the shared queue.")


@tool
def search_community_knowledge(query: str) -> str:
    """Search practical insights other operators contributed and the owner approved. Use during
    troubleshooting for real-world tips. These are COMMUNITY EXPERIENCE, not verified science —
    always present them as "reported by other operators", never as fact or coefficients, and
    never for sizing numbers."""
    cs = runtime.get_community()
    if cs is None:
        return "Community knowledge unavailable right now."
    hits = cs.search_approved(query)
    if not hits:
        return "No community insights yet for that — answer from your own knowledge."
    lines = "\n".join(
        f"- {h['insight']}" + (f" ({h['topic']})" if h.get("topic") else "") for h in hits
    )
    return "Reported by other operators (community experience, not verified science):\n" + lines


# metric -> (calibration-key suffix, which profile field names the species/crop)
_MEASUREMENT_METRICS = {
    "fcr": ("fcr", "fish_species"),
    "harvest_weight": ("harvest_weight", "fish_species"),
    "yield": ("yield", "crop"),
}


@tool
def record_measurement(metric: str, value: float) -> str:
    """Record a REAL measured outcome from the operator's OWN running system so their future
    sizings calibrate to reality. Call ONLY with a number the user actually measured — never an
    estimate or a model output. metric is one of: 'fcr' (feed used / weight gained),
    'harvest_weight' (kg per fish), 'yield' (kg per m² per year of the crop). The value is
    combined with their species/crop; once you have >=2 in-range measurements it calibrates
    their sizing."""
    cur = runtime.get_current()
    cal = runtime.get_calibration()
    if cur is None or cal is None:
        return "Can't record a measurement right now."
    mem, user_id = cur
    m = (metric or "").strip().lower()
    if m not in _MEASUREMENT_METRICS:
        return f"Unknown metric {metric!r}. Use one of: fcr, harvest_weight, yield."
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "The measurement must be a number."
    if not (0 < v < 100000):
        return "That measurement doesn't look like a real number — please double-check it."
    suffix, profile_field = _MEASUREMENT_METRICS[m]
    subject = mem.get_facts(user_id).get(profile_field)
    if not subject:
        need = "fish species" if profile_field == "fish_species" else "crop"
        return f"Tell me your {need} first so I can record that measurement."
    coefficient = f"{str(subject).strip().lower()}.{suffix}"
    cal.record(user_id, coefficient, v)
    from aqua_model import calibration as _calibration
    try:
        _calibration.get(coefficient)
    except KeyError:
        # Stored but inert: overrides_for skips coefficients without a published empirical
        # range. Say so — the confident wording here would be a lie.
        return (f"Stored your {coefficient} measurement, but I can't calibrate with it yet — "
                f"no published range on file for that metric. I'll keep it in case coverage "
                f"is added later.")
    return f"Recorded — I'll use your measurements to calibrate future sizings ({coefficient})."


def _climate_days(site: str):
    """Resolve a site slug to a parsed climate series, or raise with a teaching message."""
    import json
    from pathlib import Path

    from aqua_model.climate import from_records

    clim_dir = Path(__file__).resolve().parent.parent / "data" / "climate"
    path = clim_dir / f"{str(site).strip().lower()}.json"
    if not path.exists():
        have = sorted(p.stem for p in clim_dir.glob("*.json"))
        raise FileNotFoundError(
            f"No climate file for site '{site}'. Available: {', '.join(have) or 'none'}. "
            f"Fetch one first (no API key needed): "
            f"python scripts/fetch_climate.py --lat <LAT> --lon <LON> --name {site}")
    payload = json.loads(path.read_text())
    return from_records(payload["days"]), payload.get("site", {})


def _greenhouse_from(mode: str, heat_setpoint_c: float | None):
    from aqua_model.climate import GreenhouseParams as _GH

    mode = str(mode).strip().lower()
    if mode == "shade":
        return _GH(shade_to_ambient=True), "shade"
    if mode == "heated" or heat_setpoint_c is not None:
        return _GH(heat_setpoint_c=float(heat_setpoint_c or 26.0)), "heated"
    return _GH(), "poly"


def _run_season(*, species_key: str, crop_key: str, grow_area_m2: float, site: str,
                init, days: int, greenhouse: str, heat_setpoint_c: float | None,
                label_extra: str = "") -> str:
    from aqua_model.production import ProductionParams as _PP, format_summary, simulate_production
    from aqua_model.crops import get_crop
    from aqua_model.species import get_species

    weather, _meta = _climate_days(site)
    gh, mode = _greenhouse_from(greenhouse, heat_setpoint_c)
    n_days = max(1, min(int(days), len(weather)))
    run = simulate_production(
        init(weather[0].t_mean_c) if callable(init) else init,
        weather[:n_days], get_species(species_key), species_key, get_crop(crop_key),
        float(grow_area_m2), params=_PP(greenhouse=gh))
    label = f"{site} · {mode}" + (f" @{gh.heat_setpoint_c:.0f}C" if gh.heat_setpoint_c else "")
    return format_summary(run, site_label=label + label_extra)


@tool
def simulate_season(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    site: str,
    fish_count: int | None = None,
    start_weight_g: float = 20.0,
    volume_l: float | None = None,
    water_budget_lpd: float | None = None,
    system_type: str = "raft",
    days: int = 365,
    greenhouse: str = "poly",
    heat_setpoint_c: float | None = None,
    biofilter_cycled: bool | None = None,
) -> str:
    """SIMULATE a season of this system at a real site — the digital twin. Returns projected
    fish harvest (kg), crop harvest (kg), feed use and realized FCR, water-temperature range,
    nitrogen peaks, and WHICH factor limited the crop (light / temperature / nitrogen), with
    honest warnings (lethal-temperature days, suppressed feeding) and what is NOT modelled.

    TWO WAYS TO SET THE SYSTEM — prefer the first after a design conversation:
    1. FROM THE AGREED DESIGN (leave fish_count and volume_l unset, give water_budget_lpd
       and system_type): the tool sizes the design itself with the same deterministic
       calculator and stocks it with the design's own fish count and volume — no retyping
       numbers between tools, so the twin simulates exactly the system that was designed.
    2. EXPLICIT (give fish_count and volume_l directly), for ad-hoc questions. For a
       system the user already runs, prefer simulate_my_system, which reads their profile.

    Run twice with one change to compare scenarios — the relative difference is the
    trustworthy part.

    site: a fetched climate slug (e.g. 'ouagadougou_2025'). If missing, the error tells
        you the fetch command to give the user.
    greenhouse: 'poly' (+3C, 70% light), 'shade' (ambient, full light — the realistic hot-
        climate option), or 'heated' (poly + water heater at heat_setpoint_c, e.g. 26).
    start_weight_g: stocking weight (default 20 g fingerlings).
    biofilter_cycled: leave unset for the honest default — a designed NEW build starts
        uncycled (the cycling transient, nitrite spike included, is part of a first
        season), an explicit run assumes an established, cycled system."""
    from aqua_model.production import start_state, start_state_from_design
    from aqua_model.species import get_species

    species_key = str(fish_species).strip().lower()
    crop_key = str(crop).strip().lower()
    try:
        species = get_species(species_key)
        from aqua_model.crops import get_crop
        get_crop(crop_key)
    except KeyError as err:
        return f"Unknown species or crop: {err}. Call list_supported_species_and_crops."

    designed_note = ""
    if fish_count is None or volume_l is None:
        # Design mode: size the system the user agreed to, then stock it.
        if water_budget_lpd is None:
            return ("Give either (fish_count and volume_l) for an explicit run, or "
                    "water_budget_lpd (+ system_type) so I can size the agreed design first.")
        try:
            design = validate_design_input(species_key, crop_key, float(grow_area_m2),
                                           26.0, float(water_budget_lpd), None,
                                           str(system_type).strip().lower())
        except ValidationError as err:
            return serialize.serialize_validation_error(err.errors)
        out = size_system(design)
        if not out.feasible:
            return (f"The design is infeasible ({out.binding_constraint}) — fix the design "
                    "before simulating it.")

        cycled = False if biofilter_cycled is None else bool(biofilter_cycled)

        def init(t0: float):
            return start_state_from_design(out, species, water_temp_c=t0,
                                           start_weight_g=float(start_weight_g),
                                           cycled=cycled)
        designed_note = (f" · designed: {out.fish_count} fish, "
                         f"{out.system_volume_l:,.0f} L")
    else:
        cycled = True if biofilter_cycled is None else bool(biofilter_cycled)

        def init(t0: float):
            return start_state(volume_l=float(volume_l), fish_count=int(fish_count),
                               start_weight_g=float(start_weight_g), water_temp_c=t0,
                               species=species, cycled=cycled)
    try:
        return _run_season(species_key=species_key, crop_key=crop_key,
                           grow_area_m2=float(grow_area_m2), site=site, init=init,
                           days=days, greenhouse=greenhouse,
                           heat_setpoint_c=heat_setpoint_c, label_extra=designed_note)
    except FileNotFoundError as err:
        return str(err)


@tool
def simulate_my_system(
    site: str | None = None,
    days: int = 365,
    greenhouse: str = "poly",
    heat_setpoint_c: float | None = None,
) -> str:
    """SIMULATE the season ahead for the system THIS USER ALREADY RUNS, from their saved
    profile — the digital-twin mirror of their real farm. Use whenever a user with an
    existing system asks "what will MY system do", "should I add a heater", "is my winter
    going to be a problem". Before calling, make sure the profile holds their real setup
    (update_profile with: fish_species, crop, grow_area_m2, tank_volume_l, fish_count,
    fish_avg_weight_g, and climate_site once their weather is fetched). If anything
    essential is missing this returns the exact list to ask the user for — ask, save with
    update_profile, then call again. The biofilter is assumed cycled (it is a running
    system). Compare scenarios by calling twice with a different greenhouse mode."""
    from aqua_model.production import start_state
    from aqua_model.species import get_species

    cur = runtime.get_current()
    if cur is None:
        return "Profile unavailable right now — use simulate_season with explicit numbers."
    mem, user_id = cur
    facts = mem.get_facts(user_id) or {}

    needed = ("fish_species", "crop", "grow_area_m2", "tank_volume_l", "fish_count",
              "fish_avg_weight_g")
    missing = [k for k in needed if not str(facts.get(k, "")).strip()]
    site = _clean_optional(site) or str(facts.get("climate_site", "")).strip()
    if not site:
        missing.append("climate_site (fetch their weather first: "
                       "python scripts/fetch_climate.py --lat <LAT> --lon <LON> --name <site>)")
    if missing:
        return ("To mirror their system I still need: " + ", ".join(missing) +
                ". Ask the user, save with update_profile, then call this again.")

    species_key = str(facts["fish_species"]).strip().lower()
    try:
        species = get_species(species_key)
    except KeyError:
        return f"Unknown species in profile: {facts['fish_species']!r} — correct it first."

    def init(t0: float):
        return start_state(volume_l=float(facts["tank_volume_l"]),
                           fish_count=int(float(facts["fish_count"])),
                           start_weight_g=float(facts["fish_avg_weight_g"]),
                           water_temp_c=t0, species=species, cycled=True)
    try:
        return _run_season(species_key=species_key,
                           crop_key=str(facts["crop"]).strip().lower(),
                           grow_area_m2=float(facts["grow_area_m2"]), site=site,
                           init=init, days=days, greenhouse=greenhouse,
                           heat_setpoint_c=heat_setpoint_c,
                           label_extra=" · your system")
    except FileNotFoundError as err:
        return str(err)
    except (KeyError, ValueError) as err:
        return f"Profile value unusable: {err} — correct it with update_profile."


@tool
def what_if_nitrogen(
    fish_species: str,
    volume_l: float,
    feed_g_per_day: float,
    temperature_c: float,
    change: str,
    new_feed_g_per_day: float | None = None,
    new_temperature_c: float | None = None,
    add_fish_kg: float | None = None,
    days: int = 30,
) -> str:
    """Fork the NITROGEN twin and ask "what if" before doing it to live fish. Compares an
    intervention against leaving things alone: ammonia/nitrite/nitrate peak ratios, threshold
    crossings and timing, with an uncertainty band. Use for operational questions on a
    RUNNING system: "can I double the feed", "what if I stock 200 more fingerlings", "what
    does a cold week do". The verdict is RELATIVE (3x higher), which survives model error;
    absolute levels are not to be trusted.

    change: a short human label for the intervention (e.g. 'double feed')."""
    from aqua_model.scenario import Intervention, compare, format_comparison, run_scenario
    from aqua_model.species import get_species
    from aqua_model.twin import TwinState, mature_biofilter

    try:
        species = get_species(str(fish_species).strip().lower())
    except KeyError as err:
        return f"Unknown species: {err}. Call list_supported_species_and_crops."
    aob, nob = mature_biofilter(species, float(feed_g_per_day))
    state = TwinState(volume_l=float(volume_l), aob_capacity_g_day=aob, nob_capacity_g_day=nob)
    base = Intervention(name="leave things alone")
    change_iv = Intervention(
        name=str(change),
        feed_g_per_day=float(new_feed_g_per_day) if new_feed_g_per_day is not None else None,
        temperature_c=float(new_temperature_c) if new_temperature_c is not None else None,
        add_fish_kg=float(add_fish_kg) if add_fish_kg is not None else None,
    )
    kw = dict(days=max(7, min(int(days), 365)), feed_g_per_day=float(feed_g_per_day),
              temperature_c=float(temperature_c))
    baseline = run_scenario(state, species, base, **kw)
    scenario = run_scenario(state, species, change_iv, **kw)
    return format_comparison(compare(baseline, scenario))


@tool
def design_system_3d(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    system_type: str = "raft",
) -> str:
    """DESIGN the full system and send the user an interactive 3D model of it — greenhouse,
    fish tanks, filtration, grow beds, plumbing with animated flow, and swimming fish. The
    file is a self-contained HTML the user opens in any browser (works offline). Use when
    the user wants to SEE the system in 3D, walk through the design, or asks what the build
    looks like. The geometry is derived from the same deterministic sizing as
    size_aquaponics_system — it is the sized design drawn, not an illustration.

    system_type: 'raft', 'nft', 'media_bed', or 'vertical_tower' — the layout changes
    accordingly (media beds skip the separate biofilter; towers raise the roof)."""
    import os
    import sys
    import tempfile
    from pathlib import Path

    from aqua_model.layout import plan_layout
    from aqua_model.scene3d import to_scene

    try:
        design = validate_design_input(fish_species, crop, grow_area_m2, temperature_c,
                                       water_budget_lpd, None, system_type)
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    out = size_system(design)
    layout = plan_layout(out, crop_label=crop, species_label=fish_species)
    scene = to_scene(
        layout, out,
        name=f"{system_type.replace('_', ' ').title()} aquaponics — {fish_species} + {crop}",
        subtitle=(f"{out.grow_area_m2:.0f} m² grow area · {out.fish_count} fish "
                  f"({out.fish_biomass_kg:.0f} kg) · {out.system_volume_l:,.0f} L · greenhouse "
                  f"{layout.greenhouse.width_m:.1f}×{layout.greenhouse.length_m:.1f} m"))
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from render_3d import build_html
    finally:
        sys.path.remove(str(scripts_dir))
    html = build_html(scene, title=scene["name"])
    fd, path = tempfile.mkstemp(prefix="agronaut_design3d_", suffix=".html")
    with os.fdopen(fd, "w") as fh:
        fh.write(html)
    runtime.add_attachment(path)
    gh = layout.greenhouse
    return (f"Rendered the 3D model (attached as an HTML file — opens in any browser, "
            f"offline). Greenhouse {gh.width_m:.1f} x {gh.length_m:.1f} m, "
            f"{len(layout.components)} components, {out.fish_count} fish. Tell the user to "
            f"open the file, then orbit/zoom; toggles show flow, fish and labels. Offer to "
            f"simulate a season at their site next (simulate_season).")


@tool
def estimate_system_cost(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    region: str,
    system_type: str = "raft",
    greenhouse: str = "poly",
) -> str:
    """ESTIMATE what the designed system costs to BUILD (capex) and RUN (opex/year), from
    researched regional prices with sources and dates. Quantities are taken off the same
    deterministic design + layout the 3D view draws, so the estimate prices the system the
    user saw. Output is RANGES, names any component the region's book cannot price (the
    total excludes it, loudly), and lists what is not included (land, labour, delivery).

    Use whenever a design conversation reaches budget: "what will it cost", "can I afford
    this", "cost in my country". region: a key from the price book — call with an unknown
    region (e.g. 'list') to get the available regions. greenhouse: 'poly' or 'shade'
    (prices the envelope the user will actually build)."""
    import json
    from pathlib import Path

    from aqua_model.costing import estimate_cost, format_estimate
    from aqua_model.layout import plan_layout

    book_path = Path(__file__).resolve().parent.parent / "data" / "price_book.json"
    if not book_path.exists():
        return ("No price book on disk yet (data/price_book.json). Tell the user cost "
                "estimation is being set up and they should ask again soon.")
    book = json.loads(book_path.read_text())
    regions = sorted(book.get("regions", {}))
    region_key = str(region).strip().lower()
    if region_key not in book.get("regions", {}):
        return ("Unknown region: choose one of " + ", ".join(regions) +
                ". Pick the closest to the user's location and SAY which you used — "
                "prices vary hugely between regions.")
    try:
        design = validate_design_input(fish_species, crop, grow_area_m2, temperature_c,
                                       water_budget_lpd, None, system_type)
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    out = size_system(design)
    layout = plan_layout(out, crop_label=crop, species_label=fish_species)
    est = estimate_cost(out, layout, book, region_key,
                        species_key=str(fish_species).strip().lower(),
                        greenhouse_mode=str(greenhouse).strip().lower())
    header = ("" if out.feasible
              else f"NOTE: this design is infeasible ({out.binding_constraint}) — "
                   "the estimate prices it anyway, but fix the design first.\n\n")
    return header + format_estimate(est)


@tool
def design_full_system(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    system_type: str = "raft",
    reliable_power: bool = True,
    wants_max_nutrient_reuse: bool = False,
    operator_experience: str = "beginner",
    architecture: str | None = None,
) -> str:
    """DESIGN the COMPLETE system — not just sizes, but WHICH components this user's needs
    call for, each with its reason: coupled or decoupled loops, settling tank vs radial-flow
    separator (or none — small media beds filter themselves), dedicated biofilter, degassing,
    mineralization, sump vs hydroponic reservoir. Sends the full 3D model as a file. Use
    this as the design conversation's MAIN closing move, in place of separately calling
    sizing + 3D, whenever the user wants "the whole system" or asks what components they
    need. The component set ADAPTS: a backyard media-bed unit comes back as four
    components, a trout+basil farm comes back decoupled with the full treatment train —
    and every choice says why, with its source.

    reliable_power: False adds resilience warnings and steers away from NFT/towers.
    wants_max_nutrient_reuse: True adds a mineralization loop even when coupled.
    operator_experience: beginner | intermediate | expert (beginners pushed to decoupled
        get a workload warning, never a silent block).
    architecture: force 'coupled' or 'decoupled'; leave unset to let the rules decide."""
    import os
    import sys as _sys
    import tempfile
    from pathlib import Path

    from aqua_model.crops import get_crop
    from aqua_model.flowsheet import Needs, format_flowsheet, plan_flowsheet
    from aqua_model.layout import plan_layout
    from aqua_model.scene3d import to_scene
    from aqua_model.species import get_species

    try:
        design = validate_design_input(fish_species, crop, grow_area_m2, temperature_c,
                                       water_budget_lpd, None, system_type)
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    out = size_system(design)
    species = get_species(str(fish_species).strip().lower())
    crop_obj = get_crop(str(crop).strip().lower())
    arch = _clean_optional(architecture)
    fs = plan_flowsheet(out, species, crop_obj, Needs(
        reliable_power=bool(reliable_power),
        wants_max_nutrient_reuse=bool(wants_max_nutrient_reuse),
        operator_experience=str(operator_experience).strip().lower(),
        force_architecture=arch if arch in ("coupled", "decoupled") else None))
    layout = plan_layout(out, crop_label=crop, species_label=fish_species, flowsheet=fs)
    scene = to_scene(
        layout, out,
        name=f"{fs.architecture.title()} {system_type.replace('_', ' ')} — "
             f"{fish_species} + {crop}",
        subtitle=(f"{out.grow_area_m2:.0f} m² · {out.fish_count} fish · "
                  f"{len(layout.components)} components · greenhouse "
                  f"{layout.greenhouse.width_m:.1f}×{layout.greenhouse.length_m:.1f} m"))
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    _sys.path.insert(0, str(scripts_dir))
    try:
        from render_3d import build_html
    finally:
        _sys.path.remove(str(scripts_dir))
    fd, path = tempfile.mkstemp(prefix="agronaut_fullsystem_", suffix=".html")
    with os.fdopen(fd, "w") as fh:
        fh.write(build_html(scene, title=scene["name"]))
    runtime.add_attachment(path)
    header = ("" if out.feasible else
              f"NOTE: infeasible as sized ({out.binding_constraint}) — resolve before building.\n\n")
    return (header + format_flowsheet(fs)
            + f"\n\nSized: {out.fish_count} fish ({out.fish_biomass_kg:.0f} kg), "
              f"{out.system_volume_l:,.0f} L total, feed {out.feed_g_per_day:.0f} g/day, "
              f"greenhouse {layout.greenhouse.width_m:.1f}×{layout.greenhouse.length_m:.1f} m."
              "\n(3D model attached — opens in any browser, offline.) Offer next: "
              "simulate_season at their site, then estimate_system_cost / business_case.")


@tool
def fetch_site_climate(place: str) -> str:
    """FETCH a site's real weather so the twin can simulate there — call this FIRST when a
    simulation, business case, or heater question needs a site that is not yet in the list
    of climate slugs. Give the town/city name (e.g. 'Bobo-Dioulasso', 'Taichung'); it is
    geocoded, last calendar year's daily temperature and sunlight are pulled from NASA
    POWER (no key), and the resulting slug is saved to the user's profile as climate_site.
    Takes a few seconds. Then call simulate_season / simulate_my_system / business_case
    with the returned slug. If the place is ambiguous, the reply lists candidates — ask
    the user which one they mean."""
    import json as _json
    import re
    import sys as _sys
    import urllib.parse
    import urllib.request
    from datetime import date
    from pathlib import Path

    q = _clean_optional(place)
    if not q:
        return "Give me the town or city name to fetch weather for."
    try:
        url = ("https://geocoding-api.open-meteo.com/v1/search?name="
               + urllib.parse.quote(q) + "&count=5&language=en&format=json")
        with urllib.request.urlopen(url, timeout=30) as r:
            hits = _json.load(r).get("results") or []
    except Exception as err:  # noqa: BLE001 — a network failure must become words, not a crash
        return f"Geocoding failed ({err}) — ask the user for latitude/longitude instead."
    if not hits:
        return (f"No place called {q!r} found. Ask the user to spell it differently or "
                "give latitude/longitude.")
    # Several distinct countries under one name => genuinely ambiguous; ask.
    countries = {h.get("country") for h in hits}
    if len(countries) > 1 and len(hits) > 1:
        opts = "; ".join(f"{h['name']}, {h.get('admin1', '?')}, {h.get('country', '?')}"
                         for h in hits[:4])
        return f"Ambiguous — which one? {opts}"
    h = hits[0]
    slug = re.sub(r"[^a-z0-9]+", "_", h["name"].lower()).strip("_")
    year = date.today().year - 1
    slug = f"{slug}_{year}"

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    _sys.path.insert(0, str(scripts_dir))
    try:
        from fetch_climate import fetch_and_write
    finally:
        _sys.path.remove(str(scripts_dir))
    try:
        r = fetch_and_write(float(h["latitude"]), float(h["longitude"]), slug,
                            f"{year}-01-01", f"{year}-12-31")
    except Exception as err:  # noqa: BLE001
        return f"Weather fetch failed ({err}) — try again in a moment."

    cur = runtime.get_current()
    if cur is not None:
        mem, user_id = cur
        mem.set_facts(user_id, {"climate_site": slug,
                                "site_lat": str(h["latitude"]), "site_lon": str(h["longitude"]),
                                "location": f"{h['name']}, {h.get('country', '')}".strip(", ")},
                      source="user_stated")
    return (f"Fetched {r['n_days']} days of {year} weather for {h['name']}, "
            f"{h.get('country', '?')} (air {r['t_min']:.0f}-{r['t_max']:.0f} C). "
            f"Site slug: {slug} — saved to the profile; use it as `site` in "
            f"simulate_season / simulate_my_system / business_case.")


_TWIN_STATE_KEY = "twin_state_json"


def _live_weather(lat: float, lon: float, past_days: int, forecast_days: int):
    """Past + forecast daily weather in one keyless Open-Meteo call, as DailyClimate.

    Returns (dates, days). past_days is capped at 92 (the API's window)."""
    import json as _json
    import urllib.request

    from aqua_model.climate import from_records

    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           f"&past_days={min(92, max(0, past_days))}"
           f"&forecast_days={min(16, max(1, forecast_days))}"
           "&daily=temperature_2m_mean,temperature_2m_min,temperature_2m_max,"
           "shortwave_radiation_sum&timezone=auto")
    with urllib.request.urlopen(url, timeout=45) as r:
        d = _json.load(r)["daily"]
    recs, dates = [], []
    for i, day in enumerate(d["time"]):
        vals = (d["temperature_2m_mean"][i], d["temperature_2m_min"][i],
                d["temperature_2m_max"][i], d["shortwave_radiation_sum"][i])
        if any(v is None for v in vals):
            continue
        recs.append({"t_mean_c": vals[0], "t_min_c": vals[1], "t_max_c": vals[2],
                     "solar_mj_m2": vals[3]})
        dates.append(day)
    return dates, from_records(recs)


def _load_mirror(mem, user_id):
    import json as _json

    from aqua_model import mirror

    raw = (mem.get_facts(user_id) or {}).get(_TWIN_STATE_KEY)
    if not raw:
        return None, None
    return mirror.from_dict(_json.loads(raw))


def _save_mirror(mem, user_id, state, as_of: str) -> None:
    import json as _json

    from aqua_model import mirror

    mem.set_facts(user_id, {_TWIN_STATE_KEY: _json.dumps(mirror.to_dict(state, as_of=as_of))},
                  source="user_stated")


def _mirror_context(facts: dict):
    """The profile facts the live mirror needs; returns (missing, parsed) — ask for missing."""
    needed = ("fish_species", "crop", "grow_area_m2", "tank_volume_l", "fish_count",
              "fish_avg_weight_g")
    missing = [k for k in needed if not str(facts.get(k, "")).strip()]
    if not str(facts.get("site_lat", "")).strip():
        missing.append("site location (call fetch_site_climate with their town first)")
    return missing


def _advance_mirror(mem, user_id, facts: dict, forecast_days: int = 1,
                    greenhouse: str = "poly"):
    """Advance the stored twin to TODAY through real weather; create it if absent.

    Returns (state_today, run_forecast_or_None, notes). The forecast run, when asked for,
    continues from today's state through the coming days."""
    from datetime import date, datetime

    from aqua_model.crops import get_crop
    from aqua_model.production import ProductionParams, simulate_production, start_state
    from aqua_model.species import get_species

    gh, _mode = _greenhouse_from(greenhouse, None)
    params = ProductionParams(greenhouse=gh)
    species = get_species(str(facts["fish_species"]).strip().lower())
    crop = get_crop(str(facts["crop"]).strip().lower())
    lat, lon = float(facts["site_lat"]), float(facts["site_lon"])
    area = float(facts["grow_area_m2"])
    notes: list[str] = []

    stored, as_of = _load_mirror(mem, user_id)
    today = date.today()
    if stored is None:
        stored = start_state(volume_l=float(facts["tank_volume_l"]),
                             fish_count=int(float(facts["fish_count"])),
                             start_weight_g=float(facts["fish_avg_weight_g"]),
                             water_temp_c=25.0, species=species, cycled=True)
        as_of = today.isoformat()
        notes.append("started your live twin today from the profile")
    behind = (today - datetime.strptime(as_of, "%Y-%m-%d").date()).days
    if behind > 92:
        notes.append(f"the mirror was {behind} days behind — advanced only the last 92 "
                     "(the weather API's window); its state before that is stale")
        behind = 92

    dates, weather = _live_weather(lat, lon, past_days=behind + 1,
                                   forecast_days=max(1, forecast_days))
    today_iso = today.isoformat()
    idx_today = dates.index(today_iso) if today_iso in dates else len(dates) - 1

    if behind > 0 and idx_today > 0:
        past = weather[max(0, idx_today - behind):idx_today]
        if past:
            run = simulate_production(stored, past, species,
                                      str(facts["fish_species"]).strip().lower(), crop, area,
                                      params=params)
            stored = run.trajectory[-1].state
            notes.append(f"advanced {len(past)} day(s) through your site's real weather")
    _save_mirror(mem, user_id, stored, today_iso)

    fc_run = None
    if forecast_days > 1 and idx_today < len(weather):
        from aqua_model.production import simulate_production as _sim
        fc_run = _sim(stored, weather[idx_today:], species,
                      str(facts["fish_species"]).strip().lower(), crop, area, params=params)
    return stored, fc_run, notes


@tool
def log_my_readings(
    greenhouse: str = "poly",
    water_temp_c: float | None = None,
    ammonia_mg_l: float | None = None,
    nitrite_mg_l: float | None = None,
    nitrate_mg_l: float | None = None,
    fish_avg_weight_g: float | None = None,
    fish_count: int | None = None,
) -> str:
    """LOG the user's real measurements into their LIVE twin — the mirror of their actual
    system. Call whenever a user with a running system reports a reading (test-kit ammonia/
    nitrite/nitrate, water temperature, a fish weighing, a mortality). The twin first
    advances to today through the site's real weather, then is pulled toward the readings,
    and the reply says HOW FAR OFF the model was (the innovation) — that drift report is
    valuable, share it. Requires the profile facts simulate_my_system needs plus a fetched
    site (fetch_site_climate); if something is missing the reply lists exactly what.
    greenhouse: the envelope they actually run — 'poly', 'shade', or 'heated'."""
    from aqua_model import mirror

    cur = runtime.get_current()
    if cur is None:
        return "No session — cannot keep a live twin here."
    mem, user_id = cur
    facts = mem.get_facts(user_id) or {}
    missing = _mirror_context(facts)
    if missing:
        return ("The live twin needs: " + ", ".join(missing) +
                ". Ask, save with update_profile (and fetch_site_climate for the site), "
                "then log again.")
    try:
        state, _fc, notes = _advance_mirror(mem, user_id, facts, greenhouse=greenhouse)
    except Exception as err:  # noqa: BLE001 — a weather hiccup must become words
        return f"Could not advance the twin ({err}) — try again shortly."
    state, innovation = mirror.nudge(state, {
        "water_temp_c": water_temp_c, "tan_mg_l": ammonia_mg_l,
        "no2_mg_l": nitrite_mg_l, "no3_mg_l": nitrate_mg_l,
        "fish_avg_weight_g": fish_avg_weight_g, "fish_count": fish_count})
    from datetime import date
    _save_mirror(mem, user_id, state, date.today().isoformat())
    if fish_count is not None or fish_avg_weight_g is not None:
        upd = {}
        if fish_count is not None:
            upd["fish_count"] = fish_count
        if fish_avg_weight_g is not None:
            upd["fish_avg_weight_g"] = fish_avg_weight_g
        mem.set_facts(user_id, upd, source="user_stated")
    return ("Logged. " + " ".join(notes) + "\n"
            + "\n".join("  - " + n for n in innovation)
            + "\nNow: " + mirror.snapshot_line(state))


@tool
def my_system_forecast(days_ahead: int = 7, greenhouse: str = "poly") -> str:
    """The LIVE twin's answer to "how is my system doing, and what happens next?" —
    advances the user's persistent twin to TODAY through their site's real weather, then
    runs it forward through the actual forecast (up to 15 days). Reports where the system
    stands now and what the coming days do to it (nitrogen peaks, temperature stress,
    harvest progress), with the model's honesty lines. Use for "how's my pond", "what will
    this heatwave do", "check my system". The state persists between conversations —
    logged readings (log_my_readings) keep it honest.
    greenhouse: the envelope they actually run — 'poly', 'shade', or 'heated'."""
    from aqua_model import mirror
    from aqua_model.production import format_summary

    cur = runtime.get_current()
    if cur is None:
        return "No session — cannot keep a live twin here."
    mem, user_id = cur
    facts = mem.get_facts(user_id) or {}
    missing = _mirror_context(facts)
    if missing:
        return ("The live twin needs: " + ", ".join(missing) +
                ". Ask, save with update_profile (and fetch_site_climate), then call again.")
    try:
        state, fc_run, notes = _advance_mirror(mem, user_id, facts, greenhouse=greenhouse,
                                               forecast_days=max(2, min(int(days_ahead), 15)))
    except Exception as err:  # noqa: BLE001
        return f"Could not advance the twin ({err}) — try again shortly."
    out = ["LIVE twin — " + " ".join(notes) if notes else "LIVE twin",
           "Now: " + mirror.snapshot_line(state)]
    if fc_run is not None:
        out.append("")
        out.append(format_summary(fc_run,
                                  site_label=f"next {fc_run.summary.days} days (forecast)"))
    return "\n".join(out)


@tool
def business_case(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    water_budget_lpd: float,
    region: str,
    site: str,
    system_type: str = "raft",
    greenhouse: str = "poly",
    labour_hours_per_week: float | None = None,
    channel: str = "farm_gate",
) -> str:
    """Answer "will this MAKE money?" — the full case: build cost, running cost, a
    simulated year's harvest priced at researched farm-gate prices, margin, and simple
    payback. Use when a design conversation reaches viability: "is this worth building",
    "when do I get my money back", "can I live off this".

    This runs the whole chain itself (size -> layout -> cost -> simulate a year at the
    site's real climate -> price the harvest), so call it directly rather than stitching
    the other tools together. A losing system is reported as losing, plainly.

    labour_hours_per_week: pass the operator's realistic weekly hours to price labour —
    it usually decides whether the system is a business or a hobby, and the case reports
    the verdict both with and without.
    channel: how they will SELL — 'farm_gate' (default, to a wholesaler), 'restaurant', or
    'direct' (farmers' market). Direct roughly doubles produce revenue and is often the
    difference between a losing and a working system, so run it both ways when a design
    does not clear at farm-gate prices — but say plainly that the higher price has to be
    earned with stalls, transport and hours.
    region: a price-book region; site: a climate slug."""
    import json
    from pathlib import Path

    from aqua_model.business import build_case, format_case
    from aqua_model.costing import estimate_cost
    from aqua_model.crops import get_crop
    from aqua_model.layout import plan_layout
    from aqua_model.production import (
        ProductionParams as _PP, simulate_production, start_state_from_design,
    )
    from aqua_model.species import get_species

    book_path = Path(__file__).resolve().parent.parent / "data" / "price_book.json"
    if not book_path.exists():
        return "No price book on disk yet (data/price_book.json)."
    book = json.loads(book_path.read_text())
    region_key = str(region).strip().lower()
    if region_key not in book.get("regions", {}):
        return ("Unknown region: choose one of " + ", ".join(sorted(book["regions"])) +
                ". Say which you used — prices vary hugely between regions.")

    species_key = str(fish_species).strip().lower()
    crop_key = str(crop).strip().lower()
    try:
        species = get_species(species_key)
        crop_obj = get_crop(crop_key)
    except KeyError as err:
        return f"Unknown species or crop: {err}. Call list_supported_species_and_crops."
    try:
        weather, _meta = _climate_days(site)
    except FileNotFoundError as err:
        return str(err)
    try:
        design = validate_design_input(species_key, crop_key, float(grow_area_m2), 26.0,
                                       float(water_budget_lpd), None,
                                       str(system_type).strip().lower())
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)

    out = size_system(design)
    if not out.feasible:
        return (f"The design is infeasible ({out.binding_constraint}) — fix it before "
                "asking whether it pays.")
    layout = plan_layout(out, crop_label=crop_key, species_label=species_key)
    gh, mode = _greenhouse_from(greenhouse, None)
    # Simulate FIRST, then cost the feed the fish actually ate — pricing the design's
    # steady-state feed rate against a simulated first year's harvest is not like-for-like.
    init = start_state_from_design(out, species, water_temp_c=weather[0].t_mean_c,
                                   start_weight_g=20.0, cycled=False)
    run = simulate_production(init, weather[:365], species, species_key, crop_obj,
                              float(grow_area_m2), params=_PP(greenhouse=gh))
    cost = estimate_cost(out, layout, book, region_key, species_key=species_key,
                         greenhouse_mode=mode if mode == "shade" else "poly",
                         feed_kg_year=run.summary.feed_used_kg)
    case = build_case(run.summary, cost, book, region_key, crop_key=crop_key,
                      species_key=species_key, crop_category=crop_obj.category,
                      labour_hours_per_week=labour_hours_per_week, channel=channel)
    return (f"[{site} · {mode} · {system_type}]\n\n" + format_case(case))


AGRONAUT_TOOLS = [
    size_aquaponics_system,
    size_mixed_bed_aquaponics,
    size_hydroponic_system_tool,
    optimize_fish_crop_ratio,
    list_supported_species_and_crops,
    design_envelope_reality_check,
    render_design_report,
    render_pilot_proposal,
    render_system_schematic,
    simulate_season,
    simulate_my_system,
    estimate_system_cost,
    business_case,
    fetch_site_climate,
    log_my_readings,
    my_system_forecast,
    what_if_nitrogen,
    design_system_3d,
    design_full_system,
    search_knowledge_base,
    triage_visual_symptoms,
    remember_about_user,
    update_profile,
    schedule_followup,
    nominate_shared_insight,
    search_community_knowledge,
    record_measurement,
]
