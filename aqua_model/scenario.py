"""Ask the twin "what if" before doing it to live fish.

The twin (`twin.py`) rolls one system forward. This forks it: take the state a system is in now,
apply a change, and run both branches side by side. The output is not a number but a COMPARISON —
what the change does to the path, relative to leaving things alone.

That framing is deliberate. An absolute prediction ("nitrite will reach 2.3 mg/L") claims accuracy
the twin has not earned: it has never been validated against a real system, because no public
dataset pairs feeding records with independently-measured nitrogen (#87). A relative statement
("this triples your nitrite peak and delays recovery by nine days") survives being wrong about the
absolute level, because the same unmodelled error sits in both branches and largely cancels.

Every trajectory carries an uncertainty band swept over `TwinParams` — the parameters the stepped
model adds and nobody has fitted. The band is what is genuinely unknown, not decoration.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .species import FishSpecies
from .twin import (
    NOT_MODELLED, PARAMS_FAST, PARAMS_SLOW, PARAMS_TYPICAL, StepResult, TwinParams, TwinState,
    simulate,
)

# Concentrations at which a curated source says an operator should act. First-party, from
# knowledge/nitrogen_cycle_and_cycling.md.
#
# The ammonia figure is TOTAL ammonia nitrogen and is a CONSERVATIVE PROXY, not a toxicity
# calculation: what actually harms fish is the un-ionised NH3 fraction, which rises sharply with pH
# and temperature and which this model explicitly does not compute (see twin.NOT_MODELLED). At high
# pH the real danger begins below this number. Treat a crossing as "look at this", never as a
# safety certificate.
THRESHOLDS_MG_L = {
    "tan_mg_l": 0.5,
    "no2_mg_l": 0.5,
    "no3_mg_l": 150.0,
}
THRESHOLD_SOURCE = "knowledge/nitrogen_cycle_and_cycling.md"

# Below this a peak is indistinguishable from zero, and a RATIO against it is arithmetic noise —
# 0.012 -> 8.12 mg/L is a real and serious change, but calling it "678x higher" is meaningless
# precision. Under this floor the absolute change is reported instead.
_MATERIAL_MG_L = 0.05


@dataclass(frozen=True)
class Intervention:
    """A change an operator is considering. `None` means "leave this as it is"."""

    name: str
    feed_g_per_day: float | None = None
    temperature_c: float | None = None
    add_fish_kg: float | None = None
    plant_uptake_capacity_g_day: float | None = None
    volume_l: float | None = None

    def apply_to_state(self, state: TwinState) -> TwinState:
        if self.add_fish_kg is None and self.volume_l is None:
            return state
        return replace(
            state,
            fish_biomass_kg=state.fish_biomass_kg + (self.add_fish_kg or 0.0),
            volume_l=self.volume_l if self.volume_l is not None else state.volume_l,
        )


@dataclass(frozen=True)
class ChannelOutcome:
    """What one water-quality channel does over a run."""

    channel: str
    peak: float
    peak_day: float
    final: float
    days_above_threshold: float
    threshold: float
    peak_low: float          # uncertainty band, swept over TwinParams
    peak_high: float

    @property
    def crosses_threshold(self) -> bool:
        return self.peak > self.threshold


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    outcomes: dict[str, ChannelOutcome]
    trajectory: list[StepResult]
    warnings: tuple[str, ...]
    not_modelled: tuple[str, ...] = NOT_MODELLED


def _series(traj: list[StepResult], channel: str) -> list[tuple[float, float]]:
    return [(r.state.day, getattr(r.state, channel)) for r in traj]


def _outcome(channel: str, bands: dict[str, list[list[StepResult]]], dt_days: float) -> ChannelOutcome:
    mid = bands["typical"][0]
    pts = _series(mid, channel)
    peak_day, peak = max(((d, v) for d, v in pts), key=lambda p: p[1])
    thr = THRESHOLDS_MG_L[channel]
    above = sum(dt_days for _d, v in pts if v > thr)
    peaks = []
    for runs in bands.values():
        for run in runs:
            peaks.append(max(getattr(r.state, channel) for r in run))
    return ChannelOutcome(
        channel=channel, peak=peak, peak_day=peak_day, final=pts[-1][1],
        days_above_threshold=above, threshold=thr,
        peak_low=min(peaks), peak_high=max(peaks),
    )


def run_scenario(
    state: TwinState,
    species: FishSpecies,
    intervention: Intervention,
    *,
    days: int,
    feed_g_per_day: float,
    temperature_c: float,
    dt_days: float = 1.0,
    plant_uptake_capacity_g_day: float | None = None,
) -> ScenarioResult:
    """Fork the twin, apply an intervention, and run it forward with an uncertainty band.

    Forking is safe by construction: `TwinState` is frozen, so a branch cannot reach back and
    modify the system it came from. That property is why the twin's state was made immutable
    before there was anything to fork.
    """
    start = intervention.apply_to_state(state)
    feed = intervention.feed_g_per_day if intervention.feed_g_per_day is not None else feed_g_per_day
    temp = intervention.temperature_c if intervention.temperature_c is not None else temperature_c
    cap = (intervention.plant_uptake_capacity_g_day
           if intervention.plant_uptake_capacity_g_day is not None
           else plant_uptake_capacity_g_day)

    bands: dict[str, list[list[StepResult]]] = {}
    for label, params in (("typical", PARAMS_TYPICAL), ("fast", PARAMS_FAST), ("slow", PARAMS_SLOW)):
        bands[label] = [simulate(start, species, days=days, feed_g_per_day=feed,
                                 temperature_c=temp, dt_days=dt_days,
                                 plant_uptake_capacity_g_day=cap, params=params)]

    traj = bands["typical"][0]
    warnings = tuple(dict.fromkeys(w for r in traj for w in r.warnings))
    outcomes = {ch: _outcome(ch, bands, dt_days) for ch in THRESHOLDS_MG_L}
    return ScenarioResult(name=intervention.name, outcomes=outcomes,
                          trajectory=traj, warnings=warnings)


@dataclass(frozen=True)
class Comparison:
    """What an intervention changes, stated relative to leaving things alone."""

    baseline: ScenarioResult
    scenario: ScenarioResult
    verdict: str
    findings: tuple[str, ...]

    def worsens(self) -> bool:
        return any(
            self.scenario.outcomes[ch].peak > self.baseline.outcomes[ch].peak * 1.1
            for ch in self.scenario.outcomes
        )


def compare(baseline: ScenarioResult, scenario: ScenarioResult) -> Comparison:
    """Contrast two runs and say, in plain terms, what changed.

    Reported as ratios and day-offsets rather than absolute levels. The twin is unvalidated, so a
    shared systematic error mostly cancels between two branches of the same model — which makes
    "three times worse" far more defensible than "2.3 mg/L".
    """
    findings: list[str] = []
    worse = False
    for ch, after in scenario.outcomes.items():
        before = baseline.outcomes[ch]
        label = {"tan_mg_l": "ammonia", "no2_mg_l": "nitrite", "no3_mg_l": "nitrate"}[ch]
        if before.peak >= _MATERIAL_MG_L:
            ratio = after.peak / before.peak
            if ratio >= 1.1:
                worse = True
                findings.append(
                    f"{label} peak {ratio:.1f}x higher ({before.peak:.2f} -> {after.peak:.2f} mg/L, "
                    f"plausible range {after.peak_low:.2f}-{after.peak_high:.2f})")
            elif ratio <= 0.9:
                findings.append(f"{label} peak {1 / ratio:.1f}x lower")
        elif after.peak >= _MATERIAL_MG_L:
            # Baseline was effectively zero: a ratio here would be noise, so state the level.
            worse = True
            findings.append(
                f"{label} rises to {after.peak:.2f} mg/L (plausible range "
                f"{after.peak_low:.2f}-{after.peak_high:.2f}) from a baseline that stayed near zero")

        if after.crosses_threshold and not before.crosses_threshold:
            worse = True
            findings.append(
                f"{label} crosses {after.threshold} mg/L for {after.days_above_threshold:.0f} days "
                f"— the baseline never does ({THRESHOLD_SOURCE})")
        elif after.crosses_threshold and after.days_above_threshold > before.days_above_threshold:
            findings.append(
                f"{label} spends {after.days_above_threshold - before.days_above_threshold:.0f} "
                f"more days above {after.threshold} mg/L")

    # An intervention that suppresses feeding improves every nitrogen channel, because less feed
    # means less nitrogen. Reporting that as "improves water quality" is how a nitrogen-only model
    # recommends starving fish or chilling them out of their thermal range. The model cannot see
    # welfare, so where it has evidence that feeding was suppressed it must say what it is looking
    # at rather than deliver a verdict it has no standing to give.
    suppressed = any("optimum" in w for w in scenario.warnings)
    if not findings:
        verdict = "No material change to water quality over this horizon."
    elif worse:
        verdict = "This makes water quality worse before it settles — stage it or add capacity first."
    elif suppressed:
        verdict = ("Nitrogen levels fall, but only because the fish are outside their temperature "
                   "optimum and eating less. That is suppressed feeding, not a healthier system — "
                   "this model sees nitrogen only, and cannot speak to fish welfare or growth.")
    else:
        verdict = "This improves water quality over this horizon."
    return Comparison(baseline=baseline, scenario=scenario, verdict=verdict,
                      findings=tuple(findings))


def format_comparison(cmp: Comparison) -> str:
    """Operator-facing text. States its own limits, because a projection reads as a promise."""
    lines = [f"{cmp.scenario.name} vs {cmp.baseline.name}", "", cmp.verdict]
    if cmp.findings:
        lines.append("")
        lines += [f"  - {f}" for f in cmp.findings]
    if cmp.scenario.warnings:
        lines.append("")
        lines += [f"  ! {w}" for w in cmp.scenario.warnings]
    from .validation_status import validation_lines
    lines += ["", *validation_lines(),
              "NOT modelled: " + "; ".join(cmp.scenario.not_modelled[:3]) + "."]
    return "\n".join(lines)
