"""Explicit state adjustments, kept separate from simulated water fluxes."""

from dataclasses import dataclass, replace
from fractions import Fraction

from .soil_evaporation import SoilEvaporationMemory
from .soil_replay import SoilWaterState
from .soil_storage import _quantity
from .validation import text

MODEL_VERSION = "soil-reconciliation-1-explicit-compartments"


@dataclass(frozen=True)
class CompartmentReading:
    patch_id: str
    layer_id: str
    water_content: Fraction
    source: str

    def __post_init__(self):
        for key in ("patch_id", "layer_id", "source"):
            text(key, getattr(self, key))
        object.__setattr__(self, "water_content", _quantity("reading water content", self.water_content, 1))


def reconcile_soil_water(state, readings, memories, source):
    """Replace explicitly sampled whole cells; caller establishes observation coverage/time.

    Every affected patch needs reviewed drying memory. Surface water, geometry,
    hydraulic properties and all unselected compartments remain unchanged.
    The signed storage difference is an adjustment, not rainfall or irrigation.
    """
    if not isinstance(state, SoilWaterState):
        raise ValueError("reconciliation requires a validated soil-water state")
    text("state reconciliation source", source)
    if not isinstance(readings, (tuple, list)) or not 1 <= len(readings) <= 1000:
        raise ValueError("supply 1–1000 explicit compartment readings")
    if any(not isinstance(r, CompartmentReading) for r in readings):
        raise ValueError("supply validated compartment readings")
    selected = {(r.patch_id, r.layer_id): r for r in readings}
    cells = {(p.id, c.id) for p in state.soil.patches for c in p.compartments}
    if len(selected) != len(readings) or not set(selected) <= cells:
        raise ValueError("readings require unique existing patch/layer pairs")
    if not isinstance(memories, tuple) or not memories or len(memories) > 100:
        raise ValueError("supply an immutable tuple of reviewed patch drying memories")
    for pair in memories:
        if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[1], SoilEvaporationMemory):
            raise ValueError("memory rows require patch ID and validated drying memory")
        text("memory patch ID", pair[0])
    replacements = dict(memories)
    if len(replacements) != len(memories) or set(replacements) != {r.patch_id for r in readings}:
        raise ValueError("reviewed memories must cover exactly the affected patches")
    patches, changes = [], []
    for patch in state.soil.patches:
        new_cells = []
        for cell in patch.compartments:
            reading = selected.get((patch.id, cell.id))
            updated = replace(cell, water_content=reading.water_content) if reading else cell
            new_cells.append(updated)
            if reading:
                changes.append(dict(patch_id=patch.id, layer_id=cell.id, previous_water_content=cell.water_content,
                    adopted_water_content=updated.water_content, source=reading.source,
                    storage_adjustment_l=1000*state.soil.area_m2*patch.area_fraction*cell.thickness_m*(updated.water_content-cell.water_content)))
        patches.append(replace(patch, compartments=tuple(new_cells)))
    ending = replace(state, soil=replace(state.soil, patches=tuple(patches)), source=source,
        evaporation_memories=tuple((key, replacements.get(key, memory)) for key, memory in state.evaporation_memories))
    delta = ending.soil.water_l-state.soil.water_l
    return dict(model_version=MODEL_VERSION, initial=state, ending=ending, changes=tuple(changes),
        storage_adjustment_l=delta, adjustment_residual_l=delta-sum((r["storage_adjustment_l"] for r in changes), Fraction(0)),
        assumptions=("Each supplied reading represents its complete modeled compartment and patch footprint at this end-of-day boundary.",
                     "Reviewed drying memories are explicit inputs; they are not inferred from soil-water readings.",
                     "Positive adjustment increases stored soil water. The adjustment is not a simulated or observed water flux."),
        limitations=("No field observation quality, sampling coverage or timing is established by this storage primitive.",
                     "Geometry, soil properties, surface water and unselected compartments remain unchanged.",
                     "Adopted water contents outside the supplied soil dry/saturation bounds are rejected, never clipped."))
