"""Design-input sensitivity — how the model's answer moves when an input moves.

The design report answers one question ("what does THIS design produce?") with single
numbers. A funder conversation rarely stops there; the next question is always "and if
the water budget is 20% lower?". This module answers it with the SAME optimizer the chat
tool uses: each row re-runs `optimizer.optimize` on the design's own inputs with one input
moved at a time. There is deliberately no second scoring path here, so the table can never
drift from the answer it sits beside.

Fixed, stated perturbation set (small on purpose — legible beats general):
  - water budget +20% / -20%   (only when the design actually gives a budget)
  - grow area +20% / -20%
  - crop mix → the optimizer's best allocation over the crops in band at the design's
    own temperature, ranked by its default objective, at the optimizer's existing
    allocation steps

The table states its own limit once, plainly: it shows how the MODEL responds, not how a
real system would. Pure and deterministic — no LLM, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .crops import CROPS
from .optimizer import OptimizeInput, optimize
from .types import DesignInput
from .validate import _BOUNDS, WATER_NOT_LIMITING

# How far the +/- rows move each input. A reporting choice — the fixed step that keeps
# every report's sensitivity table comparable — not a physical coefficient, hence a
# module constant with a stated reason rather than a Coefficient in coefficients.py.
SWEEP = 0.20

# The crop-mix row ranks the optimizer's in-band-palette search by the optimizer's
# default objective. Stated in the row label so a reader knows what "best" means there.
_MIX_OBJECTIVE = "water_efficiency"

_HONESTY = (
    "This table shows how the model responds, not how a real system would: each row "
    "re-runs the same optimizer on the design's own inputs with one input moved at a "
    "time. No real system responds this cleanly."
)

_BASELINE_LABEL = "Design as given"


@dataclass(frozen=True)
class SensitivityRow:
    """One perturbed run: what moved, the optimizer's answer for it, and the delta."""

    change: str
    feasible: bool
    food_kg_yr: float | None = None
    water_efficiency_kg_per_m3: float | None = None
    food_delta_pct: float | None = None
    water_efficiency_delta_pct: float | None = None
    detail: str = ""


@dataclass
class SensitivityResult:
    """The design's own optimizer answer plus every perturbation row, ready to render."""

    baseline_feasible: bool
    baseline_food_kg_yr: float | None = None
    baseline_water_efficiency_kg_per_m3: float | None = None
    rows: list[SensitivityRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _clamp(name: str, value: float) -> float:
    lo, hi = _BOUNDS[name]
    return min(max(value, lo), hi)


def _delta(new: float | None, base: float | None) -> float | None:
    if new is None or base is None or base <= 0:
        return None
    return round((new / base - 1.0) * 100.0, 1)


def _pinned_input(
    design: DesignInput,
    *,
    grow_area_m2: float | None = None,
    water_budget_lpd: float | None = None,
) -> OptimizeInput:
    """The design's own inputs with at most one moved, pinned to its own fish and crop.

    Pinning the palette is what makes the +/- rows pure input sensitivity: the optimizer
    still does the scoring, but with a single-candidate search the ONLY thing that moves
    between rows is the input itself.
    """
    area = _clamp("grow_area_m2", grow_area_m2 if grow_area_m2 is not None else design.grow_area_m2)
    water = _clamp(
        "water_budget_lpd", water_budget_lpd if water_budget_lpd is not None else design.water_budget_lpd
    )
    return OptimizeInput(
        grow_area_m2=area,
        temperature_c=design.temperature_c,
        water_budget_lpd=water,
        fish_palette=(design.fish_species,),
        crop_palette=(design.crop,),
    )


def _row(
    design: DesignInput,
    base_food: float | None,
    base_weff: float | None,
    *,
    change: str,
    detail: str,
    grow_area_m2: float | None = None,
    water_budget_lpd: float | None = None,
) -> SensitivityRow:
    res = optimize(
        _pinned_input(design, grow_area_m2=grow_area_m2, water_budget_lpd=water_budget_lpd)
    )
    best = res.best
    if best is None:
        return SensitivityRow(change=change, feasible=False, detail=detail)
    return SensitivityRow(
        change=change,
        feasible=True,
        food_kg_yr=best.food_kg_yr,
        water_efficiency_kg_per_m3=best.water_efficiency_kg_per_m3,
        food_delta_pct=_delta(best.food_kg_yr, base_food),
        water_efficiency_delta_pct=_delta(best.water_efficiency_kg_per_m3, base_weff),
        detail=detail,
    )


def _crop_mix_row(
    design: DesignInput, base_food: float | None, base_weff: float | None
) -> SensitivityRow:
    """What re-allocating the same area across crops would buy, per the optimizer itself."""
    # The "best" row may only recommend what can actually grow in the room: the palette
    # is filtered to crops whose temperature band covers the design's own temperature
    # before ranking (review on #241 — cucumber topping out at 30 °C was being offered
    # for a 33 °C design). Same optimizer, same objective — a pre-filtered palette,
    # never a second scoring path.
    palette = tuple(sorted(
        k for k, c in CROPS.items()
        if c.temp_min_c <= design.temperature_c <= c.temp_max_c))
    change = "crop mix → optimizer's best"
    if not palette:
        return SensitivityRow(
            change=change,
            feasible=False,
            detail=f"no crop in the palette grows at {design.temperature_c:g} °C",
        )
    res = optimize(
        OptimizeInput(
            grow_area_m2=design.grow_area_m2,
            temperature_c=design.temperature_c,
            water_budget_lpd=design.water_budget_lpd,
            objective=_MIX_OBJECTIVE,
            fish_palette=(design.fish_species,),
            crop_palette=palette,
        )
    )
    best = res.best
    if best is None:
        return SensitivityRow(change=change, feasible=False)
    mix = ", ".join(f"{crop} {frac:.0%}" for crop, frac in sorted(best.crop_allocation.items()))
    return SensitivityRow(
        change=change,
        feasible=True,
        food_kg_yr=best.food_kg_yr,
        water_efficiency_kg_per_m3=best.water_efficiency_kg_per_m3,
        food_delta_pct=_delta(best.food_kg_yr, base_food),
        water_efficiency_delta_pct=_delta(best.water_efficiency_kg_per_m3, base_weff),
        detail=(f"by {_MIX_OBJECTIVE.replace('_', ' ')}; best allocation: {mix}; "
                f"palette: {len(palette)} of {len(CROPS)} crops in band at "
                f"{design.temperature_c:g} °C"),
    )


def sensitivity_table(design: DesignInput) -> SensitivityResult:
    """Re-run the optimizer on the design's own inputs, one input moved at a time."""
    base = optimize(_pinned_input(design)).best
    base_food = base.food_kg_yr if base is not None else None
    base_weff = base.water_efficiency_kg_per_m3 if base is not None else None
    result = SensitivityResult(
        baseline_feasible=base is not None,
        baseline_food_kg_yr=base_food,
        baseline_water_efficiency_kg_per_m3=base_weff,
    )

    if design.crop_plan:
        result.notes.append(
            f"Mixed bed: the +/- rows pin the mix to the dominant crop ({design.crop}); "
            "the crop-mix row searches the crops in band at the design's temperature."
        )
    if base is None:
        result.notes.append(
            "The design as given is not feasible under its own water budget, so deltas "
            "are omitted; feasible rows show absolute values only."
        )

    if design.water_budget_lpd < WATER_NOT_LIMITING:
        up = _clamp("water_budget_lpd", design.water_budget_lpd * (1.0 + SWEEP))
        down = _clamp("water_budget_lpd", design.water_budget_lpd * (1.0 - SWEEP))
        result.rows.append(
            _row(
                design, base_food, base_weff,
                change="water budget +20%",
                detail=f"{design.water_budget_lpd:g} → {up:g} L/day",
                water_budget_lpd=up,
            )
        )
        result.rows.append(
            _row(
                design, base_food, base_weff,
                change="water budget -20%",
                detail=f"{design.water_budget_lpd:g} → {down:g} L/day",
                water_budget_lpd=down,
            )
        )
    else:
        result.notes.append(
            "Water-budget rows are omitted: the design gives no water budget, so there "
            "is nothing to move."
        )

    up_area = _clamp("grow_area_m2", design.grow_area_m2 * (1.0 + SWEEP))
    down_area = _clamp("grow_area_m2", design.grow_area_m2 * (1.0 - SWEEP))
    result.rows.append(
        _row(
            design, base_food, base_weff,
            change="grow area +20%",
            detail=f"{design.grow_area_m2:g} → {up_area:g} m²",
            grow_area_m2=up_area,
        )
    )
    result.rows.append(
        _row(
            design, base_food, base_weff,
            change="grow area -20%",
            detail=f"{design.grow_area_m2:g} → {down_area:g} m²",
            grow_area_m2=down_area,
        )
    )
    result.rows.append(_crop_mix_row(design, base_food, base_weff))
    return result


def _num(value: float | None, places: int) -> str:
    if value is None:
        return "—"
    return f"{value:,.{places}f}"


def _pct(delta: float | None) -> str:
    return "—" if delta is None else f"{delta:+.1f}%"


def _render_row(row: SensitivityRow) -> str:
    label = f"{row.change} ({row.detail})" if row.detail else row.change
    if not row.feasible:
        return f"| {label} | none feasible | — | — | — |"
    return (
        f"| {label} | {_num(row.food_kg_yr, 1)} | {_pct(row.food_delta_pct)} | "
        f"{_num(row.water_efficiency_kg_per_m3, 2)} | {_pct(row.water_efficiency_delta_pct)} |"
    )


def render_sensitivity(result: SensitivityResult) -> list[str]:
    """Markdown lines for the sensitivity section: heading, honesty line, table, notes."""
    lines = [
        "## Sensitivity (model response)",
        "",
        _HONESTY,
        "",
        "| Change | Food (kg/yr) | Δ food | Water efficiency (kg/m³) | Δ water eff |",
        "|---|---|---|---|---|",
        (
            f"| {_BASELINE_LABEL} | none feasible | — | — | — |"
            if not result.baseline_feasible
            else f"| {_BASELINE_LABEL} | {_num(result.baseline_food_kg_yr, 1)} | — | "
            f"{_num(result.baseline_water_efficiency_kg_per_m3, 2)} | — |"
        ),
    ]
    for row in result.rows:
        lines.append(_render_row(row))
    for note in result.notes:
        lines.append("")
        lines.append(f"*{note}*")
    return lines
