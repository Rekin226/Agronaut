"""Experimental design checks for a flat, sequential-zone PC drip installation.

The source feeds a trunk along the field edge. Each contiguous zone has its own
manifold; laterals are identical. The first branch/emitter is half a spacing from
the inlet. Remaining outlets are one spacing apart. All outlet flows are bounded
by the emitter specification, conditional on its compensation band being met.
"""

from dataclasses import asdict, dataclass

from .hydraulics import EPA_ALGORITHMS, EPA_NETWORK, GRAVITY_M_S2, Pipe, pipe_loss
from .hydraulics import distributed_outlet_losses as _outlet_losses
from .validation import choice, number, text

MODEL_VERSION = "flat-pc-drip-0.1.0-epanet2.2"
FAO_DRIP = "https://www.fao.org/4/s8684e/s8684e07.htm"


def _count(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10000:
        raise ValueError(f"{name} must be an integer from 1 to 10000")


@dataclass(frozen=True)
class Layout:
    row_count: int
    emitters_per_row: int
    rows_per_zone: int
    row_spacing_m: float
    emitter_spacing_m: float
    feed_length_m: float
    terrain: str
    source: str

    def __post_init__(self):
        for name in ("row_count", "emitters_per_row", "rows_per_zone"):
            _count(name, getattr(self, name))
        if self.rows_per_zone > self.row_count:
            raise ValueError("rows_per_zone exceeds row_count")
        for name in ("row_spacing_m", "emitter_spacing_m"):
            number(name, getattr(self, name), 0.01, 20)
        number("feed_length_m", self.feed_length_m, 0, 10000)
        choice("terrain", self.terrain, ("flat",))
        text("layout source", self.source)


@dataclass(frozen=True)
class Emitter:
    nominal_flow_lph: float
    flow_tolerance_fraction: float
    min_head_m: float
    max_head_m: float
    max_filter_microns: float
    insertion_k: float
    pressure_compensating: bool
    source: str

    def __post_init__(self):
        number("nominal_flow_lph", self.nominal_flow_lph, 0.01, 100)
        number("flow_tolerance_fraction", self.flow_tolerance_fraction, 0, 0.99)
        number("min_head_m", self.min_head_m, high=1000, positive=True)
        number("max_head_m", self.max_head_m, self.min_head_m, 1000)
        number("max_filter_microns", self.max_filter_microns, 1, 10000)
        number("insertion_k", self.insertion_k, 0, 10000)
        if self.pressure_compensating is not True:
            raise ValueError("only pressure_compensating emitters are supported")
        text("emitter source", self.source)


@dataclass(frozen=True)
class Supply:
    min_running_head_m: float
    max_running_head_m: float
    max_static_head_m: float
    max_flow_lph: float
    daily_available_l: float
    available_hours: float
    source: str

    def __post_init__(self):
        number("min_running_head_m", self.min_running_head_m, high=1000, positive=True)
        number("max_running_head_m", self.max_running_head_m, self.min_running_head_m, 1000)
        number("max_static_head_m", self.max_static_head_m, self.max_running_head_m, 1000)
        number("max_flow_lph", self.max_flow_lph, high=1e9, positive=True)
        number("daily_available_l", self.daily_available_l, 0, 1e12)
        number("available_hours", self.available_hours, 0, 24)
        text("supply source", self.source)


@dataclass(frozen=True)
class Headworks:
    min_filter_loss_m: float
    max_filter_loss_m: float
    filter_microns: float
    filter_max_flow_lph: float
    max_head_m: float
    main_fittings_k: float
    manifold_through_k: float
    lateral_takeoff_k: float
    source: str

    def __post_init__(self):
        number("min_filter_loss_m", self.min_filter_loss_m, 0, 1000)
        number("max_filter_loss_m", self.max_filter_loss_m, self.min_filter_loss_m, 1000)
        number("filter_microns", self.filter_microns, 1, 10000)
        number("filter_max_flow_lph", self.filter_max_flow_lph, high=1e9, positive=True)
        number("max_head_m", self.max_head_m, high=1000, positive=True)
        for name in ("main_fittings_k", "manifold_through_k", "lateral_takeoff_k"):
            number(name, getattr(self, name), 0, 10000)
        text("headworks source", self.source)


@dataclass(frozen=True)
class Application:
    gross_depth_mm: float
    wetted_fraction: float
    max_local_depth_mm: float
    max_local_rate_mm_h: float
    maintenance_water_l: float
    maintenance_hours: float
    source: str

    def __post_init__(self):
        number("gross_depth_mm", self.gross_depth_mm, 0, 1000)
        number("wetted_fraction", self.wetted_fraction, 1e-6, 1)
        number("max_local_depth_mm", self.max_local_depth_mm, high=10000, positive=True)
        number("max_local_rate_mm_h", self.max_local_rate_mm_h, high=10000, positive=True)
        number("maintenance_water_l", self.maintenance_water_l, 0, 1e12)
        number("maintenance_hours", self.maintenance_hours, 0, 24)
        text("application source", self.source)


@dataclass(frozen=True)
class OperatingLimits:
    viscosity_m2_s: float
    max_velocity_m_s: float
    source: str

    def __post_init__(self):
        number("viscosity_m2_s", self.viscosity_m2_s, 1e-8, 1e-3)
        number("max_velocity_m_s", self.max_velocity_m_s, high=100, positive=True)
        text("operating limits source", self.source)


@dataclass(frozen=True)
class DripDesign:
    layout: Layout
    emitter: Emitter
    main: Pipe
    manifold: Pipe
    lateral: Pipe
    supply: Supply
    headworks: Headworks
    application: Application
    limits: OperatingLimits

    def __post_init__(self):
        for name, cls in (("layout", Layout), ("emitter", Emitter), ("main", Pipe),
                          ("manifold", Pipe), ("lateral", Pipe), ("supply", Supply),
                          ("headworks", Headworks), ("application", Application),
                          ("limits", OperatingLimits)):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be a validated {cls.__name__}")


def design_drip(spec: DripDesign) -> dict:
    """Check supplied design; no implicit crop schedule, pipe sizing or pump curve."""
    layout, emitter, supply = spec.layout, spec.emitter, spec.supply
    works, app, limits = spec.headworks, spec.application, spec.limits
    n, per_row = layout.row_count, layout.emitters_per_row
    row_length = per_row * layout.emitter_spacing_m
    row_area = row_length * layout.row_spacing_m
    area = n * row_area
    q_low = emitter.nominal_flow_lph * (1 - emitter.flow_tolerance_fraction)
    q_high = emitter.nominal_flow_lph * (1 + emitter.flow_tolerance_fraction)
    violations = []

    def check(code, actual, limit, *, minimum=False, zone=None):
        number(code, actual)  # no NaN/inf is allowed to turn a constraint into a pass
        if (actual < limit if minimum else actual > limit):
            violations.append(dict(code=code, zone=zone, actual=actual,
                                   required_min=limit if minimum else None,
                                   allowed_max=None if minimum else limit))

    check("filter_microns", works.filter_microns, emitter.max_filter_microns)
    for name in ("main", "manifold", "lateral", "headworks", "emitter"):
        check(f"{name}_static_head_m", supply.max_static_head_m, getattr(spec, name).max_head_m)

    lateral_bounds = []
    for q in (q_low, q_high):
        first, last, velocity = _outlet_losses(
            spec.lateral, per_row, layout.emitter_spacing_m, q,
            limits.viscosity_m2_s, emitter.insertion_k)
        takeoff = pipe_loss(spec.lateral, flow_lph=per_row*q, length_m=0,
                            viscosity_m2_s=limits.viscosity_m2_s,
                            minor_k=works.lateral_takeoff_k)["total_head_m"]
        lateral_bounds.append((first + takeoff, last + takeoff, velocity))

    zones, header_cache = [], {}
    total_hours = total_upper_water = 0.0
    for start in range(0, n, layout.rows_per_zone):
        count = min(layout.rows_per_zone, n - start)
        zone_id = len(zones) + 1
        zone_area = count * row_area
        flows = (count * per_row * q_low, count * per_row * q_high)
        bounds = []
        for index, q in enumerate((q_low, q_high)):
            key = (count, index)
            if key not in header_cache:
                header_cache[key] = _outlet_losses(
                    spec.manifold, count, layout.row_spacing_m, per_row*q,
                    limits.viscosity_m2_s, works.manifold_through_k)
            first, last, header_velocity = header_cache[key]
            main = pipe_loss(spec.main, flow_lph=flows[index],
                             length_m=layout.feed_length_m + start * layout.row_spacing_m,
                             viscosity_m2_s=limits.viscosity_m2_s,
                             minor_k=works.main_fittings_k)
            lat_first, lat_last, lat_velocity = lateral_bounds[index]
            bounds.append(dict(first_loss_m=main["total_head_m"] + first + lat_first,
                               last_loss_m=main["total_head_m"] + last + lat_last,
                               main_loss_m=main["total_head_m"], manifold_loss_m=last,
                               lateral_loss_m=lat_last,
                               peak_velocity_m_s=max(main["velocity_m_s"], header_velocity,
                                                     lat_velocity)))
        low, high = bounds
        minimum_head = supply.min_running_head_m - works.max_filter_loss_m - high["last_loss_m"]
        maximum_head = supply.max_running_head_m - works.min_filter_loss_m - low["first_loss_m"]
        check("emitter_min_head_m", minimum_head, emitter.min_head_m, minimum=True, zone=zone_id)
        check("emitter_max_head_m", maximum_head, emitter.max_head_m, zone=zone_id)
        check("source_flow_lph", flows[1], supply.max_flow_lph, zone=zone_id)
        check("filter_flow_lph", flows[1], works.filter_max_flow_lph, zone=zone_id)
        check("velocity_m_s", high["peak_velocity_m_s"], limits.max_velocity_m_s, zone=zone_id)
        # Runtime delivers the requested volume even at the lower flow bound.
        # The higher flow bound therefore controls maximum withdrawal/local depth.
        target_l = app.gross_depth_mm * zone_area
        hours = target_l / flows[0]
        upper_l = hours * flows[1]
        local_depth = upper_l / (zone_area * app.wetted_fraction)
        local_rate = flows[1] / (zone_area * app.wetted_fraction)
        check("local_depth_mm", local_depth, app.max_local_depth_mm, zone=zone_id)
        check("local_rate_mm_h", local_rate, app.max_local_rate_mm_h, zone=zone_id)
        total_hours += hours
        total_upper_water += upper_l
        zones.append(dict(zone=zone_id, first_row=start+1, row_count=count, area_m2=zone_area,
                          emitter_count=count*per_row, flow_min_lph=flows[0], flow_max_lph=flows[1],
                          emitter_min_head_m=minimum_head, emitter_max_head_m=maximum_head,
                          required_source_min_head_m=emitter.min_head_m + works.max_filter_loss_m
                          + high["last_loss_m"],
                          allowed_source_max_head_m=emitter.max_head_m + works.min_filter_loss_m
                          + low["first_loss_m"],
                          high_flow_path_losses=high, candidate_runtime_hours=hours,
                          requested_water_l=target_l, candidate_upper_water_l=upper_l,
                          candidate_local_depth_mm=local_depth, local_rate_max_mm_h=local_rate))
    total_hours += app.maintenance_hours
    total_upper_water += app.maintenance_water_l
    check("daily_source_water_l", total_upper_water, supply.daily_available_l)
    check("daily_runtime_hours", total_hours, supply.available_hours)
    feasible = not violations
    return dict(
        model_version=MODEL_VERSION, evidence_status="experimental", inputs=asdict(spec),
        feasible=feasible, feasibility_scope="Supplied steady-flow, resource and localized-application limits only",
        constraints=violations, binding_constraints=sorted({v["code"] for v in violations}),
        area_m2=area, row_length_m=row_length, field_width_m=n*layout.row_spacing_m,
        requested_water_l=app.gross_depth_mm*area,
        candidate_daily_upper_water_l=total_upper_water, candidate_daily_hours=total_hours,
        zones=zones,
        schedule=([dict(zone=z["zone"], runtime_hours=z["candidate_runtime_hours"]) for z in zones]
                  if feasible else None),
        materials=dict(main_pipe_m=layout.feed_length_m+n*layout.row_spacing_m,
                       manifold_pipe_m=n*layout.row_spacing_m, lateral_pipe_m=n*row_length,
                       emitters=n*per_row, zone_valves=len(zones), lateral_takeoffs=n,
                       lateral_flush_ends=n, manifold_flush_ends=len(zones), trunk_flush_ends=1,
                       filter_assemblies=1),
        sources=sorted({EPA_ALGORITHMS, EPA_NETWORK, FAO_DRIP,
                        *(value.source for value in vars(spec).values())}),
        coefficients=dict(gravity_m_s2=GRAVITY_M_S2,
                          friction_method="EPANET 2.2 section 12.1: laminar/Dunlop/Swamee-Jain"),
        assumptions=[
            "Flat rectangular field; identical surface PC emitters and laterals; one contiguous zone at a time.",
            "Source running-head bounds apply across the entire specified flow range, upstream of the filter.",
            "First outlet at half spacing; all subsequent outlets at full spacing; closed pipe tails.",
            "Steady full-pipe junction-head model; no kinetic-head recovery at branch junctions.",
            "Emitter tolerance bounds every emitter across its rated pressure band, not just a sample mean.",
            "Fitting K values include every active-path fitting; emitter K uses the upstream lateral velocity.",
            "All pipe/headworks ratings and the emitter upper band conservatively checked against source static head.",
            "Wetted fraction and local depth/rate limits supplied for this soil, geometry and irrigation event.",
            "Gross depth is an independently determined field-average application target; it is not reduced by wetted fraction.",
            "Daily resource totals include supplied maintenance water and time, outside the zone runtimes.",
        ],
        limitations=[
            "No independent field validation; passing constraints does not establish installation readiness.",
            "Candidate flows, pressures and runtimes are conditional on the PC model; infeasible designs have no schedule.",
            "No slope, non-PC emitter curve, pump curve, surge, leakage, clogging, fertigation or flushing hydraulics.",
            "Localized depth/rate are area averages, not wetting-front, infiltration, crop stress or drainage predictions.",
            "Crop and soil-state scheduling, salinity and actual distribution uniformity require separate evaluation.",
            "Material quantities exclude joints, spares, installation waste, pumps, controls and site-specific accessories.",
            "Supplied provenance is recorded, not independently verified.",
        ], missing_inputs=[])
