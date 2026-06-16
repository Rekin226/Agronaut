"""M2 optimizer — adjust the fish/plant ratio for maximum efficiency.

Method: BOUNDED ENUMERATION over the small palette (per the design doc — no LP/MILP solver;
the objective and constraints are nonlinear and the palette is tiny, so enumeration is both
simpler and correct). For each (fish species × crop-area allocation) it sizes the system,
checks feasibility against the binding constraint (water budget in v1), scores it by the
chosen objective, and returns the ranked best with the same honesty layer as M1.

Objectives:
  food            -> total edible mass per year (fish growth + crop yield), kg/yr
  protein         -> total edible protein per year, kg/yr
  water_efficiency-> food per unit makeup water, kg per m3/yr   (the founder's likely objective)

What it does NOT optimize (seed model): money/value (no price data), labour, BOM cost,
local availability. Those are real for Shape-B quoting and are deferred (see not_modeled).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations_with_replacement

from . import coefficients as C
from . import massbalance as mb
from .crops import CROPS, get_crop
from .species import SPECIES, get_species, temperature_feed_factor

OBJECTIVES = ("food", "protein", "water_efficiency")
_ALLOC_STEPS = 4  # area is split into quarters across crops (bounded enumeration granularity)


@dataclass(frozen=True)
class OptimizeInput:
    grow_area_m2: float
    temperature_c: float
    water_budget_lpd: float
    objective: str = "water_efficiency"
    fish_palette: tuple[str, ...] = tuple(sorted(SPECIES))
    crop_palette: tuple[str, ...] = tuple(sorted(CROPS))


@dataclass
class Candidate:
    fish_species: str
    crop_allocation: dict[str, float]   # crop -> fraction of grow area (sums to 1)
    feasible: bool
    score: float
    food_kg_yr: float
    protein_kg_yr: float
    makeup_water_lpd: float
    water_efficiency_kg_per_m3: float
    feed_g_per_day: float
    fish_biomass_kg: float


@dataclass
class OptimizeResult:
    objective: str
    best: Candidate | None
    ranked: list[Candidate] = field(default_factory=list)
    searched: int = 0
    feasible_count: int = 0
    baseline: Candidate | None = None          # naive even-split, same best fish
    improvement_vs_baseline_pct: float | None = None
    not_modeled: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


_NOT_MODELED = [
    "monetary value / price, labour, and bill-of-materials cost",
    "local availability of fish, crops, feed, and materials",
    "market demand for the chosen crop/fish mix",
    "per-crop evapotranspiration — water use is modeled per-m2 (crop-agnostic) in v1, so the "
    "water-efficiency objective is currently yield-driven; calibrate per-crop ET to refine it",
    "everything aqua_model already does not model (pH, micronutrients, solids, pests, cohorts)",
]


def _allocations(crops: tuple[str, ...], steps: int = _ALLOC_STEPS):
    """Yield crop->fraction dicts: integer compositions of `steps` quarters over `crops`,
    dropping zero-area crops. Bounded and deterministic."""
    n = len(crops)
    seen = set()
    # Each composition = a multiset of `steps` picks among crop indices.
    for combo in combinations_with_replacement(range(n), steps):
        counts = [0] * n
        for i in combo:
            counts[i] += 1
        alloc = {crops[i]: counts[i] / steps for i in range(n) if counts[i] > 0}
        key = tuple(sorted(alloc.items()))
        if key not in seen:
            seen.add(key)
            yield alloc


def _evaluate(fish_name: str, alloc: dict[str, float], inp: OptimizeInput) -> Candidate:
    species = get_species(fish_name)
    area = inp.grow_area_m2

    # Feed from the area-weighted FRR of the mix (FRR is the sizing rule).
    feed_g_day = sum(area * frac * get_crop(c).frr_g_per_m2_day for c, frac in alloc.items())

    temp_factor = temperature_feed_factor(species, inp.temperature_c)
    eff_pct = species.feeding_rate_pct_bw * temp_factor
    fish_biomass_kg = feed_g_day / (eff_pct / 100.0) / 1000.0 if eff_pct > 0 else 0.0

    # Annual food: crop yield (per allocated area) + fish growth (= feed / FCR).
    crop_food_kg_yr = sum(area * frac * get_crop(c).yield_kg_per_m2_year for c, frac in alloc.items())
    fish_growth_kg_yr = (feed_g_day / species.fcr) / 1000.0 * 365.0
    food_kg_yr = crop_food_kg_yr + fish_growth_kg_yr

    crop_protein_kg_yr = sum(
        area * frac * get_crop(c).yield_kg_per_m2_year * (get_crop(c).edible_protein_pct / 100.0)
        for c, frac in alloc.items()
    )
    fish_protein_kg_yr = fish_growth_kg_yr * (species.body_protein_pct / 100.0)
    protein_kg_yr = crop_protein_kg_yr + fish_protein_kg_yr

    # Water balance + feasibility (water is the binding constraint in v1).
    tank_surface_m2 = (fish_biomass_kg / species.stocking_density_kg_m3) / 1.0
    makeup_lpd = mb.water_balance(area, tank_surface_m2)["makeup_water_lpd"]
    annual_makeup_m3 = makeup_lpd * 365.0 / 1000.0
    water_eff = food_kg_yr / annual_makeup_m3 if annual_makeup_m3 > 0 else 0.0
    feasible = makeup_lpd <= inp.water_budget_lpd

    score = {
        "food": food_kg_yr,
        "protein": protein_kg_yr,
        "water_efficiency": water_eff,
    }[inp.objective]

    return Candidate(
        fish_species=fish_name,
        crop_allocation={c: round(f, 3) for c, f in alloc.items()},
        feasible=feasible,
        score=round(score, 3),
        food_kg_yr=round(food_kg_yr, 1),
        protein_kg_yr=round(protein_kg_yr, 2),
        makeup_water_lpd=makeup_lpd,
        water_efficiency_kg_per_m3=round(water_eff, 2),
        feed_g_per_day=round(feed_g_day, 1),
        fish_biomass_kg=round(fish_biomass_kg, 2),
    )


def optimize(inp: OptimizeInput) -> OptimizeResult:
    if inp.objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective {inp.objective!r}. Supported: {OBJECTIVES}.")
    if not inp.fish_palette or not inp.crop_palette:
        raise ValueError("fish_palette and crop_palette must be non-empty.")

    # Search the quarter-grid PLUS the exact even-split, so the optimizer provably can
    # never score below the naive baseline (best >= baseline by construction).
    even = {c: 1.0 / len(inp.crop_palette) for c in inp.crop_palette}
    allocs = list(_allocations(tuple(inp.crop_palette)))
    if not any(a == even for a in allocs):
        allocs.append(even)

    candidates: list[Candidate] = []
    for fish in inp.fish_palette:
        for alloc in allocs:
            candidates.append(_evaluate(fish, alloc, inp))

    feasible = [c for c in candidates if c.feasible]
    ranked = sorted(feasible, key=lambda c: c.score, reverse=True)
    best = ranked[0] if ranked else None

    # Naive baseline: even split across the whole crop palette, using the best design's fish
    # (or the first species if nothing is feasible). This is what the optimizer must beat.
    baseline_fish = best.fish_species if best else inp.fish_palette[0]
    baseline = _evaluate(baseline_fish, even, inp)

    improvement = None
    if best and baseline.score > 0:
        improvement = round((best.score - baseline.score) / baseline.score * 100.0, 1)

    return OptimizeResult(
        objective=inp.objective,
        best=best,
        ranked=ranked,
        searched=len(candidates),
        feasible_count=len(feasible),
        baseline=baseline,
        improvement_vs_baseline_pct=improvement,
        not_modeled=list(_NOT_MODELED),
        assumptions=[
            f"Bounded enumeration: {len(candidates)} candidates "
            f"({len(inp.fish_palette)} fish × crop-mix allocations in 1/{_ALLOC_STEPS} steps).",
            "Water budget is the binding feasibility constraint (v1).",
            "Yields and protein are seed coefficients — calibrate before quoting outcomes.",
        ],
    )
