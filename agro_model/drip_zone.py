"""Coupled parallel laterals at one hydraulic junction with a bounded source curve."""

from dataclasses import asdict, dataclass
from math import fsum

from .drip_lateral import (
    Conditions,
    DripLateral,
    EmitterCurve,
    Inlet,
    InletCurve,
    Limits,
    Station,
    _solve_with_direction,
    evaluate_drip_lateral,
)
from .hydraulics import EPA_ALGORITHMS, EPA_NETWORK, Pipe
from .validation import number, text

MODEL_VERSION = 'drip-zone-1-common-junction'


@dataclass(frozen=True)
class Lateral:
    identifier: str
    max_flow_lph: float
    source: str
    pipe: Pipe
    emitter: EmitterCurve
    limits: Limits
    conditions: Conditions
    stations: tuple[Station, ...]

    def __post_init__(self):
        text('lateral identifier', self.identifier)
        text('lateral geometry/capacity source', self.source)
        number('lateral max_flow_lph', self.max_flow_lph, high=1e9, positive=True)
        # Reuse the exact surveyed-series validation. This is not a design result.
        self.at_head(1, 1, 'Validation boundary only')

    def at_head(self, head, static, source):
        return DripLateral(self.pipe, self.emitter, Inlet(head, static, self.max_flow_lph, source),
                           self.limits, self.conditions, self.stations)


@dataclass(frozen=True)
class JunctionConditions:
    common_hydraulic_junction: bool
    all_active_laterals_included: bool
    no_unmodeled_connection_losses: bool
    steady_source_configuration: bool
    source: str

    def __post_init__(self):
        for key in ('common_hydraulic_junction', 'all_active_laterals_included',
                    'no_unmodeled_connection_losses', 'steady_source_configuration'):
            if getattr(self, key) is not True:
                raise ValueError(key+' must be explicitly true')
        text('junction applicability source', self.source)


@dataclass(frozen=True)
class DripZone:
    supply: InletCurve
    junction: JunctionConditions
    laterals: tuple[Lateral, ...]

    def __post_init__(self):
        if not isinstance(self.supply, InletCurve) or not isinstance(self.junction, JunctionConditions):
            raise ValueError('supply and junction must be validated records')
        if not isinstance(self.laterals, tuple) or not 2 <= len(self.laterals) <= 20:
            raise ValueError('zone requires 2–20 parallel laterals')
        identifiers, count = set(), 0
        for lateral in self.laterals:
            if not isinstance(lateral, Lateral) or lateral.identifier in identifiers:
                raise ValueError('laterals must be validated and have unique identifiers')
            identifiers.add(lateral.identifier)
            count += len(lateral.stations)
        if count > 2000:
            raise ValueError('zone supports at most 2000 surveyed emitters across its laterals')


def _trial(spec, head):
    flows, movements = [], set()
    for lateral in spec.laterals:
        series = lateral.at_head(head, spec.supply.max_static_head_m, spec.supply.source)
        movement, result = _solve_with_direction(series)
        if result is None:
            if movement == 0:
                return 0, None
            movements.add(movement)
        else:
            flows.append(result['inlet_flow_lph'])
    if movements:
        # Simultaneously needing higher and lower junction pressure cannot produce
        # a common supported operating point. No branch is silently dropped.
        return (next(iter(movements)) if len(movements) == 1 else 0), None
    total = fsum(flows)
    if total < spec.supply.curve[0].flow_lph:
        return 1, None
    if total > spec.supply.curve[-1].flow_lph:
        return -1, None
    available = spec.supply.head_at(total)
    residual = head-available
    return (1 if residual < 0 else -1), dict(junction_pressure_head_m=head, total_flow_lph=total,
        supply_pressure_head_m=available, source_head_residual_m=residual)


def _solve_zone(spec):
    low, high = spec.supply.curve[-1].pressure_head_m, spec.supply.curve[0].pressure_head_m
    tolerance = 1e-8+high*1e-9
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


def evaluate_drip_zone(spec):
    if not isinstance(spec, DripZone):
        raise ValueError('spec must be a validated DripZone')
    constraints, static = [], []
    for lateral in spec.laterals:
        rows = []
        for identifier, elevation in [('inlet', 0), *((s.identifier, s.elevation_m) for s in lateral.stations)]:
            head = spec.supply.max_static_head_m-elevation
            rows.append(dict(identifier=identifier, pressure_head_m=head))
            for code, limit in [('pipe_static_head_m', lateral.pipe.max_head_m),
                                *([('emitter_static_head_m', lateral.emitter.max_static_head_m)] if identifier != 'inlet' else [])]:
                if head > limit:
                    constraints.append(dict(lateral=lateral.identifier, code=code, identifier=identifier, actual=head,
                                            required_min=None, allowed_max=limit))
            if head < 0:
                constraints.append(dict(lateral=lateral.identifier, code='static_head_below_zero', identifier=identifier,
                                        actual=head, required_min=0, allowed_max=None))
        static.append(dict(lateral=lateral.identifier, nodes=rows))
    prediction = _solve_zone(spec)
    if prediction is None:
        constraints.append(dict(lateral=None, code='no_common_operating_point_within_supplied_curves',
                                identifier=None, actual=None, required_min=None, allowed_max=None))
    else:
        results = [dict(identifier=lateral.identifier, result=evaluate_drip_lateral(lateral.at_head(
            prediction['junction_pressure_head_m'], spec.supply.max_static_head_m, spec.supply.source))) for lateral in spec.laterals]
        if any(row['result']['prediction'] is None for row in results):
            raise ValueError('internal solver inconsistency: a solved zone lost a lateral prediction')
        constraints = [dict(c, lateral=row['identifier']) for row in results for c in row['result']['constraints']]
        total = prediction['total_flow_lph']
        if total > spec.supply.max_flow_lph:
            constraints.append(dict(lateral=None, code='source_flow_lph', identifier=None, actual=total,
                                    required_min=None, allowed_max=spec.supply.max_flow_lph))
        prediction.update(laterals=results,
            junction_mass_residual_lph=total-fsum(row['result']['prediction']['inlet_flow_lph'] for row in results),
            emitter_mass_residual_lph=total-fsum(n['emitter_flow_lph'] for row in results for n in row['result']['prediction']['nodes']))
    return dict(model_version=MODEL_VERSION, evidence_status='experimental', event_kind='scenario', inputs=asdict(spec),
        prediction=prediction, static_profiles=static, feasible=None if prediction is None else not constraints,
        feasibility_scope='One simultaneous zone operating point at an explicitly common hydraulic junction',
        constraints=constraints, binding_constraints=sorted({c['code'] for c in constraints}), schedule=None, missing_inputs=[],
        sources=sorted({EPA_ALGORITHMS, EPA_NETWORK, spec.supply.source, spec.junction.source,
            *(source for lateral in spec.laterals for source in (lateral.source, lateral.pipe.source, lateral.emitter.source, lateral.limits.source, lateral.conditions.source)),
            *(s.source for lateral in spec.laterals for s in lateral.stations)}),
        assumptions=[
            'All represented laterals operate simultaneously from the same pressure/elevation junction. Survey distances and elevations start there; lateral fittings and connection losses must be included.',
            'The supply curve relates pressure at that junction to the total flow entering all represented laterals for one declared source configuration; no extrapolation or added source capacity.',
            'Each lateral retains its supplied emitter curve, diameter, surveyed profile and limits. Different lateral specifications are allowed; hydraulic uniformity is assessed within each lateral only.',
            'A bounded outer pressure solve enforces total-flow/source-head agreement; each inner series solve enforces node head balance and pressure-dependent emitter discharges.',
            'Maximum static pressure assumes all branches remain hydraulically connected to the supplied maximum inlet head. No valve isolation or transient behavior is inferred.',
        ], limitations=[
            'Experimental nominal hydraulic scenario; not independent field validation, irrigation duration, actuation or guaranteed delivery.',
            'Not a distributed manifold or arbitrary network solver. Distinct junction pressures, loops, unmodeled feeder losses and changing concurrent loads are unsupported.',
            'No pump selection, efficiency/NPSH, surge, air entry, leakage, clogging, manufacturing variation, subsurface backpressure, soil wetting or crop demand.',
            'A withheld solve means no supported numerical common operating point was found, not proof of physical infeasibility outside the declared model.',
            'Shared-pressure applicability, source curve, equipment evidence and the complete active branch set require review. Independent single-lateral operating points are not summed.',
            'Software envelope: 2–20 laterals and at most 2000 emitters. No automatic pipe sizing, total-zone uniformity claim or bill of materials.',
        ])
