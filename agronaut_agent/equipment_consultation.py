"""Guided inputs for the existing drip and sprinkler engines; no design defaults.

Question bounds are input feedback, not recommended equipment specifications. The
native record validators and engines remain authoritative at preview and save.
"""

from dataclasses import dataclass, fields

from agro_model.validation import number, text

from . import equipment_targets
from .irrigation_io import DRIP_RECORDS, SPRINKLER_RECORDS

TASKS = ("drip_design", "sprinkler_design")


@dataclass(frozen=True)
class Question:
    prompt: str
    low: float | tuple | None = 0
    high: float | tuple | None = None
    positive: bool = False
    integer: bool = False
    percent: bool = False
    choices: tuple = ()


PIPE = dict(
    internal_diameter_mm=Question("What is the pipe's INTERNAL diameter in mm? Nominal trade sizes are not internal diameters.", 1, 2000),
    roughness_mm=Question("What absolute pipe roughness applies, in mm? Supply a value for this material and condition.", 0, ("internal_diameter_mm", .05)),
    max_head_m=Question("What maximum static pressure rating applies, in metres of water head?", None, 10000, positive=True),
)
SUPPLY = dict(
    min_running_head_m=Question("What minimum running head can the source maintain upstream of the filter, in metres of water?", None, 1000, positive=True),
    max_running_head_m=Question("What maximum running head applies over the operating flow range, in metres of water?", ("min_running_head_m", 1), 1000),
    max_static_head_m=Question("What maximum static head applies when flow is stopped, in metres of water?", ("max_running_head_m", 1), 1000),
    max_flow_lph=Question("What maximum source flow is available in L/hour? The running-head bounds must apply throughout 0 to this flow.", None, 1e9, positive=True),
    daily_available_l=Question("How many litres are available for this irrigation day? Explicit zero is allowed; unknown is not zero.", 0, 1e12),
    available_hours=Question("How many hours are available for irrigation and maintenance this day?", 0, 24),
)
HEADWORKS = dict(
    min_filter_loss_m=Question("What minimum clean-filter head loss applies, in metres of water?", 0, 1000),
    max_filter_loss_m=Question("What maximum dirty-filter head loss applies over the operating flows, in metres of water?", ("min_filter_loss_m", 1), 1000),
    filter_microns=Question("What filtration opening is installed, in micrometres (microns)?", 1, 10000),
    filter_max_flow_lph=Question("What maximum rated filter flow applies, in L/hour?", None, 1e9, positive=True),
    max_head_m=Question("What maximum static head can the filter/headworks withstand, in metres of water?", None, 1000, positive=True),
    main_fittings_k=Question("What is the combined dimensionless K loss coefficient for the main-pipe fittings?", 0, 10000),
    manifold_through_k=Question("What dimensionless K loss coefficient applies at EACH through-connection on the manifold?", 0, 10000),
    lateral_takeoff_k=Question("What dimensionless K loss coefficient applies at EACH lateral takeoff?", 0, 10000),
)
LIMITS = dict(
    viscosity_m2_s=Question("What kinematic viscosity applies to the water, in m²/s? Scientific notation is accepted; supply the value for its temperature.", 1e-8, 1e-3),
    max_velocity_m_s=Question("What maximum pipe velocity does the designer permit, in metres/second?", None, 100, positive=True),
)
MAINTENANCE = dict(
    maintenance_water_l=Question("How many additional litres are budgeted for flushing and other maintenance this day? Enter zero only if explicitly assumed.", 0, 1e12),
    maintenance_hours=Question("How many additional hours are budgeted for maintenance this day? Enter zero only if explicitly assumed.", 0, 24),
)
LOCAL_LIMITS = dict(
    max_local_depth_mm=Question("What maximum LOCAL applied depth is permitted on the wetted soil, in mm? Supply a soil/site-specific limit.", None, 10000, positive=True),
    max_local_rate_mm_h=Question("What maximum LOCAL application rate can the soil accept, in mm/hour? Supply a soil/site-specific limit.", None, 10000, positive=True),
)
DRIP = dict(
    layout=dict(
        terrain=Question("This experimental check supports flat fields with identical laterals and sequential zones. Enter flat only if this layout applies.", choices=("flat",)),
        row_count=Question("How many rows/laterals are installed? Enter a whole number.", 1, 10000, integer=True),
        emitters_per_row=Question("How many emitters are on EACH identical lateral? Enter a whole number.", 1, 10000, integer=True),
        rows_per_zone=Question("How many contiguous rows run together in one zone? Zones run sequentially. Enter a whole number.", 1, ("row_count", 1), integer=True),
        row_spacing_m=Question("What is the spacing between rows/laterals, in metres?", .01, 20),
        emitter_spacing_m=Question("What is the spacing between emitters, in metres? The engine assumes the first outlet is half a spacing from the inlet.", .01, 20),
        feed_length_m=Question("What main-pipe length leads to the first zone, in metres? Subsequent zones extend along the field edge.", 0, 10000),
    ),
    emitter=dict(
        pressure_compensating=Question("Are these pressure-compensating emitters? Reply yes only if documented. Other emitters are outside this model.", choices=("yes",)),
        nominal_flow_lph=Question("What is each emitter's nominal flow, in L/hour?", .01, 100),
        flow_tolerance_fraction=Question("What per-emitter flow tolerance applies, as a percentage from 0 to 99?", 0, .99, percent=True),
        min_head_m=Question("What minimum emitter head is required for pressure compensation, in metres of water?", None, 1000, positive=True),
        max_head_m=Question("What maximum permitted emitter head applies, in metres of water?", ("min_head_m", 1), 1000),
        max_filter_microns=Question("What maximum filtration opening does the emitter specification permit, in micrometres?", 1, 10000),
        insertion_k=Question("What dimensionless K insertion-loss coefficient applies at EACH emitter?", 0, 10000),
    ),
    application=dict(
        gross_depth_mm=Question("What GROSS irrigation depth is planned for this day, in mm over the whole layout area? This includes application losses; crop ET alone is not a refill target.", 0, 1000),
        wetted_fraction=Question("What percentage of the layout area is wetted, greater than 0 and at most 100? Supply a measured or explicitly sourced value.", 1e-6, 1, percent=True),
        **LOCAL_LIMITS, **MAINTENANCE,
    ),
)
SPRINKLER = dict(
    layout=dict(
        terrain=Question("This experimental check supports flat, fixed solid-set, full-circle grids with sequential zones. Enter flat only if this layout applies.", choices=("flat",)),
        lateral_count=Question("How many identical laterals are installed? Enter a whole number.", 1, 1000, integer=True),
        sprinklers_per_lateral=Question("How many sprinklers are on EACH lateral? Enter a whole number. The layout supports at most 10,000 sprinklers.", 1, 1000, integer=True),
        laterals_per_zone=Question("How many contiguous laterals run together in a zone? Zones run sequentially. Enter a whole number.", 1, ("lateral_count", 1), integer=True),
        lateral_spacing_m=Question("What is the spacing between laterals, in metres?", .1, 100),
        sprinkler_spacing_m=Question("What is the spacing between sprinklers, in metres? The first sprinkler is half a spacing from the inlet.", .1, 100),
        feed_length_m=Question("What main-pipe length leads to the first zone, in metres? Subsequent zones extend along the field edge.", 0, 10000),
        riser_height_m=Question("What height above the lateral is each sprinkler nozzle, in metres?", 0, 20),
    ),
    sprinkler=dict(
        pattern=Question("What sprinkler pattern is installed? Enter full_circle; partial-circle and moving systems are outside this model.", choices=("full_circle",)),
        reference_flow_lph=Question("What nozzle flow is specified at the reference head, in L/hour?", 1, 100000),
        min_head_m=Question("What minimum nozzle operating head is permitted, in metres of water?", .01, 1000),
        max_head_m=Question("What maximum nozzle operating head is permitted, in metres of water?", ("min_head_m", 1), 1000),
        reference_head_m=Question("At what reference head was the nozzle flow specified, in metres of water? It must lie within the operating band.", ("min_head_m", 1), ("max_head_m", 1)),
        max_static_head_m=Question("What maximum static head can the sprinkler withstand, in metres of water?", ("max_head_m", 1), 10000),
        flow_tolerance_fraction=Question("What per-nozzle flow tolerance applies, as a percentage from 0 to 99?", 0, .99, percent=True),
        min_wetted_diameter_m=Question("What minimum wetted diameter is supported throughout the pressure/wind envelope, in metres?", .1, 200),
        max_spacing_fraction=Question("What maximum spacing is permitted, as a percentage of wetted diameter (greater than 0, at most 100)?", None, 1, positive=True, percent=True),
        max_wind_m_s=Question("What maximum wind speed does that pattern/spacing evidence cover, in metres/second?", 0, 30),
        max_filter_microns=Question("What maximum filtration opening does the sprinkler permit, in micrometres?", 1, 10000),
        lateral_through_k=Question("What dimensionless K loss coefficient applies at EACH sprinkler through-connection?", 0, 10000),
        riser_fittings_k=Question("What combined dimensionless K loss coefficient applies to EACH riser's fittings?", 0, 10000),
    ),
    application=dict(
        net_depth_mm=Question("What NET irrigation depth is planned for this day, in mm over the whole layout area? This is before accounting for application losses; crop ET alone is not a refill target.", 0, 1000),
        efficiency=Question("What application efficiency applies, as a percentage greater than 0 and at most 100? Include boundary and wind losses in this supplied value.", None, 1, positive=True, percent=True),
        wind_m_s=Question("What wind speed applies during the planned irrigation, in metres/second?", 0, 100),
        peak_to_average_factor=Question("What whole-grid peak-to-average application factor applies, including overlap? Enter a supplied factor of at least 1.", 1, 100),
        **LOCAL_LIMITS, **MAINTENANCE,
    ),
)
SOURCES = dict(
    layout="What survey, layout or explicit scenario supports this geometry and terrain?",
    emitter="What exact emitter specification supports these flow, pressure, filtration and insertion-loss values?",
    sprinkler="What exact nozzle specification and field/pattern evidence support these values across the supplied pressure and wind range?",
    supply="What measurements or documented source envelope support these running/static heads, flow and daily water/time availability?",
    headworks="What specifications or calculations support these filter, pressure and fitting-loss values?",
    application="What dated irrigation plan or explicit scenario supports this day's target depth, wetted area or efficiency/pattern, local soil limits and maintenance allowances? Include applicability to this layout area.",
    limits="What sources and operating conditions support the water viscosity and chosen velocity limit?",
)


def _records(task):
    return DRIP_RECORDS if task == "drip_design" else SPRINKLER_RECORDS


def questions(task):
    specific = DRIP if task == "drip_design" else SPRINKLER
    common = dict(main=PIPE, manifold=PIPE, lateral=PIPE, riser=PIPE,
                  supply=SUPPLY, headworks=HEADWORKS, limits=LIMITS)
    result = {}
    for section, cls in _records(task).items():
        prompts = specific.get(section, common.get(section))
        # A changed engine contract must never silently omit a required question.
        if set(prompts) | {"source"} != {f.name for f in fields(cls)}:
            raise ValueError(f"guided {section} questions do not match the engine contract")
        result.update({f"{section}.{key}": q for key, q in prompts.items()})
        result[f"{section}.source"] = Question(SOURCES.get(section,
            f"What exact {section} pipe specification supports its internal diameter, roughness and pressure rating?"))
    return result


def steps(draft):
    result = []
    application_started = False
    for key, q in questions(draft["data"]["task"]).items():
        if key.startswith("application.") and not application_started:
            result.extend(equipment_targets.steps(draft))
            application_started = True
        result.append((key, f"{key.split('.')[0].replace('_', ' ').title()} — {q.prompt}", "equipment"))
    return result


def answer(draft, key, raw):
    data = draft["data"]
    q = questions(data["task"])[key]
    raw = text("answer", raw).strip()
    section, name = key.split(".")
    if name == "source":
        if len(raw) > 2000 or raw.startswith("/"):
            raise ValueError("Supply a source and applicability description of at most 2000 characters.")
        value = raw
    elif q.choices:
        value = raw.lower()
        if value not in q.choices:
            raise ValueError("Supported choice: " + ", ".join(q.choices) + ". Use /back to edit or /cancel to leave this draft.")
        if name == "pressure_compensating":
            value = True
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("Supply a number in the stated units. Unknown values cannot be assumed; /back edits earlier answers.") from exc
        if q.percent:
            value /= 100
        def bound(limit):
            return data[f"{section}.{limit[0]}"] * limit[1] if isinstance(limit, tuple) else limit
        number(key, value, bound(q.low), bound(q.high), positive=q.positive)
        if q.integer:
            if not value.is_integer():
                raise ValueError("Supply a whole number.")
            value = int(value)
        if key == "layout.sprinklers_per_lateral" and value * data["layout.lateral_count"] > 10000:
            raise ValueError("The layout exceeds the 10,000-sprinkler software envelope.")
    candidate = dict(data, **{key: value})
    cls = _records(data["task"])[section]
    if all(f"{section}.{f.name}" in candidate for f in fields(cls)):
        cls(**{f.name: candidate[f"{section}.{f.name}"] for f in fields(cls)})
    data[key] = value


def inputs(draft):
    data = draft["data"]
    payload = dict(schema_version=1, **{section: {f.name: data[f"{section}.{f.name}"] for f in fields(cls)}
                   for section, cls in _records(data["task"]).items()})
    if data.get("target_accepted"):
        target = equipment_targets.receipt(draft)
        payload["application"]["source"] += "\n" + equipment_targets.describe(target) + "\nApplicability: " + target["reason"]
    return payload
