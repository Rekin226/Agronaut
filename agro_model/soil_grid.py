"""Conservative subdivision of uniformly mixed soil compartments before replay."""

from dataclasses import dataclass, replace
from fractions import Fraction

from .soil_drainage import DrainageParameters
from .soil_roots import RootLayerActivity
from .soil_storage import SoilStorage, _quantity
from .validation import text

MODEL_VERSION = "soil-grid-1-uniform-subdivision"


@dataclass(frozen=True)
class SoilCellOrigin:
    patch_id: str
    original_id: str
    resulting_id: str
    volume_fraction: Fraction


@dataclass(frozen=True)
class SoilGridChange:
    initial: SoilStorage
    ending: SoilStorage
    origins: tuple[SoilCellOrigin, ...]
    source: str
    model_version: str = MODEL_VERSION

    @property
    def balance_residual_l(self):
        return self.ending.water_l-self.initial.water_l

    def _remap(self, values, cls):
        keys = {(p.id, c.id) for p in self.initial.patches for c in p.compartments}
        if not isinstance(values, dict) or set(values) != keys or any(not isinstance(v, cls) for v in values.values()):
            raise ValueError("supply exactly one validated intensive parameter per original compartment")
        # These are volumetric rates/conductivities, never additive layer totals.
        return tuple(((r.patch_id, r.resulting_id), values[(r.patch_id, r.original_id)]) for r in self.origins)

    def drainage_parameters(self, values):
        return self._remap(values, DrainageParameters)

    def root_activities(self, values):
        return self._remap(values, RootLayerActivity)


def split_soil_grid(storage, boundaries, *, source):
    """Split at explicit local depths; retain density, water and soil properties.

    Every patch requires a tuple of requested depths (possibly empty). Existing
    boundaries are a no-op. This only subdivides uniform cells; it never combines
    heterogeneous layers, changes patch area or estimates a moisture gradient.
    Prepare all required rooting/evaporation boundaries before a dated replay.
    """
    if not isinstance(storage, SoilStorage):
        raise ValueError("supply validated soil storage")
    text("grid preparation source", source)
    if not isinstance(boundaries, dict) or set(boundaries) != {p.id for p in storage.patches}:
        raise ValueError("supply boundary depths for every patch, including an empty tuple when unchanged")
    patches, origins = [], []
    for patch in storage.patches:
        supplied = boundaries[patch.id]
        if not isinstance(supplied, tuple) or len(supplied) > 100:
            raise ValueError("boundary depths must be a tuple of at most 100 values per patch")
        cuts = tuple(_quantity("split depth", d, patch.compartments[-1].bottom_m, positive=True) for d in supplied)
        if len(set(cuts)) != len(cuts):
            raise ValueError("split depths must be unique")
        reserved = {c.id for c in patch.compartments}
        cells, serial = [], 0
        for cell in patch.compartments:
            inside = sorted(d for d in cuts if cell.top_m < d < cell.bottom_m)
            edges = [cell.top_m, *inside, cell.bottom_m]
            for top, bottom in zip(edges, edges[1:]):
                identity = cell.id
                if inside:
                    identity = f"__grid_cell_{serial}"
                    while identity in reserved:
                        serial += 1
                        identity = f"__grid_cell_{serial}"
                    reserved.add(identity)
                    serial += 1
                cells.append(replace(cell, id=identity, top_m=top, bottom_m=bottom))
                origins.append(SoilCellOrigin(patch.id, cell.id, identity, (bottom-top)/cell.thickness_m))
        patches.append(replace(patch, compartments=tuple(cells)))
    ending = replace(storage, patches=tuple(patches))
    change = SoilGridChange(storage, ending, tuple(origins), source)
    if change.balance_residual_l:
        raise ArithmeticError("grid subdivision failed water conservation")
    return change
