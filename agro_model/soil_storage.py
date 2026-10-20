"""Conservative shared soil storage for a future coupled water model.

This internal primitive accounts for supplied, ordered flux requests. It does not
predict infiltration, hydraulic transport, root activity, stress, or evaporation.
Those governing equations must supply requests; bookkeeping must not invent them.
All amounts are exact litres, including transfers between adjacent compartments.
"""

from dataclasses import dataclass, replace
from fractions import Fraction

from .validation import choice, number, text

MODEL_VERSION = "soil-storage-1"


def _quantity(label, value, maximum, *, positive=False):
    # Preserve exact continuation states. Convert input floats by their decimal
    # representation, not their binary expansion. No rounding or missing defaults.
    if isinstance(value, Fraction):
        result = value
    else:
        number(label, value, 0, maximum, positive=positive)
        result = Fraction(str(value))
    if result < 0 or result > maximum or (positive and result == 0):
        raise ValueError(f"{label} is outside its supported nonnegative range")
    return result


@dataclass(frozen=True)
class SoilCompartment:
    id: str
    top_m: Fraction
    bottom_m: Fraction
    field_capacity: Fraction
    wilting_point: Fraction
    saturation: Fraction
    evaporation_floor: Fraction
    water_content: Fraction
    soil_source: str

    def __post_init__(self):
        text("compartment id", self.id)
        text("soil source", self.soil_source)
        for name in ("top_m", "bottom_m", "field_capacity", "wilting_point",
                     "saturation", "evaporation_floor", "water_content"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name),
                                                   10 if name.endswith("_m") else 1))
        if self.bottom_m <= self.top_m:
            raise ValueError("compartment bottom must exceed top")
        if not self.evaporation_floor <= self.wilting_point < self.field_capacity <= self.saturation:
            raise ValueError("require evaporation floor <= wilting point < field capacity <= saturation")
        if not self.evaporation_floor <= self.water_content <= self.saturation:
            raise ValueError("water content must lie between the supplied evaporation floor and saturation")

    @property
    def thickness_m(self):
        return self.bottom_m - self.top_m


@dataclass(frozen=True)
class SoilPatch:
    id: str
    area_fraction: Fraction
    compartments: tuple[SoilCompartment, ...]

    def __post_init__(self):
        text("patch id", self.id)
        object.__setattr__(self, "area_fraction", _quantity("patch area fraction", self.area_fraction, 1, positive=True))
        if not isinstance(self.compartments, tuple) or not 1 <= len(self.compartments) <= 100:
            raise ValueError("patch compartments must be a tuple of 1–100 layers")
        previous, ids = Fraction(0), set()
        for cell in self.compartments:
            if not isinstance(cell, SoilCompartment):
                raise ValueError("patch requires validated soil compartments")
            if cell.id in ids:
                raise ValueError("compartment IDs must be unique within a patch")
            ids.add(cell.id)
            if cell.top_m != previous:
                raise ValueError("compartments must be contiguous from depth zero without overlap")
            previous = cell.bottom_m


@dataclass(frozen=True)
class SoilStorage:
    area_m2: Fraction
    patches: tuple[SoilPatch, ...]
    source: str

    def __post_init__(self):
        text("storage state source", self.source)
        object.__setattr__(self, "area_m2", _quantity("area_m2", self.area_m2, 10**12, positive=True))
        if not isinstance(self.patches, tuple) or not 1 <= len(self.patches) <= 100:
            raise ValueError("patches must be a tuple of 1–100 patches")
        if any(not isinstance(patch, SoilPatch) for patch in self.patches):
            raise ValueError("storage requires validated soil patches")
        if len({patch.id for patch in self.patches}) != len(self.patches):
            raise ValueError("patch IDs must be unique")
        if sum(patch.area_fraction for patch in self.patches) != 1:
            raise ValueError("disjoint patch area fractions must sum exactly to one")
        if len({patch.compartments[-1].bottom_m for patch in self.patches}) != 1:
            raise ValueError("all patches must cover the same profile depth")
        if sum(len(patch.compartments) for patch in self.patches) > 1000:
            raise ValueError("storage supports at most 1000 compartments")

    @property
    def water_l(self):
        return sum((1000 * self.area_m2 * patch.area_fraction * cell.thickness_m * cell.water_content
                    for patch in self.patches for cell in patch.compartments), Fraction(0))


@dataclass(frozen=True)
class SoilFlux:
    kind: str
    patch_id: str
    from_layer_id: str | None
    to_layer_id: str | None
    requested_l: Fraction
    source: str

    def __post_init__(self):
        choice("flux kind", self.kind, ("infiltration", "drainage", "evaporation", "root_uptake", "transfer"))
        text("patch id", self.patch_id)
        text("flux source", self.source)
        object.__setattr__(self, "requested_l", _quantity("requested litres", self.requested_l, 10**16))
        for label in ("from_layer_id", "to_layer_id"):
            if getattr(self, label) is not None:
                text(label, getattr(self, label))
        if self.kind == "infiltration":
            valid = self.from_layer_id is None and self.to_layer_id is not None
        elif self.kind == "transfer":
            valid = self.from_layer_id is not None and self.to_layer_id is not None
        else:
            valid = self.from_layer_id is not None and self.to_layer_id is None
        if not valid:
            raise ValueError("flux endpoints do not match its kind")


@dataclass(frozen=True)
class AppliedSoilFlux:
    request: SoilFlux
    actual_l: Fraction
    unmet_l: Fraction
    limiting_bounds: tuple[str, ...]


@dataclass(frozen=True)
class SoilStorageChange:
    initial: SoilStorage
    ending: SoilStorage
    fluxes: tuple[AppliedSoilFlux, ...]
    external_input_l: Fraction
    external_output_l: Fraction
    internal_transfer_l: Fraction
    balance_residual_l: Fraction
    compartment_residuals_l: tuple[tuple[str, str, Fraction], ...]
    model_version: str = MODEL_VERSION
    application_mode: str = "ordered_requests"


def apply_soil_fluxes(storage, fluxes):
    """Account for an explicit sequence; unmet requests are never silently rerouted.

    Root uptake stops at WP, evaporation/transfer at the supplied dry floor, and
    bottom drainage at FC. Infiltration and transfers cannot fill beyond saturation.
    The caller determines physically applicable rates, time steps, extraction depths
    and order. A transfer may move up or down between adjacent cells in one patch;
    its direction/rate is not predicted or justified by this conservation primitive.

    Cells below WP are retained as such. Water still available to evaporation is
    never added to plant-available water. Fixed geometry avoids implicit remapping.
    """
    if not isinstance(storage, SoilStorage):
        raise ValueError("supply validated soil storage")
    if not isinstance(fluxes, (list, tuple)) or len(fluxes) > 10000:
        raise ValueError("fluxes must be a sequence of at most 10000 explicit requests")
    cells, sizes, positions = {}, {}, {}
    for patch in storage.patches:
        for index, cell in enumerate(patch.compartments):
            key = (patch.id, cell.id)
            cells[key] = cell
            sizes[key] = 1000 * storage.area_m2 * patch.area_fraction * cell.thickness_m
            positions[key] = (index, len(patch.compartments))
    for flux in fluxes:
        if not isinstance(flux, SoilFlux):
            raise ValueError("all fluxes must be validated SoilFlux requests")
        donor = None if flux.from_layer_id is None else (flux.patch_id, flux.from_layer_id)
        receiver = None if flux.to_layer_id is None else (flux.patch_id, flux.to_layer_id)
        if any(key is not None and key not in cells for key in (donor, receiver)):
            raise ValueError("flux endpoints must identify compartments in the specified patch")
        if flux.kind == "infiltration" and positions[receiver][0] != 0:
            raise ValueError("infiltration must enter the surface compartment")
        if flux.kind == "drainage" and positions[donor][0] != positions[donor][1] - 1:
            raise ValueError("external drainage must leave the bottom compartment")
        if flux.kind == "transfer" and abs(positions[donor][0] - positions[receiver][0]) != 1:
            raise ValueError("internal transfers require adjacent compartments in the same patch")

    initial = {key: cell.water_content * sizes[key] for key, cell in cells.items()}
    water = initial.copy()
    incoming = dict.fromkeys(cells, Fraction(0))
    outgoing = dict.fromkeys(cells, Fraction(0))
    external_in = external_out = transferred = Fraction(0)
    applied = []
    for flux in fluxes:
        donor = None if flux.from_layer_id is None else (flux.patch_id, flux.from_layer_id)
        receiver = None if flux.to_layer_id is None else (flux.patch_id, flux.to_layer_id)
        limits = {}
        if donor is not None:
            cell = cells[donor]
            floor_name = {"root_uptake": "wilting_point", "drainage": "field_capacity"}.get(flux.kind, "evaporation_floor")
            floor = getattr(cell, floor_name) * sizes[donor]
            limits[f"donor_{floor_name}"] = max(Fraction(0), water[donor] - floor)
        if receiver is not None:
            limits["receiver_saturation"] = cells[receiver].saturation * sizes[receiver] - water[receiver]
        actual = min(flux.requested_l, *limits.values())
        unmet = flux.requested_l - actual
        bounds = tuple(name for name, limit in limits.items() if unmet and limit == actual)
        if donor is not None:
            water[donor] -= actual
            outgoing[donor] += actual
        else:
            external_in += actual
        if receiver is not None:
            water[receiver] += actual
            incoming[receiver] += actual
        else:
            external_out += actual
        if donor is not None and receiver is not None:
            transferred += actual
        applied.append(AppliedSoilFlux(flux, actual, unmet, bounds))

    patches = tuple(replace(patch, compartments=tuple(
        replace(cell, water_content=water[(patch.id, cell.id)] / sizes[(patch.id, cell.id)])
        for cell in patch.compartments)) for patch in storage.patches)
    ending = replace(storage, patches=patches, source=f"Accounted storage from {MODEL_VERSION}; see initial state and flux sources")
    residuals = tuple((patch_id, cell_id, water[(patch_id, cell_id)] - initial[(patch_id, cell_id)]
                       - incoming[(patch_id, cell_id)] + outgoing[(patch_id, cell_id)])
                      for patch_id, cell_id in cells)
    residual = ending.water_l - storage.water_l - external_in + external_out
    if residual or any(value for _, _, value in residuals):
        raise ArithmeticError("soil storage accounting failed conservation")
    return SoilStorageChange(storage, ending, tuple(applied), external_in, external_out,
                             transferred, residual, residuals)


def apply_downward_drainage(storage, interface_l, source):
    """Apply one coupled drainage step with already solved downward interface fluxes.

    One nonnegative volume per compartment: each leaves its bottom face; only the
    last face of a patch leaves the profile. Solving all faces together permits
    throughflow through a saturated cell without an artificial sequential overflow.
    This primitive verifies the proposed solution; it never clips or solves fluxes.
    A cell cannot finish below min(initial content, FC). No external water enters.
    """
    return _apply_downward_transport(storage, interface_l, source)


def apply_downward_infiltration(storage, surface_inflow_l, interface_l, source):
    """Verify solved infiltration volumes; this phase cannot remove existing storage.

    Surface inflow is the amount actually admitted, after rejected water has been
    accounted for outside the soil. Every patch needs an explicit amount, even zero.
    Evaporation, root uptake and drainage of pre-existing water are separate phases.
    """
    if not isinstance(surface_inflow_l, dict):
        raise ValueError("supply exactly one surface inflow per patch")
    return _apply_downward_transport(storage, interface_l, source, surface_inflow_l=surface_inflow_l)


def _apply_downward_transport(storage, interface_l, source, *, surface_inflow_l=None):
    if not isinstance(storage, SoilStorage):
        raise ValueError("supply validated soil storage")
    text("transport source", source)
    expected = {(patch.id, cell.id) for patch in storage.patches for cell in patch.compartments}
    if not isinstance(interface_l, dict) or set(interface_l) != expected:
        raise ValueError("supply exactly one transport interface per compartment")
    flows = {key: _quantity("interface litres", value, 10**16) for key, value in interface_l.items()}
    infiltrating = surface_inflow_l is not None
    if infiltrating:
        if not isinstance(surface_inflow_l, dict) or set(surface_inflow_l) != {p.id for p in storage.patches}:
            raise ValueError("supply exactly one surface inflow per patch")
        inflows = {key: _quantity("surface inflow litres", value, 10**16) for key, value in surface_inflow_l.items()}
    else:
        inflows = {patch.id: Fraction(0) for patch in storage.patches}
    patches, applied, residuals = [], [], []
    external_out = transferred = Fraction(0)
    for patch in storage.patches:
        incoming, cells = inflows[patch.id], []
        if infiltrating:
            request = SoilFlux("infiltration", patch.id, None, patch.compartments[0].id, incoming, source)
            applied.append(AppliedSoilFlux(request, incoming, Fraction(0), ()))
        for index, cell in enumerate(patch.compartments):
            size = 1000 * storage.area_m2 * patch.area_fraction * cell.thickness_m
            outgoing = flows[(patch.id, cell.id)]
            ending_l = size * cell.water_content + incoming - outgoing
            floor_l = size * (cell.water_content if infiltrating else min(cell.water_content, cell.field_capacity))
            if not floor_l <= ending_l <= size * cell.saturation:
                raise ValueError("solved transport violates compartment storage bounds")
            updated = replace(cell, water_content=ending_l / size)
            cells.append(updated)
            receiver = patch.compartments[index + 1].id if index + 1 < len(patch.compartments) else None
            request = SoilFlux("transfer" if receiver else "drainage", patch.id, cell.id, receiver, outgoing, source)
            applied.append(AppliedSoilFlux(request, outgoing, Fraction(0), ()))
            residuals.append((patch.id, cell.id, size * (updated.water_content - cell.water_content) - incoming + outgoing))
            if receiver:
                transferred += outgoing
            else:
                external_out += outgoing
            incoming = outgoing
        patches.append(replace(patch, compartments=tuple(cells)))
    mode = "infiltration" if infiltrating else "drainage"
    ending = replace(storage, patches=tuple(patches), source=f"Accounted {mode} from {MODEL_VERSION}; see initial state and flux sources")
    external_in = sum(inflows.values(), Fraction(0))
    residual = ending.water_l - storage.water_l - external_in + external_out
    if residual or any(value for _, _, value in residuals):
        raise ArithmeticError("soil transport accounting failed conservation")
    return SoilStorageChange(storage, ending, tuple(applied), external_in, external_out,
                             transferred, residual, tuple(residuals),
                             application_mode=f"simultaneous_downward_{mode}")
