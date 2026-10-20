"""Experimental two-point volume-balance advance for sloping furrows and borders.

FAO Irrigation and Drainage Paper 45 (1989), §§4.3.4 and 5.3.1:
Q*t = sigma_y*A0*x + sigma_z*k*x*t**a + f0*x*t/(1+r).
Half/full-length balances determine the power advance exponent r. Bracketed
solvers replace Newton iterations; no parameter is fitted to farm observations.
This is an advance approximation, not an unsteady hydraulic or recession model.
"""

from dataclasses import asdict, dataclass
from math import fsum, log, sqrt

from .surface import Geometry, Infiltration
from .validation import number, text

MODEL_VERSION = "surface-advance-1-fao45-two-point"
SOURCES = (
    "https://www.fao.org/4/t0231e/t0231e06.htm",
    "https://www.fao.org/4/T0231e/t0231e07.htm",
    "https://www.ars.usda.gov/ARSUserFiles/20200000/Bautista_et%20al.%20JIDE727%202012.pdf",
)


@dataclass(frozen=True)
class AdvanceHydraulics:
    inflow_lps: float
    manning_n: float
    section_p1: float
    section_p2: float
    surface_shape_factor: float
    source: str

    def __post_init__(self):
        number("inflow_lps", self.inflow_lps, 1e-9, 1e6)
        number("manning_n", self.manning_n, 1e-6, 1)
        number("section_p1", self.section_p1, 1e-6, 1e6)
        number("section_p2", self.section_p2, 1, 5/3)
        number("surface_shape_factor", self.surface_shape_factor, 1e-6, 1)
        text("hydraulics source", self.source)


@dataclass(frozen=True)
class AdvanceLimits:
    max_advance_min: float
    max_inlet_velocity_m_s: float
    max_inlet_flow_area_m2: float
    source: str

    def __post_init__(self):
        number("max_advance_min", self.max_advance_min, high=1440, positive=True)
        number("max_inlet_velocity_m_s", self.max_inlet_velocity_m_s, high=100, positive=True)
        number("max_inlet_flow_area_m2", self.max_inlet_flow_area_m2, high=1e6, positive=True)
        text("limits source", self.source)


@dataclass(frozen=True)
class AdvanceConditions:
    uniform_sloping_field: bool
    normal_inlet_flow: bool
    initially_unponded: bool
    no_rain_or_runon: bool
    constant_inflow: bool
    no_downstream_backwater: bool
    source: str

    def __post_init__(self):
        for key in ("uniform_sloping_field", "normal_inlet_flow", "initially_unponded",
                    "no_rain_or_runon", "constant_inflow", "no_downstream_backwater"):
            if getattr(self, key) is not True:
                raise ValueError(f"{key} must be explicitly true for this advance approximation")
        text("conditions source", self.source)


@dataclass(frozen=True)
class SurfaceAdvance:
    geometry: Geometry
    hydraulics: AdvanceHydraulics
    infiltration: Infiltration
    limits: AdvanceLimits
    conditions: AdvanceConditions

    def __post_init__(self):
        for name, cls in (("geometry", Geometry), ("hydraulics", AdvanceHydraulics),
                          ("infiltration", Infiltration), ("limits", AdvanceLimits),
                          ("conditions", AdvanceConditions)):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be {cls.__name__}")
        if self.geometry.method not in ("furrow", "border") or self.geometry.slope_m_m <= 0:
            raise ValueError("advance supports uniformly sloping furrows/borders only; no level basin")
        number("advance length_m", self.geometry.length_m, .001)
        number("advance width_m", self.geometry.width_m, .001)
        number("advance slope_m_m", self.geometry.slope_m_m, 1e-9)
        if self.infiltration.min_opportunity_min != 0 or self.infiltration.max_opportunity_min <= 0:
            raise ValueError("advance intake applicability must include zero opportunity time and a positive duration")
        if self.geometry.method == "border" and (
            self.hydraulics.section_p1 != 1 or self.hydraulics.section_p2 != 5/3
        ):
            raise ValueError("wide rectangular border flow requires section_p1=1 and section_p2=5/3")


def _shape(a, r):
    return (1+a+r*(1-a))/((1+a)*(1+r))


def _balance(q, surface_area, k, a, f0, r, x, t):
    surface = surface_area*x
    transient = _shape(a, r)*k*x*t**a
    steady = f0*x*t/(1+r)
    supplied = q*t
    return supplied-fsum((surface, transient, steady))


def _time(q, surface_area, k, a, f0, r, x, horizon):
    """The positive root, if bracketed without exceeding the intake time envelope."""
    if _balance(q, surface_area, k, a, f0, r, x, horizon) < 0:
        return None
    lo, hi = 0.0, horizon
    # The balance starts negative and is convex for 0<a<=1. A positive upper
    # endpoint brackets its sole positive root; no singular derivative at t=0.
    for _ in range(160):
        mid = (lo+hi)/2
        if mid == lo or mid == hi:
            break
        if _balance(q, surface_area, k, a, f0, r, x, mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


def _solve(q, surface_area, k, a, f0, length, horizon):
    def balance_at_end(r):
        return _balance(q, surface_area, k, a, f0, r, length, horizon)

    if balance_at_end(1) < 0:
        return None
    lo, hi = 0.0, 1.0
    if balance_at_end(0) < 0:
        # Shape factors decline with r, so this finds the smallest r whose
        # full-length time can be solved inside the declared time envelope.
        for _ in range(80):
            mid = (lo+hi)/2
            if balance_at_end(mid) < 0:
                lo = mid
            else:
                hi = mid
        lo = hi

    def residual(r):
        full = _time(q, surface_area, k, a, f0, r, length, horizon)
        half = _time(q, surface_area, k, a, f0, r, length/2, horizon)
        if full is None or half is None or not 0 < half < full:
            return None
        implied = log(2)/(log(full)-log(half))
        return r-implied, half, full

    left, right = residual(lo), residual(1)
    if left is None or right is None or left[0] > 1e-10 or right[0] < -1e-10:
        return None
    hi = 1.0
    for iterations in range(1, 81):
        r = hi if abs(right[0]) <= 1e-10 else (lo+hi)/2
        result = residual(r)
        if result is None:
            return None
        error, half, full = result
        if abs(error) <= 1e-10 and r > 0:
            return r, half, full, iterations, error
        if error < 0:
            lo = r
        else:
            hi, right = r, result
    return None


def predict_surface_advance(spec: SurfaceAdvance) -> dict:
    if not isinstance(spec, SurfaceAdvance):
        raise ValueError("spec must be SurfaceAdvance")
    geo, hyd, intake, limits = spec.geometry, spec.hydraulics, spec.infiltration, spec.limits
    q = hyd.inflow_lps*0.06  # whole represented furrow or border, m3/min
    width = geo.width_m if geo.method == "border" else 1.0
    # Q = 60*sqrt(S)/n * p1*A**p2. A and Q are per metre width for borders.
    area = width*(q/width*hyd.manning_n/(60*hyd.section_p1*sqrt(geo.slope_m_m)))**(1/hyd.section_p2)
    number("computed inlet area", area, positive=True)
    velocity = q/(60*area)
    number("computed inlet velocity", velocity, positive=True)
    factor = geo.width_m/1000 if intake.basis == "depth_mm" else 1.0
    k, f0 = intake.k*factor, intake.steady_rate*factor
    horizon = min(limits.max_advance_min, intake.max_opportunity_min)
    constraints = []
    if area > limits.max_inlet_flow_area_m2:
        constraints.append("inlet_flow_area_exceeds_limit")
    if velocity > limits.max_inlet_velocity_m_s:
        constraints.append("inlet_velocity_exceeds_limit")
    constant_intake = f0 + (k if intake.exponent == 1 else 0)
    if q <= constant_intake*geo.length_m:
        constraints.append("constant_intake_at_full_length_not_below_inflow")
    solution = None if constraints else _solve(
        q, hyd.surface_shape_factor*area, k, intake.exponent, f0, geo.length_m, horizon)
    prediction = None
    if solution is None:
        if not constraints:
            constraints.append("no_bracketed_advance_solution_within_time_envelope")
    else:
        r, half, full, iterations, error = solution
        anchors = []
        for x, t in ((geo.length_m/2, half), (geo.length_m, full)):
            surface = hyd.surface_shape_factor*area*x
            infiltrated = _shape(intake.exponent, r)*k*x*t**intake.exponent + f0*x*t/(1+r)
            residual = _balance(q, hyd.surface_shape_factor*area, k, intake.exponent, f0, r, x, t)
            anchors.append(dict(distance_m=x, advance_min=t, inflow_l=q*t*1000,
                                surface_storage_l=surface*1000, infiltration_l=infiltrated*1000,
                                balance_residual_l=residual*1000))
        prediction = dict(half_length_advance_min=half, full_length_advance_min=full,
                          advance_exponent=r, transient_intake_shape_factor=_shape(intake.exponent, r),
                          steady_intake_shape_factor=1/(1+r), anchors=anchors,
                          solver_iterations=iterations, exponent_residual=error)
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", event_kind="scenario",
                inputs=asdict(spec), prediction=prediction, binding_constraints=constraints,
                inlet=dict(flow_area_m2=area, velocity_m_s=velocity, inflow_lps=hyd.inflow_lps,
                           border_depth_m=area/geo.width_m if geo.method == "border" else None,
                           border_depth_width_ratio=area/geo.width_m**2 if geo.method == "border" else None),
                time_envelope_min=horizon, feasible=None, cutoff_recommendation=None,
                missing_inputs=[], sources=sorted({*SOURCES, *(getattr(spec, key).source for key in
                    ("geometry", "hydraulics", "infiltration", "limits", "conditions"))}),
                assumptions=[
                    "One initially unponded, uniform sloping furrow or wide rectangular border; constant inflow and no rain, runon or backwater.",
                    "Normal inlet flow, fixed cross-section/roughness and supplied surface-storage shape factor throughout advance.",
                    "Border flow is wide relative to its depth; the supplied supported-area limit must preserve this approximation.",
                    "Uniform Kostiakov-Lewis intake valid from zero opportunity time through the complete modeled advance.",
                    "Furrow width is represented spacing; volume-per-length intake already accounts for that represented strip.",
                ], limitations=[
                    "Experimental scenario, not field validation or an operational irrigation recommendation.",
                    "Only half/full-length volume balances are solved. A two-point power fit need not balance water at intermediate distances.",
                    "Shape-factor and normal-flow approximations can bias predictions; check independent advance and surface-storage observations.",
                    "No level basins, unsteady hydraulic solution, recession, cutoff optimization, tailwater, erosion simulation or postadvance water distribution.",
                    "Inlet velocity/area limits and parameter applicability are supplied evidence, not independently verified site safety.",
                    "An unresolved result does not prove real-world infeasibility; review inputs, duration, scope and numerical assumptions.",
                ])
