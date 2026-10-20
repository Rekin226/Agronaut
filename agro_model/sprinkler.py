"""Conservative design envelope for flat, solid-set, full-circle sprinkler grids.

Nozzle q = q_ref * sqrt(h/h_ref), with a supplied per-nozzle tolerance. Flow
bounds over the entire rated pressure band bound every pipe loss. A design passes
only if the resulting nozzle heads remain within that same band. This encloses
the passive network's operating point; it does not solve individual nozzle heads.
"""

from dataclasses import asdict, dataclass
from math import hypot, sqrt

from .drip import Headworks, OperatingLimits, Supply
from .hydraulics import (
    EPA_ALGORITHMS,
    EPA_NETWORK,
    GRAVITY_M_S2,
    Pipe,
    distributed_outlet_losses,
    pipe_loss,
)
from .validation import choice, number, text

MODEL_VERSION = "flat-sprinkler-envelope-0.1.0-epanet2.2"
FAO_SPRINKLER = "https://www.fao.org/4/a1336e/a1336e08.pdf"


@dataclass(frozen=True)
class Layout:
    lateral_count: int
    sprinklers_per_lateral: int
    laterals_per_zone: int
    lateral_spacing_m: float
    sprinkler_spacing_m: float
    feed_length_m: float
    riser_height_m: float
    terrain: str
    source: str

    def __post_init__(self):
        for key in ("lateral_count", "sprinklers_per_lateral", "laterals_per_zone"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValueError(f"{key} must be an integer from 1 to 1000")
        if self.laterals_per_zone > self.lateral_count:
            raise ValueError("laterals_per_zone exceeds lateral_count")
        if self.lateral_count * self.sprinklers_per_lateral > 10000:
            raise ValueError("layout exceeds the 10000-sprinkler software envelope")
        for key in ("lateral_spacing_m", "sprinkler_spacing_m"):
            number(key, getattr(self, key), 0.1, 100)
        number("feed_length_m", self.feed_length_m, 0, 10000)
        number("riser_height_m", self.riser_height_m, 0, 20)
        choice("terrain", self.terrain, ("flat",))
        text("layout source", self.source)


@dataclass(frozen=True)
class Sprinkler:
    reference_flow_lph: float
    reference_head_m: float
    min_head_m: float
    max_head_m: float
    max_static_head_m: float
    flow_tolerance_fraction: float
    min_wetted_diameter_m: float
    max_spacing_fraction: float
    max_wind_m_s: float
    max_filter_microns: float
    lateral_through_k: float
    riser_fittings_k: float
    pattern: str
    source: str

    def __post_init__(self):
        number("reference_flow_lph", self.reference_flow_lph, 1, 100000)
        number("min_head_m", self.min_head_m, 0.01, 1000)
        number("max_head_m", self.max_head_m, self.min_head_m, 1000)
        number("reference_head_m", self.reference_head_m, self.min_head_m, self.max_head_m)
        number("max_static_head_m", self.max_static_head_m, self.max_head_m, 10000)
        number("flow_tolerance_fraction", self.flow_tolerance_fraction, 0, 0.99)
        number("min_wetted_diameter_m", self.min_wetted_diameter_m, 0.1, 200)
        number("max_spacing_fraction", self.max_spacing_fraction, high=1, positive=True)
        number("max_wind_m_s", self.max_wind_m_s, 0, 30)
        number("max_filter_microns", self.max_filter_microns, 1, 10000)
        for key in ("lateral_through_k", "riser_fittings_k"):
            number(key, getattr(self, key), 0, 10000)
        choice("pattern", self.pattern, ("full_circle",))
        text("sprinkler source", self.source)

    def flow_bounds(self):
        return (self.reference_flow_lph * sqrt(self.min_head_m / self.reference_head_m)
                * (1 - self.flow_tolerance_fraction),
                self.reference_flow_lph * sqrt(self.max_head_m / self.reference_head_m)
                * (1 + self.flow_tolerance_fraction))


@dataclass(frozen=True)
class Application:
    net_depth_mm: float
    efficiency: float
    wind_m_s: float
    peak_to_average_factor: float
    max_local_rate_mm_h: float
    max_local_depth_mm: float
    maintenance_water_l: float
    maintenance_hours: float
    source: str

    def __post_init__(self):
        number("net_depth_mm", self.net_depth_mm, 0, 1000)
        number("efficiency", self.efficiency, high=1, positive=True)
        number("wind_m_s", self.wind_m_s, 0, 100)
        number("peak_to_average_factor", self.peak_to_average_factor, 1, 100)
        number("max_local_rate_mm_h", self.max_local_rate_mm_h, high=10000, positive=True)
        number("max_local_depth_mm", self.max_local_depth_mm, high=10000, positive=True)
        number("maintenance_water_l", self.maintenance_water_l, 0, 1e12)
        number("maintenance_hours", self.maintenance_hours, 0, 24)
        text("application source", self.source)


@dataclass(frozen=True)
class SprinklerDesign:
    layout: Layout
    sprinkler: Sprinkler
    main: Pipe
    manifold: Pipe
    lateral: Pipe
    riser: Pipe
    supply: Supply
    headworks: Headworks
    application: Application
    limits: OperatingLimits

    def __post_init__(self):
        for name, cls in (("layout", Layout), ("sprinkler", Sprinkler), ("main", Pipe),
                          ("manifold", Pipe), ("lateral", Pipe), ("riser", Pipe),
                          ("supply", Supply), ("headworks", Headworks),
                          ("application", Application), ("limits", OperatingLimits)):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be a validated {cls.__name__}")


def design_sprinkler(spec: SprinklerDesign) -> dict:
    layout, nozzle, app = spec.layout, spec.sprinkler, spec.application
    supply, works, limits = spec.supply, spec.headworks, spec.limits
    n, per_lateral = layout.lateral_count, layout.sprinklers_per_lateral
    cell_area = layout.lateral_spacing_m * layout.sprinkler_spacing_m
    area = n * per_lateral * cell_area
    q_low, q_high = nozzle.flow_bounds()
    violations = []

    def check(code, actual, limit, *, minimum=False, zone=None):
        number(code, actual)
        if (actual < limit if minimum else actual > limit):
            violations.append(dict(code=code, zone=zone, actual=actual,
                                   required_min=limit if minimum else None,
                                   allowed_max=None if minimum else limit))

    check("wind_m_s", app.wind_m_s, nozzle.max_wind_m_s)
    spacing_limit = nozzle.min_wetted_diameter_m * nozzle.max_spacing_fraction
    check("lateral_spacing_m", layout.lateral_spacing_m, spacing_limit)
    check("sprinkler_spacing_m", layout.sprinkler_spacing_m, spacing_limit)
    # At a grid-cell corner the nearest nozzle is half a cell diagonal away.
    # This is only geometric coverage of the supplied minimum circular footprint.
    check("cell_diagonal_m", hypot(layout.lateral_spacing_m, layout.sprinkler_spacing_m),
          nozzle.min_wetted_diameter_m)
    check("filter_microns", works.filter_microns, nozzle.max_filter_microns)
    for name in ("main", "manifold", "lateral", "riser", "headworks"):
        check(f"{name}_static_head_m", supply.max_static_head_m, getattr(spec, name).max_head_m)
    check("sprinkler_static_head_m", max(0, supply.max_static_head_m - layout.riser_height_m),
          nozzle.max_static_head_m)

    # Cache identical lateral and riser calculations for both flow endpoints.
    paths = []
    for q in (q_low, q_high):
        first, last, velocity = distributed_outlet_losses(spec.lateral, per_lateral,
            layout.sprinkler_spacing_m, q, limits.viscosity_m2_s, nozzle.lateral_through_k)
        takeoff = pipe_loss(spec.lateral, flow_lph=per_lateral*q, length_m=0,
                           viscosity_m2_s=limits.viscosity_m2_s, minor_k=works.lateral_takeoff_k)
        riser = pipe_loss(spec.riser, flow_lph=q, length_m=layout.riser_height_m,
                         viscosity_m2_s=limits.viscosity_m2_s, minor_k=nozzle.riser_fittings_k)
        extra = takeoff["total_head_m"] + riser["total_head_m"]
        paths.append((first + extra, last + extra, max(velocity, riser["velocity_m_s"])))

    zones, header_cache = [], {}
    total_hours = app.maintenance_hours
    total_water = app.maintenance_water_l
    for start in range(0, n, layout.laterals_per_zone):
        count = min(layout.laterals_per_zone, n-start)
        zone_id = len(zones)+1
        bounds = []
        for index, q in enumerate((q_low, q_high)):
            key = (count, index)
            if key not in header_cache:
                header_cache[key] = distributed_outlet_losses(spec.manifold, count,
                    layout.lateral_spacing_m, per_lateral*q, limits.viscosity_m2_s, works.manifold_through_k)
            first, last, velocity = header_cache[key]
            main = pipe_loss(spec.main, flow_lph=count*per_lateral*q,
                            length_m=layout.feed_length_m+start*layout.lateral_spacing_m,
                            viscosity_m2_s=limits.viscosity_m2_s, minor_k=works.main_fittings_k)
            lat_first, lat_last, lat_velocity = paths[index]
            bounds.append(dict(first_loss_m=main["total_head_m"]+first+lat_first,
                               last_loss_m=main["total_head_m"]+last+lat_last,
                               peak_velocity_m_s=max(main["velocity_m_s"], velocity, lat_velocity)))
        low, high = bounds
        min_head = supply.min_running_head_m - works.max_filter_loss_m - high["last_loss_m"] - layout.riser_height_m
        max_head = supply.max_running_head_m - works.min_filter_loss_m - low["first_loss_m"] - layout.riser_height_m
        check("sprinkler_min_head_m", min_head, nozzle.min_head_m, minimum=True, zone=zone_id)
        check("sprinkler_max_head_m", max_head, nozzle.max_head_m, zone=zone_id)
        flows = (count*per_lateral*q_low, count*per_lateral*q_high)
        check("source_flow_lph", flows[1], supply.max_flow_lph, zone=zone_id)
        check("filter_flow_lph", flows[1], works.filter_max_flow_lph, zone=zone_id)
        check("velocity_m_s", high["peak_velocity_m_s"], limits.max_velocity_m_s, zone=zone_id)
        zone_area = count * per_lateral * cell_area
        gross_l = app.net_depth_mm * zone_area / app.efficiency
        hours = gross_l / flows[0]
        upper_water = hours * flows[1]
        peak_rate = q_high / cell_area * app.peak_to_average_factor
        peak_depth = peak_rate * hours
        check("local_rate_mm_h", peak_rate, app.max_local_rate_mm_h, zone=zone_id)
        check("local_depth_mm", peak_depth, app.max_local_depth_mm, zone=zone_id)
        total_hours += hours
        total_water += upper_water
        zones.append(dict(zone=zone_id, first_lateral=start+1, lateral_count=count, area_m2=zone_area,
                          sprinkler_count=count*per_lateral, flow_min_lph=flows[0], flow_max_lph=flows[1],
                          sprinkler_min_head_m=min_head, sprinkler_max_head_m=max_head,
                          required_source_min_head_m=nozzle.min_head_m+layout.riser_height_m
                          +works.max_filter_loss_m+high["last_loss_m"],
                          allowed_source_max_head_m=nozzle.max_head_m+layout.riser_height_m
                          +works.min_filter_loss_m+low["first_loss_m"],
                          high_flow_path_losses=high, candidate_runtime_hours=hours,
                          requested_gross_water_l=gross_l, candidate_upper_water_l=upper_water,
                          average_rate_min_mm_h=q_low/cell_area, average_rate_max_mm_h=q_high/cell_area,
                          local_rate_upper_mm_h=peak_rate, candidate_local_depth_upper_mm=peak_depth))
    check("daily_source_water_l", total_water, supply.daily_available_l)
    check("daily_runtime_hours", total_hours, supply.available_hours)
    feasible = not violations
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", inputs=asdict(spec),
        feasible=feasible, feasibility_scope="Conservative pressure-band enclosure and supplied pattern/application bounds only",
        constraints=violations, binding_constraints=sorted({v["code"] for v in violations}),
        area_m2=area, requested_net_water_l=app.net_depth_mm*area,
        requested_gross_water_l=app.net_depth_mm*area/app.efficiency,
        candidate_daily_upper_water_l=total_water, candidate_daily_hours=total_hours, zones=zones,
        schedule=([dict(zone=z["zone"], runtime_hours=z["candidate_runtime_hours"]) for z in zones] if feasible else None),
        materials=dict(main_pipe_m=layout.feed_length_m+n*layout.lateral_spacing_m,
                       manifold_pipe_m=n*layout.lateral_spacing_m,
                       lateral_pipe_m=n*per_lateral*layout.sprinkler_spacing_m,
                       riser_pipe_m=n*per_lateral*layout.riser_height_m, sprinklers=n*per_lateral,
                       riser_assemblies=n*per_lateral, zone_valves=len(zones), lateral_takeoffs=n,
                       lateral_flush_ends=n, manifold_flush_ends=len(zones), trunk_flush_ends=1, filter_assemblies=1),
        sources=sorted({EPA_NETWORK, EPA_ALGORITHMS, FAO_SPRINKLER, *(v.source for v in vars(spec).values())}),
        coefficients=dict(gravity_m_s2=GRAVITY_M_S2, nozzle_pressure_exponent=0.5,
                          friction_method="EPANET 2.2 section 12.1: laminar/Dunlop/Swamee-Jain"),
        assumptions=[
            "Flat rectangular solid-set grid, identical full-circle nozzles and risers; one contiguous zone at a time.",
            "First branch and nozzle at half spacing; subsequent outlets at full spacing; closed pipe tails.",
            "Nozzle square-root pressure-flow relation and tolerance apply to every nozzle throughout its rated band.",
            "Source head bounds hold at all candidate flows upstream of the filter; no pump curve is inferred.",
            "Spacing and minimum wetted diameter are supplied for the entire pressure band and stated wind envelope.",
            "Application efficiency is a supplied lower bound on useful field-average net water per emitted volume, including edge overspray and wind losses.",
            "Peak-to-average factor bounds the complete overlapping grid pattern at the stated spacing, wind and pressures, including cumulative adjacent-zone contributions.",
            "Identical zone runtime follows from equal cell area and nozzle flow bounds, including a partial last zone.",
            "Local rate and cumulative depth bounds use the full grid's peak factor; they are not distribution predictions.",
            "Steady junction-head model without kinetic-head recovery; fitting K values use the corresponding upstream pipe velocity.",
            "Daily resources include supplied maintenance allowances outside zone runtimes; target net depth is independently supplied.",
        ], limitations=[
            "Experimental; no independent field validation or installation-readiness claim.",
            "Flow/head intervals are conservative enclosures, not solved nozzle operating points; failure may require a detailed hydraulic solver.",
            "No slope, partial-circle heads, moving laterals, pivots, pump curves, regulators, surge, leakage or clogging.",
            "Pattern, wind, efficiency and soil intake inputs need site/equipment evidence; spacing checks alone do not establish uniformity or prevent runoff.",
            "Boundary coverage, off-field spray, drop damage, salinity, crop scheduling and measured distribution uniformity need separate evaluation.",
            "Net depth is a field-average target, not guaranteed delivery to every plant; supplied sources are not independently verified.",
            "Materials exclude joints, spares, installation waste, pumps, controls and site-specific accessories.",
        ], missing_inputs=[])
