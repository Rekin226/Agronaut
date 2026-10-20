"""One catalog for CLI, consultations and dispatch, with explicit evidence levels.

This catalog describes the adapters registered here, not every legacy tool. Existing
tools keep their interfaces. New adapters must declare a validator and an evidence scope
before they can be offered to an agricultural consultation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Capability:
    production_system: str
    task: str
    title: str
    status: str
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...]
    knowledge_domains: tuple[str, ...]
    applicability: tuple[str, ...]
    evidence: tuple[str, ...]
    adapter: str | None = None
    command: str | None = None

    @property
    def id(self) -> str:
        return f"{self.production_system}.{self.task}"

    def missing_inputs(self, inputs: dict) -> list[str]:
        """Readiness only. Calculators still validate types, ranges and nested records."""
        def absent(value):
            if value is None or value == [] or value == {}:
                return True
            return isinstance(value, str) and value.strip().lower() in {
                "", "unknown", "not sure", "none", "null", "n/a"}
        return [key for key in self.required_inputs if absent(inputs.get(key))]

    def knowledge_filters(self) -> dict:
        # Never widen to all domains if this domain has no indexed documents yet.
        return {"domain": list(self.knowledge_domains)}

    def describe(self) -> dict:
        result = asdict(self)
        result.pop("adapter")
        result["id"] = self.id
        result["executable"] = self.adapter is not None
        if self.adapter == "field_guidance":
            from agronaut_agent.field_guides import guide_catalog, guide_languages
            result["input_schema"] = {"topic": [guide["topic"] for guide in guide_catalog()],
                                      "language": list(guide_languages()), "default_language": "en"}
        if self.adapter == "surface_advance":
            from agronaut_agent.surface_advance_io import surface_advance_input_schema
            result["input_schema"] = surface_advance_input_schema()
        if self.adapter == "drip_design":
            from agronaut_agent.irrigation_io import drip_input_schema
            result["input_schema"] = drip_input_schema()
        if self.adapter == "drip_lateral":
            from agronaut_agent.drip_lateral_io import drip_lateral_input_schema
            result["input_schema"] = drip_lateral_input_schema()
        if self.adapter == "sprinkler_lateral":
            from agronaut_agent.sprinkler_lateral_io import sprinkler_lateral_input_schema
            result["input_schema"] = sprinkler_lateral_input_schema()
        if self.adapter == "drip_zone":
            from agronaut_agent.drip_zone_io import drip_zone_input_schema
            result["input_schema"] = drip_zone_input_schema()
        if self.adapter == "drip_manifold":
            from agronaut_agent.drip_manifold_io import drip_manifold_input_schema
            result["input_schema"] = drip_manifold_input_schema()
        if self.adapter == "sprinkler_design":
            from agronaut_agent.irrigation_io import sprinkler_input_schema
            result["input_schema"] = sprinkler_input_schema()
        if self.adapter == "surface_evaluation":
            from agronaut_agent.surface_io import surface_input_schema
            result["input_schema"] = surface_input_schema()
        if self.adapter == "sprinkler_catch_test":
            from agronaut_agent.catch_io import catch_input_schema
            result["input_schema"] = catch_input_schema()
        if self.adapter == "drip_emitter_test":
            from agronaut_agent.emitter_io import emitter_input_schema
            result["input_schema"] = emitter_input_schema()
        if self.adapter == "soil_profile":
            from agronaut_agent.soil_profile_io import soil_profile_input_schema
            result["input_schema"] = soil_profile_input_schema()
        if self.adapter == "dual_crop_water":
            from agronaut_agent.evaporation_io import evaporation_input_schema
            result["input_schema"] = evaporation_input_schema()
        if self.adapter == "coupled_soil_water":
            from agronaut_agent.coupled_water_io import coupled_water_input_schema
            result["input_schema"] = coupled_water_input_schema()
        if self.adapter == "nutrient_budget":
            from agronaut_agent.nutrition_io import nutrition_input_schema
            result["input_schema"] = nutrition_input_schema()
        if self.adapter in ("field_demand", "field_budget"):
            from agronaut_agent.field_io import field_input_schema
            result["input_schema"] = field_input_schema()
        return result


_AQUATIC_INPUTS = ("crop", "grow_area_m2", "temperature_c", "water_budget_lpd")
_FIELD_INPUTS = ("schema_version", "site", "area_m2", "crop", "weather")
_FIELD_OPTIONAL = ("soil", "initial_state", "application_efficiency", "events", "weather_sources")
_AQUATIC_SCOPE = (
    "Only species, crops and growing methods accepted by the existing aqua_model validators.",
    "Literature-based preliminary sizing; not a field-validated performance forecast.",
    "Use the result's operating envelope, coefficients, assumptions and not_modeled list.",
)
_FIELD_SCOPE = (
    "Supplied daily weather with net radiation, humidity and wind; explicit source and date.",
    "Caller-supplied crop coefficient and stage; no automatic crop or regional calibration.",
    "No demonstrated field-validation scope, including for tomato, onion or maize.",
)
_CAPABILITIES = (
    Capability("soil", "field_guidance", "Offline field observation guides", "guidance",
               ("topic",), ("language",), ("soil", "irrigation"), (
                   "General sourced explanations and observation preparation; no calculation, confirmed diagnosis or treatment prescription.",
                   "English originals; French and Traditional Chinese draft translations pending local review. No regional validation or automatic method selection.",),
               ("FAO crop-water/irrigation references and university extension scouting guidance.",
                "Each original guide includes its own source links and applicability."),
               adapter="field_guidance", command="field-guide"),
    Capability("aquaponics", "size", "Fish and plant system sizing", "calculation",
               ("fish_species",) + _AQUATIC_INPUTS, ("source_water_note", "system_type", "crop_plan"),
               ("aquaponics",), _AQUATIC_SCOPE,
               ("aqua_model/tests/test_sizing.py", "Sources accompany each coefficient in the result."),
               adapter="aquaponics_size", command="size"),
    Capability("hydroponics", "size", "Nutrient-solution system sizing", "calculation",
               _AQUATIC_INPUTS, ("source_water_note", "system_type"),
               ("hydroponics",), _AQUATIC_SCOPE,
               ("aqua_model/tests/test_hydroponics.py", "Sources accompany each coefficient in the result."),
               adapter="hydroponics_size"),
    Capability("soil", "crop_water", "Daily crop-water demand", "experimental",
               _FIELD_INPUTS, _FIELD_OPTIONAL, ("soil", "irrigation"), _FIELD_SCOPE + (
                   "Crop demand alone does not determine irrigation timing or application depth.",),
               ("FAO-56 (1998), equations 6 and 58; Example 18 reference ET check.",
                "agro_model/tests/test_field_water.py"),
               adapter="field_demand", command="water-needs"),
    Capability("soil", "root_zone_budget", "Daily root-zone water budget", "experimental",
               _FIELD_INPUTS + ("soil", "application_efficiency", "events"),
               ("initial_state", "weather_sources", "rainfall_overrides", "soil_state_updates", "root_zone_schedule"),
               ("soil", "irrigation"), _FIELD_SCOPE + (
                   "Homogeneous root zone; optional explicit daily growth and subsoil water, incoming water before daily ET, no capillary rise.",
                   "Initial soil state required for an amount; unknown state remains unknown.",
                   "No localized drip wetting, hydraulic design or method-specific schedule.",),
               ("FAO-56 (1998), equations 82–85; Example 36 soil storage checks.",
                "agro_model/tests/test_field_water.py"),
               adapter="field_budget", command="irrigation-plan"),
    Capability("soil", "drip_design", "Flat-field PC drip hydraulic design", "experimental",
               ("schema_version", "layout", "emitter", "main", "manifold", "lateral", "supply",
                "headworks", "application", "limits"), (), ("soil", "irrigation"), (
                   "Flat rectangular surface-drip layout with identical pressure-compensating emitters only.",
                   "Explicit equipment ratings, source head/flow bounds, fitting losses and water/time limits.",
                   "Supplied gross application target and localized depth/rate limits; no crop/soil schedule.",
                   "No independent field validation, pump sizing, terrain or non-PC emitter support.",),
               ("EPA EPANET 2.2 sections 3.1 and 12.1; analytic laminar and Colebrook comparison tests.",
                "agro_model/tests/test_drip.py", "agro_model/tests/test_hydraulics.py"),
               adapter="drip_design", command="design-irrigation --method drip"),
    Capability("soil", "drip_lateral", "Surveyed drip lateral pressure and flow", "experimental",
               ("schema_version", "site", "pipe", "emitter", "inlet", "limits", "conditions", "stations"), (),
               ("soil", "irrigation"), (
                   "One constant-diameter, closed-tail surface-drip lateral with explicit station elevations.",
                   "Identical supplied power-curve emitters, including non-compensating flows; no extrapolation outside the supported pressure band.",
                   "Fixed inlet pressure (v1) or a bounded pressure–flow curve at the lateral inlet (v2); nominal flows, not measured distribution or a crop-water schedule.",
                   "No independent field validation, upstream network solution, pump sizing or automatic pipe selection.",),
               ("FAO emitter pressure-discharge relation; EPA EPANET 2.2 head loss and continuity equations.",
                "agro_model/tests/test_drip_lateral.py"), adapter="drip_lateral", command="evaluate-drip-lateral"),
    Capability("soil", "drip_zone", "Simultaneous drip laterals at a common junction", "experimental",
               ("schema_version", "site", "supply", "junction", "laterals"), (), ("soil", "irrigation"), (
                   "2–20 surveyed laterals connected at one common hydraulic junction; at most 2000 emitters total.",
                   "Bounded source pressure curve uses total zone flow; all active laterals and connection losses must be represented.",
                   "No distributed manifold, looped network, pump sizing, crop-water schedule or independent field validation.",),
               ("EPA EPANET 2.2 conservation of head and flow; bounded nested series solves.",
                "agro_model/tests/test_drip_zone.py"), adapter="drip_zone", command="evaluate-drip-zone"),
    Capability("soil", "drip_manifold", "Surveyed drip distribution manifold", "experimental",
               ("schema_version", "site", "supply", "pipe", "limits", "conditions", "takeoffs"), (), ("soil", "irrigation"), (
                   "1–20 ordered takeoffs on one closed-tail constant-diameter manifold; at most 2000 emitters total.",
                   "Distinct takeoff pressures, local lateral surveys and a bounded manifold-inlet source curve at total flow.",
                   "No loops, pump sizing, irrigation schedule or independent field validation.",),
               ("EPA EPANET 2.2 conservation of head and flow; nested reverse shooting.",
                "agro_model/tests/test_drip_manifold.py"), adapter="drip_manifold", command="evaluate-drip-manifold"),
    Capability("soil", "sprinkler_lateral", "Surveyed sprinkler nozzle and riser hydraulics", "experimental",
               ("schema_version", "site", "pipe", "riser", "nozzle", "inlet", "limits", "conditions", "stations"), (), ("soil", "irrigation"), (
                   "One surveyed closed-tail lateral, identical unregulated nozzles and explicit riser heights, lengths and losses.",
                   "Individual nozzle/base pressures and flows with fixed inlet pressure or a bounded lateral-inlet pressure curve.",
                   "No catch-can uniformity, wind-pattern prediction, crop-water schedule or independent field validation.",),
               ("EPA EPANET 2.2 square-root nozzle relation, head loss and continuity equations.",
                "agro_model/tests/test_sprinkler_lateral.py"), adapter="sprinkler_lateral", command="evaluate-sprinkler-lateral"),
    Capability("soil", "sprinkler_design", "Flat-field sprinkler design envelope", "experimental",
               ("schema_version", "layout", "sprinkler", "main", "manifold", "lateral", "riser",
                "supply", "headworks", "application", "limits"), (), ("soil", "irrigation"), (
                   "Flat solid-set rectangular grid with identical full-circle square-root pressure-flow nozzles.",
                   "Conservative flow/head bounds; not an individual-nozzle hydraulic solution.",
                   "Explicit spacing/wind evidence, peak pattern factor, efficiency and soil application limits.",
                   "Supplied net-depth target; no independent field validation, crop scheduling or slope support.",),
               ("EPA EPANET 2.2 nozzle relation and pipe losses; FAO sprinkler application/spacing guidance.",
                "agro_model/tests/test_sprinkler.py"),
               adapter="sprinkler_design", command="design-irrigation --method sprinkler"),
    Capability("soil", "surface_evaluation", "Surface-irrigation event evaluation", "experimental",
               ("schema_version", "site", "geometry", "event", "infiltration", "inflow", "tailwater",
                "stations", "assessment"), (), ("soil", "irrigation"), (
                   "One fully advanced furrow, border or basin; rain-free continuous inflow and complete recession records.",
                   "Supplied Kostiakov-Lewis intake calibration, explicit interval-average flows and station times.",
                   "Water-balance checks precede modeled efficiency/uniformity estimates; no operational cutoff advice.",
                   "No independent field validation, surge irrigation or automatic intake-family selection.",),
               ("FAO Irrigation and Drainage Paper 45, chapters 3–4; independent integration and accounting tests.",
                "agro_model/tests/test_surface.py"), adapter="surface_evaluation", command="evaluate-surface"),
    Capability("soil", "nutrient_budget", "Mineral-fertilizer nutrient accounting", "experimental",
               ("schema_version", "site", "area_m2", "crop", "period", "target"), ("products",), ("soil",), (
                   "Caller-supplied remaining application targets after credits; N, P2O5 and K2O basis only.",
                   "Declared mineral product quantities and label mass percentages; liquid liters require explicit density to determine mass.",
                   "Unknown target/analysis/density values remain unknown. No soil-test interpretation, availability or regional recommendations.",
                   "No blending, application safety, scheduling or field-validation claim; matching targets is not feasibility.",),
               ("University of Maryland and University of Minnesota Extension: fertilizer mass percentages and nutrient accounting.",
                "agro_model/tests/test_nutrition.py"), adapter="nutrient_budget", command="nutrient-budget"),
    Capability("soil", "sprinkler_catch_test", "Sprinkler catch-grid uniformity evaluation", "experimental",
               ("schema_version", "site", "event", "grid", "catches"), (), ("soil", "irrigation"), (
                   "One zone, equal-area rectangular catch grid, identical full rain-free exposure and calibrated depth readings.",
                   "4–2500 positions with count divisible by four; incomplete readings withhold all statistics.",
                   "Mean caught depth/rate and low-quarter uniformity only; no efficiency, runtime advice or field-validation claim.",),
               ("UF/IFAS AE194 and USU Extension catch-can methods.", "agro_model/tests/test_catch_can.py"),
               adapter="sprinkler_catch_test", command="evaluate-sprinkler-catches"),
    Capability("soil", "drip_emitter_test", "Drip-emitter discharge evaluation", "experimental",
               ("schema_version", "site", "event", "sampling", "samples"), (), ("soil", "irrigation"), (
                   "One stable surface-drip zone, same emitter specification and equally represented distinct samples.",
                   "4–2500 emitters divisible by four; timed volumes normalized separately; unknown volume withholds aggregate statistics.",
                   "Sample flow and low-quarter uniformity with location/pressure evidence; no diagnosis, zone totals or runtime recommendation.",),
               ("UC ANR microirrigation emitter evaluation and discharge uniformity guidance.", "agro_model/tests/test_emitter_test.py"),
               adapter="drip_emitter_test", command="evaluate-drip-emitters"),
    Capability("soil", "soil_profile", "Layered soil-profile water storage", "experimental",
               ("schema_version", "site", "profile", "layers"), (), ("soil", "irrigation"), (
                   "1–100 contiguous depth layers with supplied volumetric field capacity, wilting point and water readings; no texture defaults.",
                   "Depth-weighted storage within a supplied root depth; missing/rejected rooted readings withhold root water-state totals.",
                   "Separate wet/dry layer departures; no transport simulation, irrigation prescription or automatic state adoption.",),
               ("FAO-56 equations 82/87; UMN Extension depth-zone available-water examples.", "agro_model/tests/test_soil_profile.py"),
               adapter="soil_profile", command="evaluate-soil-profile"),
    Capability("soil", "dual_crop_water", "Dual-coefficient crop demand and surface evaporation", "experimental",
               ("schema_version", "site", "area_m2", "crop", "surface_soil", "initial_state", "conditions", "weather", "days"), ("weather_sources", "rainfall_overrides"),
               ("soil", "irrigation"), (
                   "Daily supplied grass-reference ET, basal coefficients, maximum coefficients, cover/wetting fractions and initialized surface depletion.",
                   "Provider ET/rain records require their exact retained weather_sources snapshots; forecast and historical labels are preserved.",
                   "Early wetting, unstressed basal ET and negligible surface-layer transpiration must be explicit. Rain, runoff and net irrigation are supplied separately.",
                   "Separate surface balance and potential demand only; no root-stress coupling, irrigation prescription, automatic wetting fractions or field validation.",),
               ("FAO-56 (1998), Chapter 7 equations 69, 71, 73–75 and 77–79; Examples 31–35.", "agro_model/tests/test_evaporation.py"),
               adapter="dual_crop_water", command="dual-crop-water"),
    Capability("soil", "coupled_soil_water", "Coupled soil-water replay", "experimental",
               ("schema_version", "site", "area_m2", "crop", "initial_state", "conditions", "weather", "days"), ("weather_sources",),
               ("soil", "irrigation"), (
                   "Explicit contiguous compartments and fixed-area patches; supplied daily grass-reference ET, surface water, crop/root activity and evaporation parameters.",
                   "Drainage, infiltration, ponding, evaporation and root extraction share physical water stores; 1–366 consecutive local days with exact continuation checkpoints.",
                   "Root and maximum evaporation depths must be compartment boundaries; complete initial water/drying states and already adjusted crop coefficients are required.",
                   "Irrigation reaches the soil surface, not net root-zone water. No automatic log substitution, operational state adoption, irrigation prescription or field validation.",),
               ("AquaCrop 7.3 manual §§3.7, 3.9, 3.10; independently implemented experimental composition.",
                "agro_model/tests/test_soil_replay.py", "agro_model/tests/test_soil_water.py"),
               adapter="coupled_soil_water", command="coupled-soil-water"),
    Capability("soil", "surface_cutoff", "Surface-irrigation cutoff timing", "not_implemented", (), (),
               ("soil", "irrigation"), ("Requires measured infiltration and advance/recession behavior.",), ()),
    Capability("soil", "surface_advance", "Sloping surface-irrigation advance prediction", "experimental",
               ("schema_version", "site", "geometry", "hydraulics", "infiltration", "limits", "conditions"), (),
               ("soil", "irrigation"), (
                   "One uniformly sloping furrow or wide rectangular border; explicit constant inflow, hydraulic section, roughness and surface-storage shape factor.",
                   "Two-point FAO-45 volume balances at half/full length; supplied intake envelope begins at zero and advance is limited to at most 1440 minutes.",
                   "Explicit inlet area/velocity limits; no default soil family or erosion threshold. Unresolved/out-of-limit cases withhold the prediction.",
                   "Scenario only: no level basin, unsteady hydraulics, recession, cutoff advice, postadvance distribution or field validation.",),
               ("FAO-45 (1989) §§4.3.4, 5.3.1; published 65-minute advance example.",
                "Bautista et al. (2012), Errors in Infiltration Calculations in Volume-Balance Models.",
                "agro_model/tests/test_surface_advance.py"),
               adapter="surface_advance", command="predict-surface-advance"),
)
_BY_ID = MappingProxyType({(c.production_system, c.task): c for c in _CAPABILITIES})


def get_capability(production_system: str, task: str) -> Capability:
    try:
        return _BY_ID[(production_system, task)]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported capability: {production_system!r}.{task!r}; "
                         "select an explicit production system and listed task") from exc


def capabilities(production_system: str | None = None) -> dict:
    if production_system is not None and production_system not in {c.production_system for c in _CAPABILITIES}:
        raise ValueError(f"unknown production system: {production_system!r}")
    result = {}
    for cap in _CAPABILITIES:
        if production_system is None or cap.production_system == production_system:
            result.setdefault(cap.production_system, {})[cap.task] = cap.describe()
    return result


def run_capability(production_system: str, task: str, inputs: dict) -> dict:
    """Reject unsupported routing before invoking a validator; never substitute engines."""
    cap = get_capability(production_system, task)
    if cap.adapter is None:
        raise ValueError(f"{cap.id} is not implemented: {' '.join(cap.applicability)}")
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be an object")
    unexpected = set(inputs) - set(cap.required_inputs + cap.optional_inputs)
    if unexpected:
        raise ValueError(f"unexpected inputs for {cap.id}: {sorted(unexpected)}")
    missing = cap.missing_inputs(inputs)
    if missing:
        raise ValueError(f"missing inputs for {cap.id}: {', '.join(missing)}")
    from .adapters import ADAPTERS

    # A fixed map, not imports or executable names supplied by the caller.
    result = ADAPTERS[cap.adapter](inputs)
    result["schema_version"] = 1
    result["capability"] = {"id": cap.id, "status": cap.status,
                            "applicability": list(cap.applicability)}
    return result
