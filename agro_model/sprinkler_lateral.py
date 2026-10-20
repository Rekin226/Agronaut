"""Bounded steady nozzle/riser operating points on a surveyed series lateral."""

from dataclasses import asdict, dataclass
from math import fsum, sqrt

from .drip_lateral import Inlet, InletCurve, Limits, Station
from .hydraulics import EPA_ALGORITHMS, EPA_NETWORK, Pipe, pipe_loss
from .validation import number, text

MODEL_VERSION = 'sprinkler-lateral-1-nozzle-riser'


@dataclass(frozen=True)
class Nozzle:
    reference_flow_lph: float
    reference_head_m: float
    min_head_m: float
    max_head_m: float
    max_static_head_m: float
    source: str

    def __post_init__(self):
        number('reference_flow_lph', self.reference_flow_lph, .001, 1e6)
        number('min_head_m', self.min_head_m, .001, 1000)
        number('max_head_m', self.max_head_m, self.min_head_m, 1000)
        if self.min_head_m == self.max_head_m:
            raise ValueError('nozzle curve needs a nonzero supported head interval')
        number('reference_head_m', self.reference_head_m, self.min_head_m, self.max_head_m)
        number('max_static_head_m', self.max_static_head_m, self.max_head_m, 10000)
        number('maximum nozzle flow_lph', self.flow(self.max_head_m), high=1e6)
        text('nozzle curve source', self.source)

    def flow(self, head):
        number('nozzle pressure head', head, self.min_head_m, self.max_head_m)
        return self.reference_flow_lph*sqrt(head/self.reference_head_m)


@dataclass(frozen=True)
class SprinklerStation(Station):
    riser_height_m: float
    riser_length_m: float
    riser_minor_k: float

    def __post_init__(self):
        super().__post_init__()
        number('riser_height_m', self.riser_height_m, 0, 100)
        number('riser_length_m', self.riser_length_m, self.riser_height_m, 1000)
        number('riser_minor_k', self.riser_minor_k, 0, 1e6)


@dataclass(frozen=True)
class Conditions:
    closed_tail: bool
    steady_full_pipes: bool
    surveyed_profile: bool
    identical_nozzles: bool
    unregulated_atmospheric_outlets: bool
    source: str

    def __post_init__(self):
        for key in ('closed_tail','steady_full_pipes','surveyed_profile','identical_nozzles','unregulated_atmospheric_outlets'):
            if getattr(self,key) is not True:
                raise ValueError(f'{key} must be explicitly true')
        text('sprinkler applicability source', self.source)


@dataclass(frozen=True)
class SprinklerLateral:
    pipe: Pipe
    riser: Pipe
    nozzle: Nozzle
    inlet: Inlet | InletCurve
    limits: Limits
    conditions: Conditions
    stations: tuple[SprinklerStation, ...]

    def __post_init__(self):
        for key, cls in (('pipe',Pipe),('riser',Pipe),('nozzle',Nozzle),('inlet',(Inlet,InletCurve)),('limits',Limits),('conditions',Conditions)):
            if not isinstance(getattr(self,key),cls):
                raise ValueError(f'{key} must be a validated model record')
        if not isinstance(self.stations,tuple) or not 1<=len(self.stations)<=2000:
            raise ValueError('stations must contain 1–2000 ordered sprinklers')
        identifiers, distance, elevation = set(), 0, 0
        for station in self.stations:
            if not isinstance(station,SprinklerStation):
                raise ValueError('stations must be validated SprinklerStation records')
            if station.identifier in identifiers or station.distance_m<=distance:
                raise ValueError('station IDs must be unique and pipe distances strictly increasing')
            if abs(station.elevation_m-elevation)>station.distance_m-distance:
                raise ValueError('elevation change cannot exceed surveyed pipe segment length')
            identifiers.add(station.identifier)
            distance,elevation=station.distance_m,station.elevation_m
        number('maximum total nozzle flow_lph',len(self.stations)*self.nozzle.flow(self.nozzle.max_head_m),high=1e9)


def _branch(spec, station, nozzle_head):
    flow=spec.nozzle.flow(nozzle_head)
    loss=pipe_loss(spec.riser,flow_lph=flow,length_m=station.riser_length_m,
                   viscosity_m2_s=spec.limits.viscosity_m2_s,minor_k=station.riser_minor_k)
    return dict(nozzle_pressure_head_m=nozzle_head,nozzle_flow_lph=flow,riser_loss=loss,
                base_pressure_head_m=nozzle_head+station.riser_height_m+loss['total_head_m'])


def _outlet(spec, station, base_head, bounds):
    low,high=spec.nozzle.min_head_m,spec.nozzle.max_head_m
    if base_head<bounds[0]['base_pressure_head_m']:
        return -1,None
    if base_head>bounds[1]['base_pressure_head_m']:
        return 1,None
    for trial in bounds:
        if base_head==trial['base_pressure_head_m']:
            return 0,dict(trial)
    # Invert the monotonically increasing nozzle+riser relation without evaluating
    # the nozzle outside its declared pressure interval.
    for _ in range(80):
        midpoint=(low+high)/2
        trial=_branch(spec,station,midpoint)
        residual=trial['base_pressure_head_m']-base_head
        if abs(residual)<=1e-11:
            trial['riser_head_residual_m']=residual
            trial['base_pressure_head_m']=base_head
            return 0,trial
        if midpoint in (low,high):
            break
        if residual<0:
            low=midpoint
        else:
            high=midpoint
    return 0,None


def _trial(spec, tail_head, bounds):
    head,flow,rows=None,0.0,[]
    for index in range(len(spec.stations)-1,-1,-1):
        station=spec.stations[index]
        if head is None:
            row=_branch(spec,station,tail_head)
        else:
            direction,row=_outlet(spec,station,head,bounds[index])
            if row is None:
                return direction,None
        previous=spec.stations[index-1] if index else None
        flow+=row['nozzle_flow_lph']
        loss=pipe_loss(spec.pipe,flow_lph=flow,length_m=station.distance_m-(previous.distance_m if previous else 0),
                       viscosity_m2_s=spec.limits.viscosity_m2_s,minor_k=station.upstream_minor_k)
        row.update(identifier=station.identifier,distance_m=station.distance_m,elevation_m=station.elevation_m,
                   nozzle_elevation_m=station.elevation_m+station.riser_height_m,
                   upstream_segment_flow_lph=flow,upstream_segment_loss=loss)
        row.setdefault('riser_head_residual_m',0.0)
        rows.append(row)
        head=row['base_pressure_head_m']+loss['total_head_m']+station.elevation_m-(previous.elevation_m if previous else 0)
    if isinstance(spec.inlet,InletCurve):
        if flow<spec.inlet.curve[0].flow_lph:
            return -1,None
        if flow>spec.inlet.curve[-1].flow_lph:
            return 1,None
        supplied=spec.inlet.head_at(flow)
    else:
        supplied=spec.inlet.running_head_m
    residual=head-supplied
    return (-1 if residual<0 else 1),dict(nodes=list(reversed(rows)),inlet_flow_lph=flow,
        inlet_pressure_head_m=supplied,inlet_head_residual_m=residual)


def _solve(spec):
    low,high=spec.nozzle.min_head_m,spec.nozzle.max_head_m
    maximum=spec.inlet.curve[0].pressure_head_m if isinstance(spec.inlet,InletCurve) else spec.inlet.running_head_m
    tolerance=1e-8+maximum*1e-10
    bounds=[tuple(_branch(spec,s,h) for h in (low,high)) for s in spec.stations]
    for iteration,head in enumerate((low,high),1):
        direction,trial=_trial(spec,head,bounds)
        if trial is not None and abs(trial['inlet_head_residual_m'])<=tolerance:
            return dict(trial,solver_iterations=iteration,head_tolerance_m=tolerance)
        if direction==0 or (head==low and direction>0) or (head==high and direction<0):
            return None
    for iteration in range(80):
        midpoint=(low+high)/2
        direction,trial=_trial(spec,midpoint,bounds)
        if trial is not None and abs(trial['inlet_head_residual_m'])<=tolerance:
            return dict(trial,solver_iterations=iteration+3,head_tolerance_m=tolerance)
        if direction==0 or midpoint in (low,high):
            break
        if direction<0:
            low=midpoint
        else:
            high=midpoint
    return None


def evaluate_sprinkler_lateral(spec):
    if not isinstance(spec,SprinklerLateral):
        raise ValueError('spec must be a validated SprinklerLateral')
    constraints=[]

    def check(code,value,limit,identifier=None,minimum=False):
        number(code,value)
        if (value<limit if minimum else value>limit):
            constraints.append(dict(code=code,identifier=identifier,actual=value,
                required_min=limit if minimum else None,allowed_max=None if minimum else limit))

    static=[dict(identifier='inlet',base_pressure_head_m=spec.inlet.max_static_head_m,nozzle_pressure_head_m=None)]
    check('pipe_static_head_m',spec.inlet.max_static_head_m,spec.pipe.max_head_m,'inlet')
    for station in spec.stations:
        base=spec.inlet.max_static_head_m-station.elevation_m
        nozzle=base-station.riser_height_m
        static.append(dict(identifier=station.identifier,base_pressure_head_m=base,nozzle_pressure_head_m=nozzle))
        check('pipe_static_head_m',base,spec.pipe.max_head_m,station.identifier)
        check('riser_static_head_m',base,spec.riser.max_head_m,station.identifier)
        check('nozzle_static_head_m',nozzle,spec.nozzle.max_static_head_m,station.identifier)
        check('static_head_below_zero',nozzle,0,station.identifier,minimum=True)
    prediction=_solve(spec)
    if prediction is None:
        constraints.append(dict(code='no_solution_within_nozzle_and_inlet_envelopes',identifier=None,
                                actual=None,required_min=None,allowed_max=None))
    else:
        nodes=prediction['nodes']
        flows=[row['nozzle_flow_lph'] for row in nodes]
        variation=(max(flows)-min(flows))/max(flows)
        previous_head,previous_z=prediction['inlet_pressure_head_m'],0
        check('pipe_running_head_m',previous_head,spec.pipe.max_head_m,'inlet')
        for row in nodes:
            row['head_balance_residual_m']=previous_head+previous_z-row['base_pressure_head_m']-row['elevation_m']-row['upstream_segment_loss']['total_head_m']
            check('pipe_running_head_m',row['base_pressure_head_m'],spec.pipe.max_head_m,row['identifier'])
            check('riser_running_head_m',row['base_pressure_head_m'],spec.riser.max_head_m,row['identifier'])
            check('lateral_velocity_m_s',row['upstream_segment_loss']['velocity_m_s'],spec.limits.max_velocity_m_s,row['identifier'])
            check('riser_velocity_m_s',row['riser_loss']['velocity_m_s'],spec.limits.max_velocity_m_s,row['identifier'])
            previous_head,previous_z=row['base_pressure_head_m'],row['elevation_m']
        check('inlet_flow_lph',prediction['inlet_flow_lph'],spec.inlet.max_flow_lph)
        check('flow_variation_fraction',variation,spec.limits.max_flow_variation_fraction)
        prediction.update(min_nozzle_pressure_head_m=min(row['nozzle_pressure_head_m'] for row in nodes),
            max_nozzle_pressure_head_m=max(row['nozzle_pressure_head_m'] for row in nodes),
            min_nozzle_flow_lph=min(flows),max_nozzle_flow_lph=max(flows),mean_nozzle_flow_lph=fsum(flows)/len(flows),
            flow_variation_fraction=variation,mass_balance_residual_lph=prediction['inlet_flow_lph']-fsum(flows))
    return dict(model_version=MODEL_VERSION,evidence_status='experimental',event_kind='scenario',inputs=asdict(spec),
        prediction=prediction,static_profile=static,constraints=constraints,binding_constraints=sorted({c['code'] for c in constraints}),
        feasible=None if prediction is None else not constraints,missing_inputs=[],schedule=None,
        feasibility_scope='One nominal steady sprinkler-lateral operating point within supplied curves and limits only',
        sources=sorted({EPA_NETWORK,EPA_ALGORITHMS,spec.pipe.source,spec.riser.source,spec.nozzle.source,spec.inlet.source,
                        spec.limits.source,spec.conditions.source,*(s.source for s in spec.stations)}),
        assumptions=[
            'One constant-diameter full lateral, closed immediately after the last station; one identical unregulated atmospheric nozzle per station.',
            'Station distances are cumulative pipe lengths and elevations are relative to the lateral inlet, linear between stations without omitted crests or sags.',
            'Each constant-diameter riser climbs monotonically from station base to nozzle; supplied path length and separate branch fitting losses apply at that nozzle flow.',
            'Nozzle flow = reference_flow_lph * sqrt(nozzle_pressure_head_m/reference_head_m), evaluated only inside the supplied pressure band.',
            'Darcy-Weisbach nodal head losses; through-fitting K is located at the downstream station and uses incoming lateral velocity, while riser K uses branch velocity. No outlet kinetic-head recovery or added nozzle exit K.',
            'The fixed head or bounded nonincreasing source curve is pressure at the lateral inlet after upstream equipment, not raw pump head gain; all active outlets are included.',
            'Static ratings assume a connected water-filled system held at the supplied maximum static inlet head with outlets shut; drainage and isolated sections are not modeled.',
        ],limitations=[
            'Experimental hydraulic scenario without independent field validation. No application rate, wetted pattern, wind drift, catch-can uniformity, crop demand or irrigation schedule.',
            'No pressure regulators, moving systems, leakage, nozzle wear/variability, surge, air entry, upstream network interaction, branched manifolds or loops.',
            'No automatic pipe/pump selection, pump efficiency or suction/NPSH assessment. Source curves are not extrapolated.',
            'A withheld result means no supported numerical operating point was found, not proof of general physical infeasibility.',
            'Survey, nozzle calibration, operating envelopes, equipment ratings and acceptance limits are user-supplied evidence, not independently verified facts.',
        ])
