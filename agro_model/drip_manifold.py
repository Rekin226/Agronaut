"""One surveyed, closed-tail distribution manifold feeding surveyed drip laterals.

Reverse shooting preserves a different pressure at every takeoff. The source curve
is evaluated at the sum of branch flows, not separately for each connection.
"""

from dataclasses import asdict, dataclass
from math import fsum

from .drip_lateral import (
    InletPoint,
    _solve_with_direction,
    evaluate_drip_lateral,
    interpolate_pressure_points,
    validate_pressure_points,
)
from .drip_zone import Lateral
from .hydraulics import EPA_ALGORITHMS, EPA_NETWORK, Pipe, pipe_loss
from .validation import number, text

MODEL_VERSION = 'drip-manifold-1-surveyed-distribution'


@dataclass(frozen=True)
class Supply:
    curve: tuple[InletPoint, ...]
    max_static_head_m: float
    max_flow_lph: float
    head_reference: str
    configuration: str
    source: str

    def __post_init__(self):
        validate_pressure_points(self.curve)
        number('max_static_head_m', self.max_static_head_m, self.curve[0].pressure_head_m, 10000)
        number('max_flow_lph', self.max_flow_lph, high=1e9, positive=True)
        if self.head_reference != 'manifold_inlet_pressure':
            raise ValueError('head_reference must be manifold_inlet_pressure, not lateral pressure or pump head gain')
        text('source configuration', self.configuration)
        text('source evidence', self.source)

    def head_at(self, flow):
        return interpolate_pressure_points(self.curve, flow)


@dataclass(frozen=True)
class ManifoldLimits:
    viscosity_m2_s: float
    max_velocity_m_s: float
    source: str

    def __post_init__(self):
        number('manifold viscosity_m2_s', self.viscosity_m2_s, 1e-8, 1e-3)
        number('manifold max_velocity_m_s', self.max_velocity_m_s, high=100, positive=True)
        text('manifold limits source', self.source)


@dataclass(frozen=True)
class ManifoldConditions:
    closed_tail: bool
    surveyed_profile: bool
    all_active_laterals_included: bool
    steady_source_configuration: bool
    no_unmodeled_connections: bool
    source: str

    def __post_init__(self):
        for key in ('closed_tail', 'surveyed_profile', 'all_active_laterals_included',
                    'steady_source_configuration', 'no_unmodeled_connections'):
            if getattr(self, key) is not True:
                raise ValueError(key+' must be explicitly true')
        text('manifold applicability source', self.source)


@dataclass(frozen=True)
class Takeoff:
    distance_m: float
    elevation_m: float
    upstream_minor_k: float
    source: str
    lateral: Lateral

    def __post_init__(self):
        number('takeoff distance_m', self.distance_m, .001, 10000)
        number('takeoff elevation_m', self.elevation_m, -1000, 1000)
        number('takeoff upstream_minor_k', self.upstream_minor_k, 0, 1e6)
        text('takeoff survey and fitting source', self.source)
        if not isinstance(self.lateral, Lateral):
            raise ValueError('takeoff requires a validated lateral')


@dataclass(frozen=True)
class DripManifold:
    supply: Supply
    pipe: Pipe
    limits: ManifoldLimits
    conditions: ManifoldConditions
    takeoffs: tuple[Takeoff, ...]

    def __post_init__(self):
        for name, cls in (('supply', Supply), ('pipe', Pipe), ('limits', ManifoldLimits), ('conditions', ManifoldConditions)):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(name+' must be a validated '+cls.__name__)
        if not isinstance(self.takeoffs, tuple) or not 1 <= len(self.takeoffs) <= 20:
            raise ValueError('manifold requires 1–20 ordered takeoffs')
        ids, distance, elevation, count = set(), 0, 0, 0
        for node in self.takeoffs:
            if not isinstance(node, Takeoff) or node.lateral.identifier in ids:
                raise ValueError('takeoffs must be validated and lateral identifiers unique')
            if node.lateral.identifier == 'inlet':
                raise ValueError("lateral identifier 'inlet' is reserved for the manifold boundary")
            if node.distance_m <= distance or abs(node.elevation_m-elevation) > node.distance_m-distance:
                raise ValueError('takeoff distances must increase and elevation changes cannot exceed segment length')
            if self.supply.max_static_head_m-node.elevation_m > 10000:
                raise ValueError('static pressure at each lateral inlet must be within the 10000 m software envelope')
            ids.add(node.lateral.identifier)
            distance, elevation = node.distance_m, node.elevation_m
            count += len(node.lateral.stations)
        if count > 2000:
            raise ValueError('manifold supports at most 2000 emitters across all laterals')


def _trial(spec, tail_head):
    head, flow, rows = tail_head, 0.0, []
    maximum_source = spec.supply.curve[0].pressure_head_m
    for index in range(len(spec.takeoffs)-1, -1, -1):
        takeoff = spec.takeoffs[index]
        if head < .001:
            return 1, None
        if head > min(1000, maximum_source-takeoff.elevation_m):
            return -1, None
        series = takeoff.lateral.at_head(head, spec.supply.max_static_head_m-takeoff.elevation_m, spec.supply.source)
        movement, branch = _solve_with_direction(series)
        if branch is None:
            return movement, None
        downstream = flow
        flow += branch['inlet_flow_lph']
        previous = spec.takeoffs[index-1] if index else None
        loss = pipe_loss(spec.pipe, flow_lph=flow,
            length_m=takeoff.distance_m-(previous.distance_m if previous else 0),
            viscosity_m2_s=spec.limits.viscosity_m2_s, minor_k=takeoff.upstream_minor_k)
        rows.append(dict(identifier=takeoff.lateral.identifier, distance_m=takeoff.distance_m,
            elevation_m=takeoff.elevation_m, pressure_head_m=head, lateral_flow_lph=branch['inlet_flow_lph'],
            upstream_flow_lph=flow, downstream_flow_lph=downstream, upstream_segment_loss=loss))
        head += loss['total_head_m']+takeoff.elevation_m-(previous.elevation_m if previous else 0)
    if flow < spec.supply.curve[0].flow_lph:
        return 1, None
    if flow > spec.supply.curve[-1].flow_lph:
        return -1, None
    available = spec.supply.head_at(flow)
    residual = head-available
    return (1 if residual < 0 else -1), dict(nodes=list(reversed(rows)), total_flow_lph=flow,
        source_pressure_head_m=available, required_source_head_m=head, source_head_residual_m=residual)


def _solve(spec):
    low = .001
    high = min(1000, spec.supply.curve[0].pressure_head_m-spec.takeoffs[-1].elevation_m)
    if high < low:
        return None
    tolerance = 1e-8+spec.supply.curve[0].pressure_head_m*1e-9
    for index, head in enumerate((low, high)):
        movement, trial = _trial(spec, head)
        if trial is not None and abs(trial['source_head_residual_m']) <= tolerance:
            return dict(trial, solver_iterations=index+1, source_head_tolerance_m=tolerance)
        if movement == 0 or (index == 0 and movement < 0) or (index == 1 and movement > 0):
            return None
    for iteration in range(80):
        head = (low+high)/2
        movement, trial = _trial(spec, head)
        if trial is not None and abs(trial['source_head_residual_m']) <= tolerance:
            return dict(trial, solver_iterations=iteration+3, source_head_tolerance_m=tolerance)
        if movement == 0 or head in (low, high):
            return None
        if movement > 0:
            low = head
        else:
            high = head
    return None


def evaluate_drip_manifold(spec):
    if not isinstance(spec, DripManifold):
        raise ValueError('spec must be a validated DripManifold')
    constraints, static, lateral_static = [], [], []

    def check(code, actual, maximum, identifier, minimum=False):
        violated = actual < maximum if minimum else actual > maximum
        if violated:
            constraints.append(dict(code=code, identifier=identifier, actual=actual,
                                    required_min=maximum if minimum else None, allowed_max=None if minimum else maximum))

    for identifier, elevation in [('inlet', 0), *((t.lateral.identifier, t.elevation_m) for t in spec.takeoffs)]:
        head = spec.supply.max_static_head_m-elevation
        static.append(dict(identifier=identifier, pressure_head_m=head))
        check('manifold_static_head_m', head, spec.pipe.max_head_m, identifier)
        check('manifold_static_head_below_zero', head, 0, identifier, minimum=True)
    for takeoff in spec.takeoffs:
        lateral, rows = takeoff.lateral, []
        for identifier, elevation in [('inlet', 0), *((s.identifier, s.elevation_m) for s in lateral.stations)]:
            head = spec.supply.max_static_head_m-takeoff.elevation_m-elevation
            rows.append(dict(identifier=identifier, pressure_head_m=head))
            for code, limit in [('pipe_static_head_m', lateral.pipe.max_head_m),
                                *([('emitter_static_head_m', lateral.emitter.max_static_head_m)] if identifier != 'inlet' else [])]:
                if head > limit:
                    constraints.append(dict(lateral=lateral.identifier, code=code, identifier=identifier, actual=head,
                                            required_min=None, allowed_max=limit))
            if head < 0:
                constraints.append(dict(lateral=lateral.identifier, code='static_head_below_zero', identifier=identifier,
                                        actual=head, required_min=0, allowed_max=None))
        lateral_static.append(dict(lateral=lateral.identifier, nodes=rows))
    prediction = _solve(spec)
    if prediction is None:
        constraints.append(dict(code='no_supported_manifold_operating_point', identifier=None, actual=None,
                                required_min=None, allowed_max=None))
    else:
        previous_head, previous_z = prediction['source_pressure_head_m'], 0
        for node, takeoff in zip(prediction['nodes'], spec.takeoffs):
            node['head_balance_residual_m'] = (previous_head+previous_z-node['pressure_head_m']-node['elevation_m']
                                               -node['upstream_segment_loss']['total_head_m'])
            node['mass_balance_residual_lph'] = node['upstream_flow_lph']-node['lateral_flow_lph']-node['downstream_flow_lph']
            previous_head, previous_z = node['pressure_head_m'], node['elevation_m']
            node['lateral_result'] = evaluate_drip_lateral(takeoff.lateral.at_head(node['pressure_head_m'],
                spec.supply.max_static_head_m-takeoff.elevation_m, spec.supply.source))
            if node['lateral_result']['prediction'] is None:
                raise ValueError('internal solver inconsistency: a manifold solution lost a lateral')
            for c in node['lateral_result']['constraints']:
                if c['code'] not in ('pipe_static_head_m', 'emitter_static_head_m', 'static_head_below_zero'):
                    constraints.append(dict(c, lateral=takeoff.lateral.identifier))
            check('manifold_velocity_m_s', node['upstream_segment_loss']['velocity_m_s'], spec.limits.max_velocity_m_s, takeoff.lateral.identifier)
        check('source_flow_lph', prediction['total_flow_lph'], spec.supply.max_flow_lph, 'inlet')
        prediction['emitter_mass_residual_lph'] = prediction['total_flow_lph']-fsum(
            emitter['emitter_flow_lph'] for node in prediction['nodes'] for emitter in node['lateral_result']['prediction']['nodes'])
    return dict(model_version=MODEL_VERSION, evidence_status='experimental', event_kind='scenario', inputs=asdict(spec),
        prediction=prediction, static_profile=static, lateral_static_profiles=lateral_static, feasible=None if prediction is None else not constraints,
        feasibility_scope='One steady surveyed manifold and its simultaneous laterals, within declared curves and limits',
        constraints=constraints, binding_constraints=sorted({c['code'] for c in constraints}), schedule=None, missing_inputs=[],
        sources=sorted({EPA_ALGORITHMS, EPA_NETWORK, spec.supply.source, spec.pipe.source, spec.limits.source, spec.conditions.source,
            *(s for t in spec.takeoffs for s in (t.source,t.lateral.source,t.lateral.pipe.source,t.lateral.emitter.source,
                t.lateral.limits.source,t.lateral.conditions.source)), *(s.source for t in spec.takeoffs for s in t.lateral.stations)}),
        assumptions=[
            'One full constant-diameter manifold, closed immediately after the last takeoff, with one active surveyed lateral per takeoff and no omitted outflow.',
            'Manifold distances are cumulative pipe length and elevations are relative to its inlet. Each lateral survey instead starts at its own takeoff and uses that takeoff as its elevation zero.',
            'Both profiles are piecewise linear between recorded nodes without omitted crests or sags. Manifold through-fitting K uses incoming segment velocity; branch entry losses belong in that lateral.',
            'The source curve is pressure at the manifold inlet after upstream equipment versus TOTAL manifold flow for one stated configuration, not pump head gain or pressure at each lateral.',
            'Reverse shooting solves each branch at its distinct takeoff head, then accumulates total flow, Darcy-Weisbach losses and elevation upstream to match the bounded source curve.',
            'Nodal hydraulic-head model: kinetic-head recovery at takeoffs is not included. Static pressure assumes continued hydraulic connection to the supplied maximum source head.',
        ], limitations=[
            'Experimental nominal hydraulic scenario, not independent field validation, irrigation duration, crop-water delivery or equipment actuation.',
            'No loops, multiple supply boundaries, intermediate manifold branches, varying diameters within a pipe, omitted connections, dynamic valve/source operation, pump sizing or NPSH/efficiency model.',
            'No surge, air entry, leakage, clogging, manufacturing variability, subsurface emitter backpressure, soil wetting or cross-lateral uniformity guarantee.',
            'Source and emitter curves are never extrapolated. A withheld solution is a numerical/model-envelope outcome, not proof of general physical infeasibility.',
            'Software envelope: 1–20 takeoffs, 2000 total emitters, positive takeoff operating heads of 0.001–1000 m and takeoff maximum-static heads no greater than 10000 m.',
            'Surveys, equipment ratings, source configuration and applicability are supplied declarations requiring site review. No automatic pipe selection, bill of materials or irrigation schedule.',
        ])
