"""Deterministic visual-symptom triage — the trust-zone half of the vision path.

Until now a photo produced PROSE: a vision model's description, which the LLM reasoned over
freely. The sizing path has never worked that way — every number it emits is traceable to a
cited coefficient. This module gives the vision path the same property for *diagnosis*: a
set of observed visual features maps, by a fixed table, to a ranked DIFFERENTIAL whose every
entry names the knowledge-base document it came from.

Three rules make it honest rather than merely structured:

1. **It returns a differential, never a verdict.** A photo cannot distinguish iron deficiency
   from pH lockout — they look identical. So the output is a ranked list plus the CHECKS that
   would actually discriminate between the entries. A single confident answer from an image
   would be a lie about what an image can tell you.
2. **Environment before pathogen, because the knowledge base says so.** Ordering is not our
   invention: `knowledge/plant_nutrient_deficiencies.md` states "The golden rule: check pH
   first — most 'deficiencies' in a cycled system are lockout, not absence", and
   `knowledge/fish_disease_and_treatment.md` opens with "Most fish disease is a
   water-quality problem first ... rule out low DO, ammonia/nitrite, pH swings, and
   temperature stress" before treating for a pathogen. The `priority` field encodes exactly
   that, so the cheap, safe, most-likely cause is always considered first.
3. **No dose or treatment quantity ever leaves here.** This model cites no dosing
   coefficient, so it states no amount — the same discipline that keeps
   `sanitize_observation` stripping doses out of the vision model's output. Cited *reference
   bands* do appear (the pH 6.0–7.0 availability window, a 12–24 h feeding pause) because
   they come verbatim from the knowledge base and are not instructions to add anything; a
   test enforces the absence of dosing units.

Pure: no LLM, no network, no I/O, and no imports from `agent/` or `srcs/` (the trust zone's
dependency rule). Every `source` is a real file under `knowledge/`; a test asserts that, so a
citation cannot rot into a dead reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .validate import ValidationError

# --- feature vocabulary -----------------------------------------------------------------
# Categorical by design. A photo can support "the older leaves are yellow at the margins";
# it cannot support "potassium is 12 mg/L". Keeping every feature categorical means a
# mis-read feature can only select a different differential branch — it can never inject a
# number into the sizing engine.

SUBJECTS = frozenset({"plant", "fish", "water", "roots", "equipment"})
LEAF_AGES = frozenset({"old", "new", "both", "unknown"})
LEAF_PATTERNS = frozenset({
    "interveinal",      # yellow leaf, green veins
    "margin_scorch",    # browning/scorch at the leaf edge
    "whole_pale",       # uniform pale green over the whole leaf
    "tip_burn",         # burnt or distorted growing tip
    "stippled",         # fine speckling
    "spots",
    "holes",            # chewed
    "powder",           # white surface bloom
    "webbing",
})
COLOURS = frozenset({"yellow", "brown", "white", "purple", "dark_green", "red"})
ROOT_STATES = frozenset({"brown_slimy", "white_healthy", "unknown"})
WATER_STATES = frozenset({"clear", "green", "cloudy", "brown", "unknown"})
FISH_BEHAVIOURS = frozenset({
    "gasping_surface", "lethargic", "not_eating", "flashing", "clamped_fins",
})
FISH_BODY = frozenset({"white_spots", "lesion", "frayed_fins", "swollen", "cotton_tufts"})
PESTS = frozenset({"aphids", "whiteflies", "mites", "gnats"})

# Knowledge-base documents cited below. Every one must exist under knowledge/.
_KB_DEFICIENCY = "knowledge/plant_nutrient_deficiencies.md"
_KB_CHEATSHEET = "knowledge/plant_deficiency_cheatsheet.md"
_KB_PESTS = "knowledge/plant_pests_and_ipm.md"
_KB_FISH_DISEASE = "knowledge/fish_disease_and_treatment.md"
_KB_FISH_STRESS = "knowledge/fish_stress_diagnosis.md"
_KB_DO = "knowledge/dissolved_oxygen_and_aeration.md"
_KB_ALGAE = "knowledge/algae_control.md"
_KB_PH = "knowledge/ph_and_alkalinity.md"
_KB_SOLIDS = "knowledge/solids_and_mineralization.md"

# Priority bands. Lower is considered first — see rule 2 in the module docstring.
_P_ENVIRONMENT = 10   # water quality / oxygen: cheap to check, dangerous to miss
_P_AVAILABILITY = 20  # pH lockout: the nutrient is present but unavailable
_P_SUPPLY = 30        # an actual nutrient shortfall
_P_PATHOGEN = 40      # pest, parasite, or disease


@dataclass(frozen=True)
class ObservationFeatures:
    """What a photo can actually support. Built by the validation gate
    (`validate_observation_features`), never populated straight from model output."""

    subject: tuple[str, ...] = ()
    leaf_age: str = "unknown"
    leaf_pattern: tuple[str, ...] = ()
    colour: tuple[str, ...] = ()
    root_state: str = "unknown"
    water_state: str = "unknown"
    fish_behaviour: tuple[str, ...] = ()
    fish_body: tuple[str, ...] = ()
    pests_visible: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """Nothing usable was observed — callers should ask for a better photo or a
        description rather than produce a differential from nothing."""
        return not any((self.subject, self.leaf_pattern, self.colour, self.fish_behaviour,
                        self.fish_body, self.pests_visible)) and \
            self.root_state == "unknown" and self.water_state == "unknown"


@dataclass(frozen=True)
class TriageCandidate:
    """One entry in the differential. `checks` is the point of the whole exercise: it is what
    the operator does next to tell this candidate apart from its neighbours."""

    cause: str
    because: str
    checks: tuple[str, ...]
    safe_actions: tuple[str, ...]
    source: str
    priority: int


@dataclass(frozen=True)
class TriageResult:
    candidates: tuple[TriageCandidate, ...] = ()
    not_modeled: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not self.candidates

    def sources(self) -> tuple[str, ...]:
        """Distinct cited documents, in first-appearance order."""
        seen: list[str] = []
        for c in self.candidates:
            if c.source not in seen:
                seen.append(c.source)
        return tuple(seen)


@dataclass(frozen=True)
class _Rule:
    matches: Callable[[ObservationFeatures], bool]
    candidate: TriageCandidate


def _has(values: tuple[str, ...], *wanted: str) -> bool:
    return any(w in values for w in wanted)


# --- the table --------------------------------------------------------------------------
# Each rule is (predicate, candidate). Predicates are deliberately narrow: a rule fires only
# on the feature combination its source document actually describes.

_RULES: tuple[_Rule, ...] = (
    # -- plant: interveinal chlorosis on NEW growth ------------------------------------
    # The KB is explicit that these two are the same picture and that pH comes first.
    _Rule(
        matches=lambda f: "interveinal" in f.leaf_pattern and f.leaf_age in {"new", "both"},
        candidate=TriageCandidate(
            cause="pH lockout (iron present but unavailable)",
            because="interveinal yellowing on new growth — the classic lockout picture, "
                    "identical to a true iron shortfall in a photo",
            checks=("pH trend over several days, not a single reading — lockout sets in above "
                    "roughly 7.0",
                    "whether anything recently raised pH (top-up water, a carbonate buffer, "
                    "new media)"),
            safe_actions=("confirm pH is in the 6.0–7.0 band where micronutrients stay "
                          "available before adding any supplement",),
            source=_KB_DEFICIENCY,
            priority=_P_AVAILABILITY,
        ),
    ),
    _Rule(
        matches=lambda f: "interveinal" in f.leaf_pattern and f.leaf_age in {"new", "both"},
        candidate=TriageCandidate(
            cause="Iron (Fe) deficiency",
            because="interveinal yellowing on new/young leaves — the most common aquaponic "
                    "deficiency",
            checks=("pH first (see above) — correcting it often resolves the symptom with no "
                    "supplement at all",
                    "whether chelated iron has ever been dosed, and which chelate"),
            safe_actions=("if pH is already in band, dose CHELATED iron (not raw iron "
                          "sulphate); Fe-DTPA holds to about pH 7.0–7.5, Fe-EDDHA above that",),
            source=_KB_DEFICIENCY,
            priority=_P_SUPPLY,
        ),
    ),
    # -- plant: OLD leaves, margins -----------------------------------------------------
    _Rule(
        matches=lambda f: "margin_scorch" in f.leaf_pattern and f.leaf_age in {"old", "both"},
        candidate=TriageCandidate(
            cause="Potassium (K) deficiency",
            because="scorch or browning at the margins of older leaves — fish feed is low in "
                    "potassium, so this is an expected aquaponic shortfall",
            checks=("stem strength and fruit set, which fail alongside margin scorch",
                    "whether any potassium-bearing buffer is in use"),
            safe_actions=("supplement via potassium bicarbonate, which doubles as a pH buffer "
                          "— you address two things at once",),
            source=_KB_DEFICIENCY,
            priority=_P_SUPPLY,
        ),
    ),
    _Rule(
        matches=lambda f: "whole_pale" in f.leaf_pattern and f.leaf_age in {"old", "both"},
        candidate=TriageCandidate(
            cause="Nitrogen (N) deficiency",
            because="uniform pale green across the whole/older plant — common in lightly "
                    "stocked or under-fed systems",
            checks=("stocking and feed rate — nitrogen is the cycle's end product, so a fed "
                    "system usually has plenty",
                    "nitrate level if a test is available"),
            safe_actions=("feed more / add fish rather than dumping fertiliser salts that may "
                          "harm the fish",),
            source=_KB_DEFICIENCY,
            priority=_P_SUPPLY,
        ),
    ),
    # -- plant: NEW growth distorted ----------------------------------------------------
    _Rule(
        matches=lambda f: "tip_burn" in f.leaf_pattern and f.leaf_age in {"new", "both"},
        candidate=TriageCandidate(
            cause="Calcium (Ca) deficiency",
            because="burnt or distorted new growth / tip burn",
            checks=("airflow and transpiration — this is often an uptake problem rather than a "
                    "supply one",
                    "whether a calcium-bearing pH base is in use"),
            safe_actions=("calcium carbonate or hydroxide used as the pH base supplies calcium",
                          "improve airflow around fruiting plants"),
            source=_KB_DEFICIENCY,
            priority=_P_SUPPLY,
        ),
    ),
    # -- plant: roots -------------------------------------------------------------------
    _Rule(
        matches=lambda f: f.root_state == "brown_slimy",
        candidate=TriageCandidate(
            cause="Root rot / Pythium",
            because="brown, slimy roots — the described picture for root disease",
            checks=("smell: foul is characteristic",
                    "root-zone oxygen and water temperature, the usual underlying causes"),
            safe_actions=("raise dissolved oxygen at the root zone",
                          "cool the water if it is running warm",
                          "remove affected plants and dead tissue rather than leaving it in "
                          "the system"),
            source=_KB_PESTS,
            priority=_P_ENVIRONMENT,
        ),
    ),
    # A yellowing plant with healthy-looking roots still has flow/oxygen on the list: the
    # lettuce-yellowing differential names low flow and poor root oxygen explicitly.
    _Rule(
        matches=lambda f: (_has(f.leaf_pattern, "interveinal", "whole_pale")
                           or "yellow" in f.colour) and "plant" in f.subject,
        candidate=TriageCandidate(
            cause="Low flow / poor root oxygen",
            because="yellowing foliage is also the signature of a delivery problem, not only "
                    "a nutrient one",
            checks=("flow consistency to every channel or bed, not just the first one",
                    "pump output and any partial blockage",
                    "root appearance: white and firm versus brown and slimy"),
            safe_actions=("improve aeration",
                          "check and restore steady flow to all grow channels"),
            source=_KB_CHEATSHEET,
            priority=_P_ENVIRONMENT,
        ),
    ),
    # -- plant: pests and surface disease ------------------------------------------------
    _Rule(
        matches=lambda f: "powder" in f.leaf_pattern,
        candidate=TriageCandidate(
            cause="Powdery mildew",
            because="white powdery bloom on the leaf surface",
            checks=("airflow and plant spacing",
                    "whether foliage is being wetted"),
            safe_actions=("improve airflow", "avoid wetting the foliage",
                          "remove affected leaves"),
            source=_KB_PESTS,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: _has(f.leaf_pattern, "webbing", "stippled") or "mites" in f.pests_visible,
        candidate=TriageCandidate(
            cause="Spider mites",
            because="fine webbing and stippled/speckled leaves",
            checks=("leaf undersides with a hand lens",
                    "air temperature and humidity — mites thrive hot and dry"),
            safe_actions=("predatory mites are the aquaponics-friendly control",
                          "never spray anything that can reach the water"),
            source=_KB_PESTS,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: "aphids" in f.pests_visible,
        candidate=TriageCandidate(
            cause="Aphids",
            because="visible clusters, typically on new growth and leaf undersides",
            checks=("new growth and undersides for clusters and sticky honeydew",),
            safe_actions=("blast them off with a spray of water; hand-pick",
                          "ladybugs or lacewings as biological control",
                          "no pesticide that can reach the water — pyrethrin is highly toxic "
                          "to fish"),
            source=_KB_PESTS,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: "whiteflies" in f.pests_visible,
        candidate=TriageCandidate(
            cause="Whiteflies",
            because="tiny white flies that puff up when the plant is disturbed",
            checks=("disturb the canopy and watch for a white cloud",),
            safe_actions=("yellow sticky traps", "netting or screens on vents"),
            source=_KB_PESTS,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: "gnats" in f.pests_visible,
        candidate=TriageCandidate(
            cause="Fungus gnats",
            because="small black flies around the media",
            checks=("media surface and root zone for larvae",),
            safe_actions=("sticky traps",
                          "Bacillus thuringiensis israelensis (Bti) for the larvae — "
                          "bacterial, considered fish-safe as directed"),
            source=_KB_PESTS,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: "holes" in f.leaf_pattern,
        candidate=TriageCandidate(
            cause="Chewing pest damage",
            because="holes bitten through the leaf",
            checks=("undersides and growing points for caterpillars or beetles, especially "
                    "at dusk",),
            safe_actions=("hand-pick",
                          "Bt kurstaki for caterpillars — bacterial, fish-safe as directed",
                          "work down the IPM ladder: cultural, physical, biological, and only "
                          "then chemical, applied foliar and shielded from the water"),
            source=_KB_PESTS,
            priority=_P_PATHOGEN,
        ),
    ),
    # -- fish: environment first ---------------------------------------------------------
    _Rule(
        matches=lambda f: "gasping_surface" in f.fish_behaviour,
        candidate=TriageCandidate(
            cause="Low dissolved oxygen",
            because="fish at the surface gasping — the textbook signature of low DO, "
                    "especially in the morning",
            checks=("time of day: a dawn low points hard at DO",
                    "aeration actually running, and whether there is any backup",
                    "surface agitation — gas exchange happens at the surface",
                    "water temperature: warm water holds less oxygen"),
            safe_actions=("add or increase aeration immediately",
                          "make the water return break the surface",
                          "stop feeding until the fish are settled"),
            source=_KB_DO,
            priority=_P_ENVIRONMENT,
        ),
    ),
    _Rule(
        matches=lambda f: _has(f.fish_behaviour, "lethargic", "not_eating"),
        candidate=TriageCandidate(
            cause="Water-quality stress (DO, ammonia/nitrite, or temperature)",
            because="fish moving slowly or off their feed — the general stress picture, which "
                    "the knowledge base says to resolve before considering any pathogen",
            checks=("dissolved oxygen", "ammonia and nitrite if a test is available",
                    "water temperature against the species' range",
                    "anything that changed recently: water change, chemicals, pump failure, "
                    "power cut"),
            safe_actions=("increase aeration",
                          "check circulation and pump output",
                          "reduce feeding for 12–24 hours while they are stressed"),
            source=_KB_FISH_STRESS,
            priority=_P_ENVIRONMENT,
        ),
    ),
    # -- fish: pathogen candidates, deliberately BELOW the environment ones --------------
    _Rule(
        matches=lambda f: "white_spots" in f.fish_body or "flashing" in f.fish_behaviour,
        candidate=TriageCandidate(
            cause="Ich / white spot (external parasite)",
            because="white salt-grain spots and/or flashing — but rule out water quality "
                    "first: a stressed fish is what gets sick",
            checks=("spots like fine salt grains on skin and fins, versus none at all — "
                    "flashing without spots points to flukes instead",
                    "DO, ammonia/nitrite and temperature before treating anything"),
            safe_actions=("treat in a SEPARATE quarantine tank, never the main system — "
                          "copper kills plants, antibiotics wipe out the biofilter, and "
                          "formalin and malachite green are not food-safe on edible crops",
                          "for ich specifically, a gradual temperature rise within the "
                          "species' safe range shortens the parasite's life cycle"),
            source=_KB_FISH_DISEASE,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: "clamped_fins" in f.fish_behaviour and "white_spots" not in f.fish_body,
        candidate=TriageCandidate(
            cause="Flukes / external parasites",
            because="clamped fins and flashing with no visible spots",
            checks=("excess mucus", "no salt-grain spots, which would point to ich instead"),
            safe_actions=("correct the environment first",
                          "treat in a separate quarantine tank if it persists"),
            source=_KB_FISH_DISEASE,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: _has(f.fish_body, "lesion", "frayed_fins"),
        candidate=TriageCandidate(
            cause="Bacterial infection (fin rot, ulcers, columnaris)",
            because="ragged fins, open sores or red patches — which typically FOLLOW poor "
                    "water quality rather than arriving on their own",
            checks=("water quality history — this usually follows a lapse",
                    "crowding and overfeeding"),
            safe_actions=("fix the water first",
                          "isolate affected fish in a quarantine tank; antibiotics in the "
                          "main system can lose you the whole nitrogen cycle overnight"),
            source=_KB_FISH_DISEASE,
            priority=_P_PATHOGEN,
        ),
    ),
    _Rule(
        matches=lambda f: "cotton_tufts" in f.fish_body,
        candidate=TriageCandidate(
            cause="Fungal infection (Saprolegnia)",
            because="cotton-wool white or grey tufts, usually on already-injured or stressed "
                    "fish",
            checks=("whether the fish was injured or stressed first — the fungus is normally "
                    "secondary",),
            safe_actions=("quarantine tank for any treatment",
                          "remove the underlying stressor"),
            source=_KB_FISH_DISEASE,
            priority=_P_PATHOGEN,
        ),
    ),
    # -- water ---------------------------------------------------------------------------
    _Rule(
        matches=lambda f: f.water_state == "green",
        candidate=TriageCandidate(
            cause="Green-water algae bloom",
            because="pea-soup green water — single-celled algae, which means light is reaching "
                    "exposed water",
            checks=("where light hits open water: tanks, sumps, channel gaps, filter tops",
                    "dawn DO, because a heavy bloom consumes oxygen overnight and can crash it"),
            safe_actions=("cover exposed water and shade open channels — light is the one "
                          "lever you control",
                          "increase aeration while the bloom persists",
                          "remove algae physically rather than killing it in place; a big "
                          "die-off rots and spikes ammonia"),
            source=_KB_ALGAE,
            priority=_P_ENVIRONMENT,
        ),
    ),
    _Rule(
        matches=lambda f: f.water_state in {"cloudy", "brown"},
        candidate=TriageCandidate(
            cause="Suspended solids / poor solids capture",
            because="cloudy or brown water rather than green",
            checks=("solids capture and filter condition",
                    "feed rate and uneaten feed",
                    "whether anything was recently disturbed or backwashed"),
            safe_actions=("clean or improve solids capture",
                          "avoid overfeeding while it clears"),
            source=_KB_SOLIDS,
            priority=_P_ENVIRONMENT,
        ),
    ),
)

# Always stated, whatever the observation — the honesty layer the rest of the model carries.
_NOT_MODELED: tuple[str, ...] = (
    "A photograph is not a measurement. Nothing here reads pH, dissolved oxygen, ammonia, "
    "nitrate, or any nutrient concentration — those come from your test kit, not the image.",
    "Whether the photo shows a representative part of your system, or the one bad plant.",
    "Cultivar, growth stage, season, light level, and any recent change to the system — none "
    "of which are visible in an image.",
    "Any dose or treatment quantity. This model cites no dosing coefficient, so it states no "
    "amount; the safe actions above are qualitative on purpose.",
    "Co-occurring causes. Two of these candidates can be true at once, and a photo cannot "
    "separate them.",
)


def validate_observation_features(
    subject=(), leaf_age="unknown", leaf_pattern=(), colour=(), root_state="unknown",
    water_state="unknown", fish_behaviour=(), fish_body=(), pests_visible=(),
) -> ObservationFeatures:
    """The trust gate for triage input.

    Unknown tokens are REJECTED rather than ignored: this function is reachable from an LLM
    tool call, and silently dropping a token the caller believed in would produce a
    confident differential built on less evidence than the caller thinks it used. Every
    error names the allowed vocabulary so the caller can correct itself, and all problems are
    collected before raising — matching ValidationError's contract elsewhere in the gate."""
    errors: list[str] = []

    def _seq(name: str, values, allowed: frozenset) -> tuple[str, ...]:
        if values is None:
            return ()
        if isinstance(values, str):
            values = [values]
        try:
            items = [str(v).strip().lower() for v in values]
        except TypeError:
            errors.append(f"{name} must be a string or a list of strings")
            return ()
        bad = sorted({v for v in items if v and v not in allowed})
        if bad:
            errors.append(f"unknown {name} value(s) {bad}. Allowed: {sorted(allowed)}")
        out: list[str] = []                      # de-duplicate, preserve order
        for v in items:
            if v and v in allowed and v not in out:
                out.append(v)
        return tuple(out)

    def _one(name: str, value, allowed: frozenset) -> str:
        v = str(value or "unknown").strip().lower()
        if v not in allowed:
            errors.append(f"unknown {name} value {v!r}. Allowed: {sorted(allowed)}")
            return "unknown"
        return v

    features = ObservationFeatures(
        subject=_seq("subject", subject, SUBJECTS),
        leaf_age=_one("leaf_age", leaf_age, LEAF_AGES),
        leaf_pattern=_seq("leaf_pattern", leaf_pattern, LEAF_PATTERNS),
        colour=_seq("colour", colour, COLOURS),
        root_state=_one("root_state", root_state, ROOT_STATES),
        water_state=_one("water_state", water_state, WATER_STATES),
        fish_behaviour=_seq("fish_behaviour", fish_behaviour, FISH_BEHAVIOURS),
        fish_body=_seq("fish_body", fish_body, FISH_BODY),
        pests_visible=_seq("pests_visible", pests_visible, PESTS),
    )
    if errors:
        raise ValidationError(errors)
    return features


def triage_symptoms(features: ObservationFeatures) -> TriageResult:
    """Map observed features to a ranked differential.

    Candidates are ordered environment → availability → supply → pathogen (the knowledge
    base's own priority), then by first appearance within a band. Duplicate causes collapse.
    An observation with nothing usable in it returns an EMPTY result — the caller should ask
    for a better photo rather than present a differential built on nothing."""
    if features.is_empty():
        return TriageResult(candidates=(), not_modeled=_NOT_MODELED)

    matched: list[TriageCandidate] = []
    seen: set[str] = set()
    for rule in _RULES:
        try:
            hit = bool(rule.matches(features))
        except Exception:  # a predicate must never take down a turn
            hit = False
        if hit and rule.candidate.cause not in seen:
            seen.add(rule.candidate.cause)
            matched.append(rule.candidate)

    matched.sort(key=lambda c: c.priority)   # stable: ties keep table order
    return TriageResult(candidates=tuple(matched), not_modeled=_NOT_MODELED)


def format_triage(result: TriageResult) -> str:
    """Render a differential as cited plain text for the agent turn.

    Deliberately spells out that this is a differential and names the discriminating checks,
    so neither the model nor the reader can mistake the first entry for a diagnosis."""
    if result.is_empty():
        return ("VISUAL_TRIAGE: nothing diagnostic identified in the described observation. "
                "Ask for a closer photo of the affected part, or for what the operator sees.")

    lines = [f"VISUAL_TRIAGE — a DIFFERENTIAL of {len(result.candidates)} candidate(s), "
             "most-likely-and-cheapest-to-check first. This is NOT a diagnosis: a photo "
             "cannot separate these, the checks below can."]
    for i, c in enumerate(result.candidates, 1):
        lines.append(f"\n{i}. {c.cause}")
        lines.append(f"   why: {c.because}")
        lines.append("   check: " + "; ".join(c.checks))
        lines.append("   safe actions: " + "; ".join(c.safe_actions))
        lines.append(f"   source: {c.source}")
    lines.append("\nNOT modeled by this triage:")
    for item in result.not_modeled:
        lines.append(f"  - {item}")
    return "\n".join(lines)
