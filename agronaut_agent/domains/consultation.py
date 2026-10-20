"""Domain-specific consultation questions and tool policy for an explicit active unit."""

from agronaut_agent import profile

COMMON_TOOLS = frozenset({"agriculture_capabilities", "calculate_agriculture", "update_profile",
                          "remember_about_user", "search_knowledge_base", "schedule_followup",
                          "decide_on_recommendations", "crop_records", "farm_resources"})
HYDRO_TOOLS = COMMON_TOOLS | {"size_hydroponic_system_tool"}
SOIL_FIELDS = ("system_stage", "location", "crop", "grow_area_m2", "water_budget_lpd",
               "water_source", "goal", "goal_detail", "experience_level", "crop_stage",
               "soil_type", "soil_water_status", "planting_date", "soil_ph")
_FISH_FIELDS = {"fish_species", "fish_count", "fish_avg_weight_g", "tank_volume_l",
                "ammonia_mgl", "objective"}
_SOIL_QUESTIONS = {
    "location": "Where is this field?",
    "grow_area_m2": "How much of the field is planted, in square metres or hectares?",
    "crop": "Which crop are you growing in this field?",
    "crop_stage": "Is the crop newly planted, growing leaves, flowering, or filling fruit or grain?",
    "water_source": "Where does the irrigation water come from?",
    "soil_type": "What do you know about the soil, such as its texture or a soil-test result?",
    "soil_water_status": "Do you have a recent soil-moisture measurement or irrigation record?",
}


def tools_for_unit(unit, tools):
    if unit is None:
        # Existing single-system accounts retain their interface and prompt size.
        # Field calculations require selecting a field, which also establishes scope.
        return [t for t in tools if t.name not in {"calculate_agriculture", "agriculture_capabilities", "field_records", "crop_records", "farm_resources"}]
    if unit.production_system == "aquaponics":
        return [t for t in tools if t.name not in {"size_hydroponic_system_tool", "calculate_agriculture", "field_records"}]
    allowed = HYDRO_TOOLS if unit.production_system == "hydroponics" else COMMON_TOOLS
    if unit.production_system == "soil":
        allowed = allowed | {"field_records"}
    if unit.production_system is None:
        allowed = {"remember_about_user", "agriculture_capabilities"}
    return [t for t in tools if t.name in allowed]


def fields_for(domain):
    if domain == "soil":
        return SOIL_FIELDS
    if domain == "hydroponics":
        return tuple(k for k in profile.PROFILE_KEYS if k not in _FISH_FIELDS)
    return profile.PROFILE_KEYS


def filter_facts(facts, domain):
    return {k: v for k, v in facts.items() if k in fields_for(domain)}


def field_problem(key, value, domain):
    if domain != "soil":
        from aqua_model.validate import check_design_field

        return check_design_field(key, value)
    from agro_model.validation import number

    try:
        if key in {"grow_area_m2", "water_budget_lpd", "soil_ph"}:
            if isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            number(key, float(value), low=0, high=14 if key == "soil_ph" else None,
                   positive=key == "grow_area_m2")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be nonempty text")
    except (ValueError, TypeError, OverflowError) as exc:
        return str(exc)
    return None


def missing(goal, facts, domain):
    if domain == "soil":
        keys = tuple(_SOIL_QUESTIONS) if goal in ("design", "optimize") else ()
    elif domain == "hydroponics":
        keys = ("grow_area_m2", "crop", "temperature_c", "water_budget_lpd") if goal in ("design", "optimize") else ()
    else:
        return profile.missing_essentials(goal, facts)
    return [k for k in keys if not str(facts.get(k) or "").strip()]


def question(goal, facts, domain):
    todo = missing(goal, facts, domain)
    if not todo:
        return None
    if domain == "soil":
        return _SOIL_QUESTIONS[todo[0]]
    if domain == "hydroponics":
        return {"grow_area_m2": "What planted area do you have, in square metres?",
                "crop": "Which crop do you want to grow?",
                "temperature_c": "What is the measured or expected nutrient-solution temperature?",
                "water_budget_lpd": "How much makeup water is available per day, or is that still unknown?"}[todo[0]]
    return profile.next_question(goal, facts)


def render(facts, domain):
    facts = filter_facts(facts, domain)
    if domain != "soil":
        return profile.render_profile(facts, facts.get("goal"))
    labels = {"grow_area_m2": "planted area (m²)", "water_budget_lpd": "water available (L/day)",
              "soil_ph": "soil pH"}
    lines = [f"• {labels.get(k, k.replace('_', ' '))}: {facts[k]}"
             for k in SOIL_FIELDS if str(facts.get(k) or "").strip()]
    return "YOUR FIELD\n" + "\n".join(lines) if lines else ""


def start_goal(goal, facts, domain):
    if domain not in ("soil", "hydroponics"):
        return profile.essentials_hint(goal, facts)
    if goal == "troubleshoot":
        return "What are you seeing, and when did it start?"
    return question(goal, facts, domain) or (
        "The basic profile is ready. Let's check which calculation fits and which observations it needs.")


def prompt(domain):
    return f"""You are Agronaut, an agricultural assistant for the active {domain or 'unresolved'} production unit.
Reply in the user's language, in short plain sentences. Ask one question at a time.
Use only this unit's facts and history. Other farms or systems require an explicit unit switch.
Never invent observations, weather, soil properties, crop coefficients or equipment specifications.
Save stated profile facts with update_profile; estimated assumptions must be labeled and confirmed.
Call agriculture_capabilities to check tasks, required inputs, applicability and evidence.
Call calculate_agriculture for a supported calculation and present its sources and limitations.
Use the calculate_agriculture tool's input contract; ask for missing data rather than filling it yourself.
Crop-water demand is not an irrigation schedule. Unknown soil state means an unknown refill amount.
Drip, sprinkler and surface equipment design are unavailable until their registered capabilities exist.
For hydroponic sizing use size_hydroponic_system_tool. It needs no fish information.
For soil cultivation ask about crop, stage, soil, weather, rainfall and irrigation records, not fish or tanks.
For soil units, use field_records to save requested calculations or log reported completed daily water totals.
Offer /fieldplan or field_records start_intake for water, drip/sprinkler or surface-event questions; it works without a model.
The intake accepts manual daily ET0 or Open-Meteo at explicit coordinates/dates; fetch only when the grower requests it.
Kc, soil and efficiency still require supplied values and sources; never invent them to finish a draft.
Keep plans separate from actions; replay explicitly selected daily records to create a new calculation version.
Use crop_records for requested plantings, stage/scouting observations and harvests; describe gives schemas. Never save forecasts as actual events.
search_knowledge_base is scoped to the active domain; if it returns no evidence, say what is missing.
Symptom descriptions are observations, not confirmed diagnoses. Do not infer chemical treatments.
Record approvals only when the user explicitly names the items. Never operate equipment.
Follow-ups concern this unit. Tell the user which unit you mean when recording a decision or outcome.
If the production system is unresolved, ask the user to identify it before recommending a calculator.
"""
