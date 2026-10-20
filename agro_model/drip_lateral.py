"""Steady pressure-dependent outflows on a surveyed, closed-tail drip lateral.

Reverse shooting exploits a series pipe's monotone head/flow equations. Only
pressures inside the supplied emitter-curve envelope are evaluated. This is a
nodal hydraulic-head model, without kinetic-head recovery at outlets.
"""

from dataclasses import asdict, dataclass
from math import fsum

from .hydraulics import EPA_ALGORITHMS, EPA_NETWORK, Pipe, pipe_loss
from .validation import number, text

MODEL_VERSION = "drip-lateral-1-pressure-curve"
CURVE_MODEL_VERSION = "drip-lateral-2-inlet-curve"
FAO_EMITTER = "https://www.fao.org/4/w4367e/w4367e0c.htm"


@dataclass(frozen=True)
class EmitterCurve:
    reference_flow_lph: float
    reference_head_m: float
    exponent: float
    min_head_m: float
    max_head_m: float
    max_static_head_m: float
    source: str

    def __post_init__(self):
        number("reference_flow_lph", self.reference_flow_lph, .001, 1000)
        number("min_head_m", self.min_head_m, .001, 1000)
        number("max_head_m", self.max_head_m, self.min_head_m, 1000)
        if self.max_head_m == self.min_head_m:
            raise ValueError("emitter curve needs a nonzero supported head interval")
        number("reference_head_m", self.reference_head_m, self.min_head_m, self.max_head_m)
        number("exponent", self.exponent, 0, 1)
        number("max_static_head_m", self.max_static_head_m, self.max_head_m, 10000)
        number("curve maximum flow_lph", self.flow(self.max_head_m), 0, 1000)
        text("emitter curve source", self.source)

    def flow(self, head):
        number("emitter pressure head", head, self.min_head_m, self.max_head_m)
        return self.reference_flow_lph*(head/self.reference_head_m)**self.exponent


@dataclass(frozen=True)
class Station:
    identifier: str
    distance_m: float
    elevation_m: float
    upstream_minor_k: float
    source: str

    def __post_init__(self):
        text("station identifier", self.identifier)
        if self.identifier == "inlet":
            raise ValueError("station identifier 'inlet' is reserved for the boundary")
        number("distance_m", self.distance_m, .001, 10000)
        number("elevation_m relative to inlet", self.elevation_m, -1000, 1000)
        number("upstream_minor_k", self.upstream_minor_k, 0, 1e6)
        text("station survey and fitting source", self.source)


@dataclass(frozen=True)
class Inlet:
    running_head_m: float
    max_static_head_m: float
    max_flow_lph: float
    source: str

    def __post_init__(self):
        number("running_head_m", self.running_head_m, .001, 1000)
        number("max_static_head_m", self.max_static_head_m, self.running_head_m, 10000)
        number("max_flow_lph", self.max_flow_lph, high=1e9, positive=True)
        text("inlet source", self.source)


@dataclass(frozen=True)
class InletPoint:
    flow_lph: float
    pressure_head_m: float

    def __post_init__(self):
        number("curve flow_lph", self.flow_lph, 0, 1e9)
        number("curve pressure_head_m", self.pressure_head_m, .001, 1000)


@dataclass(frozen=True)
class InletCurve:
    curve: tuple[InletPoint, ...]
    max_static_head_m: float
    max_flow_lph: float
    head_reference: str
    configuration: str
    source: str

    def __post_init__(self):
        validate_pressure_points(self.curve)
        number("max_static_head_m", self.max_static_head_m, self.curve[0].pressure_head_m, 10000)
        number("max_flow_lph", self.max_flow_lph, high=1e9, positive=True)
        if self.head_reference != "lateral_inlet_pressure":
            raise ValueError("head_reference must be lateral_inlet_pressure, not raw pump head gain")
        text("source configuration", self.configuration)
        text("inlet curve source", self.source)

    def head_at(self, flow_lph):
        return interpolate_pressure_points(self.curve, flow_lph)


def validate_pressure_points(curve):
    if not isinstance(curve, tuple) or not 2 <= len(curve) <= 100:
        raise ValueError("inlet curve requires 2–100 supplied points")
    for i, point in enumerate(curve):
        if not isinstance(point, InletPoint):
            raise ValueError("inlet curve requires validated InletPoint records")
        if i and (point.flow_lph <= curve[i-1].flow_lph or point.pressure_head_m > curve[i-1].pressure_head_m):
            raise ValueError("curve flows must increase strictly and pressure must not increase")


def interpolate_pressure_points(curve, flow_lph):
    number("inlet curve flow", flow_lph, curve[0].flow_lph, curve[-1].flow_lph)
    for left, right in zip(curve, curve[1:]):
        if flow_lph <= right.flow_lph:
            fraction = (flow_lph-left.flow_lph)/(right.flow_lph-left.flow_lph)
            return left.pressure_head_m + fraction*(right.pressure_head_m-left.pressure_head_m)
    raise ValueError("flow outside supplied inlet curve")


@dataclass(frozen=True)
class Limits:
    viscosity_m2_s: float
    max_velocity_m_s: float
    max_flow_variation_fraction: float
    source: str

    def __post_init__(self):
        number("viscosity_m2_s", self.viscosity_m2_s, 1e-8, 1e-3)
        number("max_velocity_m_s", self.max_velocity_m_s, high=100, positive=True)
        number("max_flow_variation_fraction", self.max_flow_variation_fraction, 0, 1)
        text("lateral limit source", self.source)


@dataclass(frozen=True)
class Conditions:
    closed_tail: bool
    surface_emitters: bool
    steady_inlet_head: bool
    surveyed_profile: bool
    identical_emitters: bool
    source: str

    def __post_init__(self):
        for key in ("closed_tail", "surface_emitters", "steady_inlet_head", "surveyed_profile", "identical_emitters"):
            if getattr(self, key) is not True:
                raise ValueError(f"{key} must be explicitly true")
        text("lateral applicability source", self.source)


@dataclass(frozen=True)
class DripLateral:
    pipe: Pipe
    emitter: EmitterCurve
    inlet: Inlet | InletCurve
    limits: Limits
    conditions: Conditions
    stations: tuple[Station, ...]

    def __post_init__(self):
        if not isinstance(self.inlet, (Inlet, InletCurve)):
            raise ValueError("inlet must be a validated Inlet or InletCurve")
        for name, cls in (("pipe", Pipe), ("emitter", EmitterCurve),
                          ("limits", Limits), ("conditions", Conditions)):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be a validated {cls.__name__}")
        if not isinstance(self.stations, tuple) or not 1 <= len(self.stations) <= 2000:
            raise ValueError("stations must contain 1–2000 ordered emitter stations")
        identifiers, distance, elevation = set(), 0, 0
        for row in self.stations:
            if not isinstance(row, Station):
                raise ValueError("stations must be validated Station records")
            if row.identifier in identifiers or row.distance_m <= distance:
                raise ValueError("station IDs must be unique and pipe distances strictly increasing")
            if abs(row.elevation_m-elevation) > row.distance_m-distance:
                raise ValueError("elevation change cannot exceed surveyed pipe segment length")
            identifiers.add(row.identifier)
            distance, elevation = row.distance_m, row.elevation_m


def _upstream(spec, tail_head):
    """Return search direction or an admissible trial, starting at the last outlet.

    Each head and cumulative flow increases with tail head. A low/high envelope
    violation therefore supplies a bisection direction without extrapolating q(h).
    An empty admissible interval produces no accepted residual and is withheld.
    """
    head, flow, rows = tail_head, 0.0, []
    for index in range(len(spec.stations)-1, -1, -1):
        if head < spec.emitter.min_head_m:
            return -1, None
        if head > spec.emitter.max_head_m:
            return 1, None
        station = spec.stations[index]
        previous = spec.stations[index-1] if index else None
        discharge = spec.emitter.flow(head)
        flow += discharge
        loss = pipe_loss(spec.pipe, flow_lph=flow,
                         length_m=station.distance_m-(previous.distance_m if previous else 0),
                         viscosity_m2_s=spec.limits.viscosity_m2_s, minor_k=station.upstream_minor_k)
        rows.append(dict(identifier=station.identifier, distance_m=station.distance_m,
                         elevation_m=station.elevation_m, pressure_head_m=head,
                         emitter_flow_lph=discharge, upstream_segment_flow_lph=flow,
                         upstream_segment_loss=loss))
        head += loss["total_head_m"] + station.elevation_m-(previous.elevation_m if previous else 0)
    if isinstance(spec.inlet, InletCurve):
        if flow < spec.inlet.curve[0].flow_lph:
            return -1, None
        if flow > spec.inlet.curve[-1].flow_lph:
            return 1, None
        supplied_head = spec.inlet.head_at(flow)
    else:
        supplied_head = spec.inlet.running_head_m
    residual = head-supplied_head
    return (-1 if residual < 0 else 1), dict(nodes=list(reversed(rows)), inlet_flow_lph=flow,
        inlet_head_residual_m=residual, inlet_pressure_head_m=supplied_head)


def _solve_with_direction(spec):
    """Return (needed inlet-head movement, solution) for bounded series solving.

    With fixed inlet pressure, +1 means the supported lateral requires a higher
    inlet head and -1 means lower. Zero with no solution means no admissible trial
    was found; callers must withhold rather than extrapolate emitter curves.
    """
    low, high = spec.emitter.min_head_m, spec.emitter.max_head_m
    maximum_head = (spec.inlet.curve[0].pressure_head_m if isinstance(spec.inlet, InletCurve)
                    else spec.inlet.running_head_m)
    tolerance = 1e-9 + maximum_head*1e-10
    closest = None
    # Test endpoints too: a valid solution can lie exactly on a supported bound.
    for iterations, tail in enumerate((low, high), 1):
        direction, trial = _upstream(spec, tail)
        if trial is not None and (closest is None or abs(trial["inlet_head_residual_m"]) < abs(closest)):
            closest = trial["inlet_head_residual_m"]
        if trial is not None and abs(trial["inlet_head_residual_m"]) <= tolerance:
            return 0, dict(trial, solver_iterations=iterations, head_tolerance_m=tolerance)
        if (tail == low and direction > 0) or (tail == high and direction < 0):
            return (direction if trial is not None else 0), None
    for iteration in range(80):
        midpoint = (low+high)/2
        direction, trial = _upstream(spec, midpoint)
        if trial is not None and (closest is None or abs(trial["inlet_head_residual_m"]) < abs(closest)):
            closest = trial["inlet_head_residual_m"]
        if trial is not None and abs(trial["inlet_head_residual_m"]) <= tolerance:
            return 0, dict(trial, solver_iterations=iteration+3, head_tolerance_m=tolerance)
        if midpoint in (low, high):
            break
        if direction < 0:
            low = midpoint
        else:
            high = midpoint
    return (0 if closest is None else 1 if closest > 0 else -1), None


def _solve(spec):
    return _solve_with_direction(spec)[1]


def evaluate_drip_lateral(spec: DripLateral) -> dict:
    if not isinstance(spec, DripLateral):
        raise ValueError("spec must be a validated DripLateral")
    constraints = []
    curved = isinstance(spec.inlet, InletCurve)

    def check(code, value, limit, identifier=None, minimum=False):
        number(code, value)
        if (value < limit if minimum else value > limit):
            constraints.append(dict(code=code, identifier=identifier, actual=value,
                                    required_min=limit if minimum else None,
                                    allowed_max=None if minimum else limit))

    static = []
    for identifier, elevation in [("inlet", 0), *((s.identifier, s.elevation_m) for s in spec.stations)]:
        head = spec.inlet.max_static_head_m-elevation
        static.append(dict(identifier=identifier, pressure_head_m=head))
        check("pipe_static_head_m", head, spec.pipe.max_head_m, identifier)
        check("static_head_below_zero", head, 0, identifier, minimum=True)
        if identifier != "inlet":
            check("emitter_static_head_m", head, spec.emitter.max_static_head_m, identifier)
    prediction = _solve(spec)
    if prediction is None:
        code = "no_solution_within_inlet_and_emitter_curves" if curved else "no_solution_within_emitter_curve_envelope"
        constraints.append(dict(code=code, identifier=None,
                                actual=None, required_min=None, allowed_max=None))
    else:
        nodes = prediction["nodes"]
        flows = [row["emitter_flow_lph"] for row in nodes]
        mean = fsum(flows)/len(flows)
        variation = (max(flows)-min(flows))/max(flows)
        peak_velocity = max(row["upstream_segment_loss"]["velocity_m_s"] for row in nodes)
        check("inlet_flow_lph", prediction["inlet_flow_lph"], spec.inlet.max_flow_lph)
        check("velocity_m_s", peak_velocity, spec.limits.max_velocity_m_s)
        check("flow_variation_fraction", variation, spec.limits.max_flow_variation_fraction)
        previous_head, previous_elevation = prediction["inlet_pressure_head_m"], 0
        for row in nodes:
            row["head_balance_residual_m"] = (previous_head+previous_elevation-row["pressure_head_m"]
                -row["elevation_m"]-row["upstream_segment_loss"]["total_head_m"])
            previous_head, previous_elevation = row["pressure_head_m"], row["elevation_m"]
        prediction.update(min_emitter_flow_lph=min(flows), max_emitter_flow_lph=max(flows),
                          mean_emitter_flow_lph=mean, hydraulic_min_to_mean_fraction=min(flows)/mean,
                          flow_variation_fraction=variation, peak_velocity_m_s=peak_velocity,
                          min_pressure_head_m=min(row["pressure_head_m"] for row in nodes),
                          max_pressure_head_m=max(row["pressure_head_m"] for row in nodes),
                          mass_balance_residual_lph=prediction["inlet_flow_lph"]-fsum(flows))
    # Preserve the fixed-pressure v1 result contract, including its prediction fields.
    if prediction is not None and not curved:
        prediction.pop("inlet_pressure_head_m")
    return dict(model_version=CURVE_MODEL_VERSION if curved else MODEL_VERSION, evidence_status="experimental", event_kind="scenario",
        inputs=asdict(spec), prediction=prediction, static_profile=static,
        feasible=None if prediction is None else not constraints,
        feasibility_scope=("One steady operating point within supplied inlet/emitter curves and declared lateral limits only"
                           if curved else "One supplied steady inlet-head scenario and declared lateral limits only"),
        constraints=constraints, binding_constraints=sorted({c["code"] for c in constraints}),
        schedule=None, missing_inputs=[],
        sources=sorted({EPA_ALGORITHMS, EPA_NETWORK, FAO_EMITTER, spec.pipe.source,
            spec.emitter.source, spec.inlet.source, spec.limits.source, spec.conditions.source,
            *(row.source for row in spec.stations)}),
        assumptions=[
            "One constant-diameter full pipe, closed immediately after its last emitter; one identical surface emitter at every surveyed station.",
            "Station distances are cumulative pipe lengths; elevations are relative to the inlet and linear between stations, with no omitted crest or sag.",
            ("Inlet pressure is piecewise-linear in total lateral flow, only between supplied points for the declared configuration; it is measured or justified after upstream equipment, not raw pump head gain."
             if curved else "Inlet pressure is supplied after upstream filters/manifolds and maintained at the calculated total flow; it is not a pump shutoff pressure."),
            "Emitter flow = reference_flow_lph * (pressure_head_m/reference_head_m)^exponent, only within its supplied supported head interval.",
            "Nodal hydraulic-head losses use Darcy-Weisbach and supplied fitting K at upstream segment velocity; no kinetic-head recovery at outlets.",
            "Static pressure assumes hydraulic connection to the inlet at its supplied maximum static head; isolation and drainage are not modeled.",
        ], limitations=[
            "Experimental nominal hydraulic scenario, not independently field validated or an irrigation runtime recommendation.",
            "No manufacturing variability, clogging, leakage, temperature-dependent emitter behavior, subsurface backpressure, surge or air-entry simulation.",
            "Flow variation and minimum/mean ratios describe predicted hydraulic flows, not measured distribution uniformity or guaranteed crop-water delivery.",
            "A withheld solution means no supported numerical prediction was found inside the curve envelope, not proof of general physical infeasibility.",
            ("No pump selection, suction/NPSH, efficiency, upstream network solution, changing source settings, other concurrent loads, branched/looped pipes, soil wetting or crop-water schedule."
             if curved else "No pump curve, upstream network interaction, branched/looped network, automatic pipe sizing, soil wetting, crop demand or seasonal schedule."),
            "Supported curve bounds, survey, ratings, inlet conditions and acceptance limits are supplied evidence, not independently verified facts.",
        ])
