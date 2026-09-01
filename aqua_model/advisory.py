"""Deterministic operating advice: the twin's state turned into a PROPOSAL a human approves.

Everything else in `aqua_model` answers "what is happening". This module answers "what should
be done about it", and it is deliberately the only module that does, because the step from a
number to an instruction is exactly where an agriculture assistant can hurt someone.

Four rules make that step honest rather than merely structured.

1. **A proposal, never an action.** Nothing here actuates anything, and nothing downstream
   may either without a human decision recorded first (`agronaut_agent.store.ProposalStore`).
   Agronaut has no hardware control path at all today; when it grows one, the gate is already
   the only door.

2. **Confidence is DERIVED, not asserted.** Every recommendation declares the class of
   evidence that produced it, and the class fixes the ceiling on its confidence
   (`EVIDENCE_CONFIDENCE`). A number the operator titrated this week can support a confident
   instruction. A modelled absolute concentration cannot, and the reason is on the record:
   `scripts/validate_twin.py` scored this twin against held-out nitrate and returned MIXED —
   it tracks the DIRECTION of change on 5 of 7 QC-passing ponds and does not beat a
   linear-trend null on LEVEL. So a rule that fires on a modelled level is capped low, a rule
   that fires on a modelled trend sits in the middle, and only a measurement reaches the top.
   A confidence field that the author picks by feel is decoration; this one is auditable.

3. **When the model is the only witness, measure before acting.** If a serious action is
   triggered by a channel the operator has not measured recently, `recommend` emits a
   `measure_first` recommendation ranked ABOVE it. The twin asking to be checked before it is
   obeyed is the whole difference between a decision-support tool and a confident guess.

4. **No dose or treatment quantity ever leaves here**, the same discipline `triage.py` keeps.
   Recommendations carry operating settings the model actually computes (a ration fraction, a
   temperature target, a harvest) and never an amount of anything to add to water.

Thresholds come from `knowledge/nitrogen_cycle_and_cycling.md` and from each species' own
cited temperature band in `species.py`; a test asserts every `source` is a real file or
module, so a citation cannot rot into a dead reference.

Pure and deterministic, like the rest of `aqua_model/`: no LLM, no network, no I/O, no
imports from `agent/` or `agronaut_agent/`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .production import NOT_MODELLED as PRODUCTION_NOT_MODELLED
from .production import ProductionDay, ProductionState
from .species import FishSpecies

ADVISORY_SCHEMA_VERSION = "1.0.0"

# --- evidence classes -------------------------------------------------------------------
# The ceiling a rule's confidence may reach, by what produced it. These are not tuned; they
# encode the validation verdict in `data/twin_validation.json` (MIXED: direction yes, level
# no) plus the plain fact that an operator's titration kit outranks an uncalibrated model.

MEASURED = "measured"                        # the operator reported this channel recently
MODELLED_DIRECTION = "modelled_direction"    # the twin's TREND, which validation supports
MODELLED_LEVEL = "modelled_level"            # the twin's ABSOLUTE value, which it does not

EVIDENCE_CONFIDENCE: dict[str, float] = {
    MEASURED: 0.90,
    MODELLED_DIRECTION: 0.60,
    MODELLED_LEVEL: 0.40,
}

EVIDENCE_NOTE: dict[str, str] = {
    MEASURED: "you measured this channel yourself within the last "
              "{days} day(s) — the strongest evidence this system has",
    MODELLED_DIRECTION: "the twin's DIRECTION of change, which held-out validation supports "
                        "(5 of 7 ponds); the absolute level is less trustworthy",
    MODELLED_LEVEL: "a MODELLED level, not a measurement — validation showed this twin does "
                    "not beat a linear-trend null on level, so treat it as a prompt to test",
}

# How recently a reading must have been logged for a channel to count as MEASURED.
MEASUREMENT_FRESH_DAYS = 7

# --- urgency ----------------------------------------------------------------------------

URGENCY_ORDER = ("now", "today", "this_week")

# --- cited thresholds -------------------------------------------------------------------
# knowledge/nitrogen_cycle_and_cycling.md, verbatim:
#   "Ammonia (total): ~0 ppm (anything persistently above ~0.5 ppm is a problem)"
#   "Nitrite: ~0 ppm (toxic even at low levels)"
#   "Nitrate: ~5-150 ppm - this is plant food; rising nitrate means the cycle is working"
# The twin carries mg/L as N, which is what a hobby titration kit reads, so the bands map
# directly. The "urgent" tier is one doubling above the "act" tier: the knowledge base does
# not give a second number, and inventing a precise one would be exactly the false precision
# this repo exists to avoid.

_N_SOURCE = "knowledge/nitrogen_cycle_and_cycling.md"

TAN_ACT_MG_L = 0.5
TAN_URGENT_MG_L = 1.0
NO2_ACT_MG_L = 0.5
NO2_URGENT_MG_L = 1.0
NO3_HIGH_MG_L = 150.0
NO3_LOW_MG_L = 5.0

# How far the ration is pulled back when nitrogen is over the band. Feed is the forcing
# function of the whole nitrogen cascade — `twin.excreted_n_g` makes it the ONLY input — so
# cutting it is the one lever the model can honestly claim will lower ammonia. The fractions
# are coarse on purpose: a half ration and a held ration are things an operator can actually
# do with a scoop, and the model cannot resolve finer than that.
RATION_REDUCED = 0.5
RATION_HELD = 0.0

# A feeding pause of 12-24 h is the cited safe action in `triage.py`; longer needs a human.
FEED_PAUSE_MAX_HOURS = 24.0


@dataclass(frozen=True)
class Recommendation:
    """One proposed change, with the evidence that produced it and the check that would
    tell the operator whether it worked.

    `value` is in `unit` and is None for actions that carry no scalar (harvest, inspect).
    `confidence` is never set by a rule directly: `recommend` clamps it to the ceiling of
    its `evidence` class, and a test enforces that.
    """

    action: str
    value: float | None
    unit: str
    why: str
    evidence: str
    confidence: float
    urgency: str
    verify: str
    source: str
    reversible: bool = True

    def __post_init__(self) -> None:
        if self.evidence not in EVIDENCE_CONFIDENCE:
            raise ValueError(f"unknown evidence class {self.evidence!r}")
        if self.urgency not in URGENCY_ORDER:
            raise ValueError(f"unknown urgency {self.urgency!r}")
        ceiling = EVIDENCE_CONFIDENCE[self.evidence]
        if not (0.0 < self.confidence <= ceiling):
            raise ValueError(
                f"{self.action}: confidence {self.confidence} outside (0, {ceiling}] for "
                f"evidence class {self.evidence!r} — confidence is derived, not asserted")


@dataclass(frozen=True)
class Proposal:
    """What the twin proposes, as of a date, pending a human decision.

    A Proposal is inert. It becomes a decision only when something outside the trust zone
    records that a person approved it, and it becomes an ACTION only when a hardware path
    exists to carry it out — which today it does not, and the rendering says so.
    """

    recommendations: tuple[Recommendation, ...]
    as_of: str
    horizon_days: int
    context: str
    measured_channels: tuple[str, ...] = ()
    not_modelled: tuple[str, ...] = PRODUCTION_NOT_MODELLED
    schema_version: str = ADVISORY_SCHEMA_VERSION

    def is_empty(self) -> bool:
        return not self.recommendations


# --- rules ------------------------------------------------------------------------------
# Each rule reads the state (and, where it helps, the forecast) and returns zero or one
# Recommendation. Rules never look at each other; ordering and de-duplication happen once,
# in `recommend`, so a rule stays a statement about the system rather than about the report.


def _evidence_for(channel: str, measured: frozenset[str]) -> str:
    """MEASURED when the operator reported this channel recently, else a modelled class."""
    return MEASURED if channel in measured else MODELLED_LEVEL


def _conf(evidence: str, *, share: float = 1.0) -> float:
    """A rule's confidence: its evidence ceiling, optionally scaled down when the rule
    itself is weaker than its evidence (a trend read off a short forecast, say)."""
    return round(EVIDENCE_CONFIDENCE[evidence] * share, 3)


def _rule_ammonia(state: ProductionState, measured: frozenset[str]) -> Recommendation | None:
    tan = state.nitrogen.tan_mg_l
    if tan < TAN_ACT_MG_L:
        return None
    urgent = tan >= TAN_URGENT_MG_L
    ev = _evidence_for("ammonia_mg_l", measured)
    return Recommendation(
        action="hold_ration" if urgent else "reduce_ration",
        value=RATION_HELD if urgent else RATION_REDUCED,
        unit="fraction of the normal daily ration",
        why=(f"total ammonia is {tan:.2f} mg/L, "
             f"{'at or above' if urgent else 'above'} the {TAN_ACT_MG_L} mg/L the knowledge "
             f"base calls a problem. Feed is the only nitrogen input the model has, so "
             f"cutting the ration is the one lever that lowers this without adding anything "
             f"to the water."),
        evidence=ev,
        confidence=_conf(ev),
        urgency="now" if urgent else "today",
        verify=("re-test ammonia in 24 h; it should stop rising before it starts falling, "
                "because the biofilter has to grow into the load"),
        source=_N_SOURCE,
        reversible=True,
    )


def _rule_nitrite(state: ProductionState, measured: frozenset[str]) -> Recommendation | None:
    no2 = state.nitrogen.no2_mg_l
    if no2 < NO2_ACT_MG_L:
        return None
    urgent = no2 >= NO2_URGENT_MG_L
    ev = _evidence_for("nitrite_mg_l", measured)
    return Recommendation(
        action="increase_water_exchange",
        value=None,
        unit="",
        why=(f"nitrite is {no2:.2f} mg/L and the knowledge base calls nitrite toxic even at "
             f"low levels. Nitrite peaks AFTER ammonia because the nitrite oxidisers double "
             f"more slowly than the ammonia oxidisers, so this is the tail of a load the "
             f"biofilter has not caught up with."
             + (" Exchange dilutes it now while the filter grows." if urgent else "")),
        evidence=ev,
        confidence=_conf(ev),
        urgency="now" if urgent else "today",
        verify="re-test nitrite after the exchange and again the next morning",
        source=_N_SOURCE,
        reversible=True,
    )


def _rule_feed_pause(state: ProductionState, measured: frozenset[str]) -> Recommendation | None:
    """Both nitrogen species over the urgent tier at once is the cycling crash, not a blip."""
    n = state.nitrogen
    if n.tan_mg_l < TAN_URGENT_MG_L or n.no2_mg_l < NO2_URGENT_MG_L:
        return None
    ev = (MEASURED if {"ammonia_mg_l", "nitrite_mg_l"} <= set(measured) else MODELLED_LEVEL)
    return Recommendation(
        action="pause_feeding",
        value=FEED_PAUSE_MAX_HOURS,
        unit="hours (maximum before a human re-decides)",
        why=(f"ammonia {n.tan_mg_l:.2f} mg/L AND nitrite {n.no2_mg_l:.2f} mg/L are both over "
             f"the band — that combination is a biofilter that has lost the load, not a "
             f"single bad reading. Fish tolerate a short fast; they tolerate this water "
             f"less well."),
        evidence=ev,
        confidence=_conf(ev),
        urgency="now",
        verify="test both ammonia and nitrite before resuming feed, and resume at part ration",
        source="aqua_model/triage.py (12-24 h feeding pause is the cited safe action)",
        reversible=True,
    )


def _rule_nitrate_high(state: ProductionState, measured: frozenset[str]) -> Recommendation | None:
    no3 = state.nitrogen.no3_mg_l
    if no3 <= NO3_HIGH_MG_L:
        return None
    ev = _evidence_for("nitrate_mg_l", measured)
    return Recommendation(
        action="increase_plant_uptake",
        value=None,
        unit="",
        why=(f"nitrate is {no3:.0f} mg/L, above the {NO3_HIGH_MG_L:.0f} mg/L top of the "
             f"cited 5-150 band. Nitrate is plant food, so a level this high usually means "
             f"the beds are under-planted or over-fed for their area, not that anything is "
             f"broken. More growing area or a harvest-and-replant is the sink."),
        evidence=ev,
        confidence=_conf(ev),
        urgency="this_week",
        verify="re-test nitrate a week after the beds are replanted",
        source=_N_SOURCE,
        reversible=True,
    )


def _rule_cycle_not_established(state: ProductionState,
                                measured: frozenset[str]) -> Recommendation | None:
    """Ammonia present, nitrate absent: the filter is not converting yet."""
    n = state.nitrogen
    if n.tan_mg_l < TAN_ACT_MG_L or n.no3_mg_l >= NO3_LOW_MG_L:
        return None
    ev = _evidence_for("nitrate_mg_l", measured)
    return Recommendation(
        action="inspect_biofilter",
        value=None,
        unit="",
        why=(f"ammonia is {n.tan_mg_l:.2f} mg/L while nitrate is only {n.no3_mg_l:.1f} mg/L. "
             f"Nitrogen is going in and not coming out the far end, which is what an "
             f"uncycled or crashed biofilter looks like. A biofilter is a population that "
             f"doubles at a rate, so it cannot be fixed today — it has to grow."),
        evidence=ev,
        confidence=_conf(ev),
        urgency="today",
        verify=("watch for nitrate appearing over the next week; that, not a clean ammonia "
                "test, is what says the filter is working"),
        source=_N_SOURCE,
        reversible=True,
    )


def _rule_temperature(state: ProductionState, species: FishSpecies,
                      measured: frozenset[str]) -> Recommendation | None:
    t = state.water_temp_c
    ev = _evidence_for("water_temp_c", measured)
    if t > species.temp_opt_high_c:
        over = t - species.temp_opt_high_c
        critical = t >= species.temp_max_c
        return Recommendation(
            action="add_shade_or_cooling",
            value=round(species.temp_opt_high_c, 1),
            unit="C (target water temperature)",
            why=(f"water is {t:.1f} C, {over:.1f} C above {species.name}'s optimal band "
                 f"({species.temp_opt_low_c:.0f}-{species.temp_opt_high_c:.0f} C"
                 + (f", and at or past its {species.temp_max_c:.0f} C limit" if critical else "")
                 + "). Warm water also holds less oxygen, which the twin does not model."),
            evidence=ev,
            confidence=_conf(ev),
            urgency="now" if critical else "today",
            verify="read water temperature at the hottest part of the afternoon, not at dawn",
            source=f"aqua_model/species.py ({species.name}, source {species.source})",
            reversible=True,
        )
    if t < species.temp_opt_low_c:
        under = species.temp_opt_low_c - t
        critical = t <= species.temp_min_c
        return Recommendation(
            action="add_heat_or_cover",
            value=round(species.temp_opt_low_c, 1),
            unit="C (target water temperature)",
            why=(f"water is {t:.1f} C, {under:.1f} C below {species.name}'s optimal band "
                 f"({species.temp_opt_low_c:.0f}-{species.temp_opt_high_c:.0f} C"
                 + (f", and at or below its {species.temp_min_c:.0f} C limit" if critical else "")
                 + "). Cold fish eat less, so growth slows before anything looks wrong."),
            evidence=ev,
            confidence=_conf(ev),
            urgency="now" if critical else "today",
            verify="read water temperature just before dawn, which is its daily minimum",
            source=f"aqua_model/species.py ({species.name}, source {species.source})",
            reversible=True,
        )
    return None


def _rule_harvest_fish(state: ProductionState, species: FishSpecies,
                       measured: frozenset[str]) -> Recommendation | None:
    target_g = species.harvest_weight_kg * 1000.0
    if state.fish.mean_weight_g < target_g:
        return None
    ev = MEASURED if "fish_avg_weight_g" in measured else MODELLED_LEVEL
    return Recommendation(
        action="harvest_fish",
        value=round(state.fish.biomass_kg(), 1),
        unit="kg standing biomass now in the tanks",
        why=(f"mean weight is {state.fish.mean_weight_g:.0f} g against a "
             f"{target_g:.0f} g harvest target for {species.name}. Past this point the feed "
             f"conversion ratio worsens, so every extra day costs more feed per kilo gained."),
        evidence=ev,
        confidence=_conf(ev),
        urgency="this_week",
        verify="weigh a sample of at least 20 fish before committing to a harvest date",
        source=f"aqua_model/species.py ({species.name}, source {species.source})",
        reversible=False,
    )


def _rule_forecast_nitrogen(trajectory: list[ProductionDay],
                            state: ProductionState) -> Recommendation | None:
    """A peak the twin sees COMING. Capped at the direction ceiling and scaled down again,
    because this is a modelled trend read off a short forecast, which is the weakest thing
    this module is willing to speak about at all."""
    if len(trajectory) < 2:
        return None
    peak_tan = max(d.state.nitrogen.tan_mg_l for d in trajectory)
    peak_no2 = max(d.state.nitrogen.no2_mg_l for d in trajectory)
    now_tan, now_no2 = state.nitrogen.tan_mg_l, state.nitrogen.no2_mg_l
    crosses_tan = peak_tan >= TAN_ACT_MG_L > now_tan
    crosses_no2 = peak_no2 >= NO2_ACT_MG_L > now_no2
    if not (crosses_tan or crosses_no2):
        return None
    which = "ammonia" if crosses_tan else "nitrite"
    peak = peak_tan if crosses_tan else peak_no2
    day = next(i for i, d in enumerate(trajectory, 1)
               if (d.state.nitrogen.tan_mg_l if crosses_tan else d.state.nitrogen.no2_mg_l) >= peak)
    return Recommendation(
        action="plan_ration_reduction",
        value=RATION_REDUCED,
        unit="fraction of the normal daily ration, if the peak materialises",
        why=(f"the forecast crosses the {which} band in about {day} day(s), peaking near "
             f"{peak:.2f} mg/L from {now_tan if crosses_tan else now_no2:.2f} mg/L today, "
             f"driven by the weather at your site. This is the twin's trend, which is the "
             f"part validation supports; the level it names is not."),
        evidence=MODELLED_DIRECTION,
        confidence=_conf(MODELLED_DIRECTION, share=0.75),
        urgency="this_week",
        verify=f"test {which} on day {max(1, day - 1)} — if it has not moved, ignore this",
        source=_N_SOURCE,
        reversible=True,
    )


# Channel each rule reads, so `measure_first` can name what to test.
_RULE_CHANNEL = {
    "reduce_ration": ("ammonia_mg_l", "ammonia"),
    "hold_ration": ("ammonia_mg_l", "ammonia"),
    "increase_water_exchange": ("nitrite_mg_l", "nitrite"),
    "pause_feeding": ("ammonia_mg_l", "ammonia and nitrite"),
    "increase_plant_uptake": ("nitrate_mg_l", "nitrate"),
    "inspect_biofilter": ("nitrate_mg_l", "ammonia and nitrate"),
    "add_shade_or_cooling": ("water_temp_c", "water temperature"),
    "add_heat_or_cover": ("water_temp_c", "water temperature"),
    "harvest_fish": ("fish_avg_weight_g", "a sample weight"),
}


def _measure_first(triggers: list[Recommendation]) -> Recommendation | None:
    """The rule that makes this honest: when something urgent rests only on the model, ask
    to be checked before being obeyed.

    Fires for `now`/`today` actions whose evidence is a modelled LEVEL — the class the
    twin's own validation says is its weakest. Not for `this_week` actions (there is time
    to measure anyway) and never for a measured channel (there is nothing left to check).
    """
    modelled = [r for r in triggers
                if r.evidence == MODELLED_LEVEL and r.urgency in ("now", "today")]
    if not modelled:
        return None
    names = []
    for r in modelled:
        _chan, label = _RULE_CHANNEL.get(r.action, ("", r.action))
        if label not in names:
            names.append(label)
    what = ", ".join(names)
    return Recommendation(
        action="measure_first",
        value=None,
        unit="",
        why=(f"every urgent item below rests on a MODELLED level, not on anything you "
             f"measured. This twin was scored against held-out data and the verdict was "
             f"MIXED: it follows the direction of change but does not beat a linear-trend "
             f"null on level. Test {what} before you change the feeding of live fish."),
        evidence=MODELLED_LEVEL,
        confidence=EVIDENCE_CONFIDENCE[MODELLED_LEVEL],
        urgency="now",
        verify="log the result with /log — it also pulls the twin back toward reality",
        source="data/twin_validation.json (scripts/validate_twin.py)",
        reversible=True,
    )


def recommend(state: ProductionState, species: FishSpecies, *,
              trajectory: list[ProductionDay] | None = None,
              measured_channels: frozenset[str] | set[str] | None = None,
              as_of: str = "", horizon_days: int = 0) -> Proposal:
    """Turn the twin's state into a ranked, cited, human-approvable Proposal.

    `measured_channels` names the channels the operator reported recently (see
    MEASUREMENT_FRESH_DAYS); passing them is what lets a recommendation reach the top
    confidence class. Omitting them is safe and simply means everything is treated as
    modelled, which is the honest default for a system nobody has tested this week.

    Deterministic: same inputs, same proposal, byte for byte.
    """
    measured = frozenset(measured_channels or ())
    traj = list(trajectory or [])

    found = [r for r in (
        _rule_feed_pause(state, measured),
        _rule_ammonia(state, measured),
        _rule_nitrite(state, measured),
        _rule_cycle_not_established(state, measured),
        _rule_nitrate_high(state, measured),
        _rule_temperature(state, species, measured),
        _rule_harvest_fish(state, species, measured),
        _rule_forecast_nitrogen(traj, state),
    ) if r is not None]

    # A held ration already supersedes a reduced one; do not tell an operator both.
    if any(r.action == "pause_feeding" for r in found):
        found = [r for r in found if r.action not in ("reduce_ration", "hold_ration")]

    gate = _measure_first(found)
    if gate is not None:
        found.insert(0, gate)

    ranked = tuple(sorted(
        found,
        key=lambda r: (r.action != "measure_first",
                       URGENCY_ORDER.index(r.urgency),
                       -r.confidence,
                       r.action),
    ))
    return Proposal(
        recommendations=ranked,
        as_of=as_of,
        horizon_days=horizon_days,
        context=_context_line(state, species),
        measured_channels=tuple(sorted(measured)),
    )


def _context_line(state: ProductionState, species: FishSpecies) -> str:
    n = state.nitrogen
    return (f"{state.fish.count} {species.name} @ {state.fish.mean_weight_g:.0f} g "
            f"({state.fish.biomass_kg():.1f} kg) · water {state.water_temp_c:.1f} C · "
            f"NH3 {n.tan_mg_l:.2f} / NO2 {n.no2_mg_l:.2f} / NO3 {n.no3_mg_l:.0f} mg/L")


def with_confidence_floor(proposal: Proposal, floor: float) -> Proposal:
    """Drop recommendations below a confidence floor. For a caller that wants only the
    strong ones; the default is to show everything, because a weak recommendation that
    SAYS it is weak is information, and hiding it is how a tool starts lying by omission."""
    kept = tuple(r for r in proposal.recommendations if r.confidence >= floor)
    return replace(proposal, recommendations=kept)


# --- rendering --------------------------------------------------------------------------

ACTION_LABEL = {
    "measure_first": "TEST THE WATER BEFORE ACTING",
    "reduce_ration": "Cut the feed ration by half",
    "hold_ration": "Hold the feed (no ration today)",
    "pause_feeding": "Stop feeding",
    "increase_water_exchange": "Exchange water",
    "increase_plant_uptake": "Plant more, or harvest and replant",
    "inspect_biofilter": "Inspect the biofilter",
    "add_shade_or_cooling": "Shade or cool the water",
    "add_heat_or_cover": "Heat or cover the water",
    "harvest_fish": "Harvest fish",
    "plan_ration_reduction": "Plan a ration cut",
}

_URGENCY_LABEL = {"now": "NOW", "today": "today", "this_week": "this week"}

NO_ACTUATION_NOTICE = (
    "Agronaut has NO connection to your equipment. Approving records your decision and the "
    "reasons for it; it does not move a valve, a pump, or a heater. Every action below is "
    "something you do by hand."
)


def format_proposal(proposal: Proposal, *, numbered: bool = True) -> str:
    """Render a Proposal as cited plain text for a phone, a terminal, or a tool result.

    The numbering is the approval handle: `/approve 1 3` refers to these numbers, so the
    order rendered here and the order stored must be the same order. `recommend` sorts
    once and everything downstream preserves it.
    """
    if proposal.is_empty():
        return ("PROPOSAL: nothing to do. Every channel the twin models is inside its cited "
                "band, so it has no action to propose.\n"
                f"State: {proposal.context}\n"
                "That is not a clean bill of health: dissolved oxygen and pH are not "
                "modelled at all, and they are what most systems actually fail on.")

    head = ["PROPOSAL — " + str(len(proposal.recommendations)) + " item(s) for your approval."
            + (f" As of {proposal.as_of}." if proposal.as_of else "")]
    head.append("State: " + proposal.context)
    if proposal.measured_channels:
        head.append("Measured recently: " + ", ".join(proposal.measured_channels))
    else:
        head.append("Measured recently: nothing — everything below is modelled.")
    head.append("")
    head.append(NO_ACTUATION_NOTICE)

    lines = list(head)
    for i, r in enumerate(proposal.recommendations, 1):
        tag = f"{i}. " if numbered else "- "
        label = ACTION_LABEL.get(r.action, r.action)
        val = f" → {r.value:g} {r.unit}" if r.value is not None else ""
        lines.append("")
        lines.append(f"{tag}[{_URGENCY_LABEL[r.urgency]}] {label}{val}")
        lines.append(f"   why: {r.why}")
        lines.append(f"   confidence: {r.confidence:.2f} ({r.evidence}) — "
                     + EVIDENCE_NOTE[r.evidence].format(days=MEASUREMENT_FRESH_DAYS))
        lines.append(f"   verify: {r.verify}")
        lines.append(f"   source: {r.source}")
        if not r.reversible:
            lines.append("   NOTE: this one cannot be undone.")

    lines.append("")
    lines.append("Reply /approve <numbers> or /reject <numbers>. Nothing happens until you do.")
    lines.append("")
    lines.append("NOT modelled by the twin behind this proposal:")
    for item in proposal.not_modelled:
        lines.append(f"  - {item}")
    return "\n".join(lines)


def to_dict(proposal: Proposal) -> dict:
    """Serialize for storage and for any surface that wants structure rather than prose —
    the same split `scene3d.py` keeps between the scene and whatever draws it."""
    return {
        "schema_version": proposal.schema_version,
        "as_of": proposal.as_of,
        "horizon_days": proposal.horizon_days,
        "context": proposal.context,
        "measured_channels": list(proposal.measured_channels),
        "recommendations": [
            {"action": r.action, "value": r.value, "unit": r.unit, "why": r.why,
             "evidence": r.evidence, "confidence": r.confidence, "urgency": r.urgency,
             "verify": r.verify, "source": r.source, "reversible": r.reversible}
            for r in proposal.recommendations
        ],
        "not_modelled": list(proposal.not_modelled),
    }
