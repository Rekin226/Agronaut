"""Explicit, resumable questions for the existing surface-event evaluation model."""

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agro_model.surface import Assessment, Event, FlowInterval, Geometry, Infiltration, Station
from agro_model.validation import aware_time, number, text

RECORDS = dict(geometry=Geometry, event=Event, infiltration=Infiltration, assessment=Assessment)
ARRAYS = dict(stations=Station, inflow=FlowInterval, tailwater=FlowInterval)


def steps(draft):
    d = draft["data"]
    furrow = d.get("geometry.method") == "furrow"
    basis = "volume_per_length_m2" if furrow else "depth_mm"
    unit = "m² (m³ per metre of furrow)" if furrow else "mm of depth"
    prompts = [
        ("geometry.method", "Evaluate ONE furrow, border or basin with supplied timings. Choose furrow, border or basin. This experimental model does not predict advance/recession or recommend a cutoff time."),
        ("geometry.length_m", "What is the evaluated length from inlet to field end, in metres?"),
        ("geometry.width_m", "What spacing in metres does this ONE furrow represent? This is not wetted perimeter." if furrow
         else "What is this border or basin's constant width, in metres?"),
        ("geometry.slope_m_m", "What longitudinal slope applies, in metres/metre (not percent)? Supply a measured or explicit scenario value from 0 to 0.2."),
        ("geometry.source", "What survey or explicit scenario supports the method, dimensions and slope?"),
        ("event.kind", "Choose observed for a completed measured/reported event, or scenario for hypothetical timings and flows. Scenario results are not predictions of advance or recession."),
        ("event.started_at", f"When did the event start? Supply an ISO timestamp with UTC offset. The farm timezone is {draft['timezone']}; all following times are elapsed minutes from this instant."),
        ("event.monitoring_end_min", "How many elapsed minutes after the start did monitoring end, after all surface water had receded? Maximum 10080 minutes."),
        ("event.cutoff_min", "How many elapsed minutes after the start was inflow stopped? Supply the observed or scenario cutoff; this model does not choose it."),
        ("event.rainfall_mm", "How much rainfall occurred during the event, in mm? This model supports rain-free events only: enter 0 only if documented or explicitly assumed."),
        ("event.initial_surface_water_l", "How many litres were already ponded at the start? This model supports initially unponded events only: enter 0 only if documented or explicitly assumed."),
        ("event.surface_drained", "Had all surface water drained/receded by monitoring end? Reply yes only if documented or explicitly assumed. Residual ponding is unsupported."),
        ("event.source", "What event log or explicit scenario supports the start, cutoff, monitoring end and rain/ponding conditions?"),
        ("infiltration.basis", f"Confirm the infiltration coefficient basis by entering {basis}. Cumulative intake Z is in {unit}; elapsed opportunity time t is in minutes."),
        ("infiltration.exponent", "What supplied exponent a applies in Z = k × t^a + steady_rate × t? Enter greater than 0 and at most 1."),
        ("infiltration.k", f"What supplied Kostiakov coefficient k applies, in {unit} per minute^a? No soil intake family is inferred."),
        ("infiltration.steady_rate", f"What supplied steady intake rate applies, in {unit} per minute?"),
        ("infiltration.min_opportunity_min", "What minimum infiltration opportunity time, in elapsed minutes, is covered by the coefficient evidence?"),
        ("infiltration.max_opportunity_min", "What maximum infiltration opportunity time, in elapsed minutes, is covered by the coefficient evidence? Every station's recession minus advance must fall in this range."),
        ("infiltration.source", "What calibration or explicit scenario supports these infiltration coefficients and their opportunity-time range? For a furrow, include applicability to this discharge and wetted perimeter."),
        ("stations_count", "How many advance/recession stations are supplied, including inlet and field end? Enter a whole number from 2 to 10000."),
    ]
    for i in range(d.get("stations_count", 0)):
        required = "0" if i == 0 else str(d["geometry.length_m"]) if i == d["stations_count"] - 1 else None
        prompts.extend([
            (f"stations.{i}.distance_m", f"Station {i+1}: distance from the inlet in metres? "
             + (f"This endpoint must be {required} m." if required is not None else "Use increasing distances strictly between the endpoints.")),
            (f"stations.{i}.advance_min", f"Station {i+1}: elapsed minutes from event start when water first arrived? "
             + ("At the inlet this must be 0." if i == 0 else "Advance cannot precede the preceding station's advance.")),
            (f"stations.{i}.recession_min", f"Station {i+1}: elapsed minutes from event start when surface water receded? This is an absolute elapsed time, not a duration after arrival."),
            (f"stations.{i}.source", f"Station {i+1}: what observation log or explicit scenario supports its location and advance/recession times?"),
        ])
    for group, label in (("inflow", "inlet flow"), ("tailwater", "tailwater outflow")):
        prompts.append((f"{group}_count", f"How many consecutive {label} intervals cover the ENTIRE monitoring window? "
                        "Enter a whole number from 1 to 10000, including explicit zero-flow intervals. "
                        + ("Inflow must split at cutoff and be zero afterwards; surge irrigation is unsupported." if group == "inflow" else "No runoff still requires an explicit zero-flow interval and source.")))
        for i in range(d.get(f"{group}_count", 0)):
            start = 0 if i == 0 else d.get(f"{group}.{i-1}.end_min")
            prompts.extend([
                (f"{group}.{i}.start_min", f"{label.title()} interval {i+1}: start time in elapsed minutes? "
                 + (f"To cover time continuously, enter {start}." if start is not None else "It must equal the preceding interval's end.")),
                (f"{group}.{i}.end_min", f"{label.title()} interval {i+1}: end time in elapsed minutes? "
                 + (f"The final interval must end at monitoring end, {d['event.monitoring_end_min']} minutes." if i == d[f"{group}_count"]-1 else "It must be after its start and before monitoring end.")),
                (f"{group}.{i}.mean_lps", f"{label.title()} interval {i+1}: mean flow over this interval, in litres/SECOND (not litres/hour)? Enter zero only when documented or explicitly assumed."),
                (f"{group}.{i}.source", f"{label.title()} interval {i+1}: what measurement or explicit scenario supports this interval-average flow?"),
            ])
    prompts.extend([
        ("assessment.root_zone_deficit_mm", "What uniform root-zone deficit applied BEFORE this event, in mm? Reply unknown if unavailable; zero means no deficit, not missing data."),
        ("assessment.balance_tolerance_fraction", "What water-accounting tolerance is justified by the measurements, as a percentage from 0 to 20? Supply the value; no default is selected."),
        ("assessment.source", "What evidence and applicability support the pre-event deficit (or its absence) and accounting tolerance?"),
    ])
    return [(key, prompt, "surface") for key, prompt in prompts]


def _record(data, group, cls):
    values = {f.name: data[f"{group}.{f.name}"] for f in fields(cls)}
    if cls is Event:
        values["started_at"] = datetime.fromisoformat(values["started_at"])
    return cls(**values)


def answer(draft, key, raw):
    raw = text("answer", raw).strip()
    d = draft["data"]
    name = key.split(".")[-1]
    if name == "source":
        if len(raw) > 2000 or raw.startswith("/"):
            raise ValueError("Supply a source and applicability description of at most 2000 characters.")
        value = raw
    elif key in ("geometry.method", "event.kind", "infiltration.basis", "event.surface_drained"):
        choices = {"geometry.method": ("furrow", "border", "basin"), "event.kind": ("observed", "scenario"),
                   "event.surface_drained": ("yes",), "infiltration.basis":
                   ("volume_per_length_m2",) if d.get("geometry.method") == "furrow" else ("depth_mm",)}
        value = raw.lower()
        if value not in choices[key]:
            raise ValueError("Supported choice: " + ", ".join(choices[key]) + ". /back edits earlier answers.")
        if key == "event.surface_drained":
            value = True
    elif key == "event.started_at":
        stamp = datetime.fromisoformat(raw)
        aware_time("event start", stamp)
        cycle = next((c for c in draft["cycles"] if c["id"] == d.get("cycle_id")), None)
        try:
            local_date = stamp.astimezone(ZoneInfo(draft["timezone"])).date().isoformat()
            stamp.astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError("event start is outside the supported calendar") from exc
        if cycle and local_date < cycle["planted_on"]:
            raise ValueError("surface event starts before the selected crop cycle")
        if d["event.kind"] == "observed" and stamp >= datetime.now(timezone.utc):
            raise ValueError("observed evaluation requires a completed event; future timings must be an explicit scenario")
        value = stamp.isoformat()
    elif key == "assessment.root_zone_deficit_mm" and raw.lower() == "unknown":
        value = None
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("Supply a number in the stated units. Unknown flows and timings cannot be assumed.") from exc
        if key == "assessment.balance_tolerance_fraction":
            value /= 100
        limits = {"geometry.length_m": (.1, 10000), "geometry.width_m": (.01, 10000), "geometry.slope_m_m": (0, .2),
                  "event.monitoring_end_min": (0, 10080), "event.cutoff_min": (0, d.get("event.monitoring_end_min")),
                  "event.rainfall_mm": (0, 0), "event.initial_surface_water_l": (0, 0),
                  "infiltration.k": (0, 1e6), "infiltration.steady_rate": (0, 1e6), "infiltration.exponent": (0, 1),
                  "infiltration.min_opportunity_min": (0, d.get("event.monitoring_end_min")),
                  "infiltration.max_opportunity_min": (d.get("infiltration.min_opportunity_min", 0), 10080),
                  "assessment.root_zone_deficit_mm": (0, 10000), "assessment.balance_tolerance_fraction": (0, .2)}
        low, high = limits.get(key, (0, None))
        number(key, value, low, high, positive=key in ("event.monitoring_end_min", "event.cutoff_min", "infiltration.exponent"))
        if key == "event.monitoring_end_min":
            try:
                ended = datetime.fromisoformat(d["event.started_at"]).astimezone(timezone.utc) + timedelta(minutes=value)
            except OverflowError as exc:
                raise ValueError("monitoring end is outside the supported calendar") from exc
            if d["event.kind"] == "observed" and ended > datetime.now(timezone.utc):
                raise ValueError("observed evaluation requires monitoring to have finished")
        elif key.endswith("_count"):
            minimum = 2 if key == "stations_count" or (key == "inflow_count" and d["event.cutoff_min"] < d["event.monitoring_end_min"]) else 1
            number(key, value, minimum, 10000)
            if not value.is_integer():
                raise ValueError("Supply a whole number of records.")
            value = int(value)
        elif key.startswith("stations."):
            _station_value(d, key, value)
        elif key.startswith(("inflow.", "tailwater.")):
            _flow_value(d, key, value)
    candidate = dict(d, **{key: value})
    group = key.rsplit(".", 1)[0]
    cls = RECORDS.get(group) or ARRAYS.get(group.split(".")[0])
    if cls and all(f"{group}.{f.name}" in candidate for f in fields(cls)):
        record = _record(candidate, group, cls)
        if cls is Event and record.kind == "observed" and record.ended_at > datetime.now(timezone.utc):
            raise ValueError("observed evaluation requires monitoring to have finished")
    d[key] = value


def _station_value(d, key, value):
    _, index, name = key.split(".")
    i = int(index)
    prefix = f"stations.{i}."
    end = d["event.monitoring_end_min"]
    if name == "distance_m":
        number(key, value, 0, d["geometry.length_m"])
        if (i == 0 and value != 0) or (i == d["stations_count"]-1 and value != d["geometry.length_m"]):
            raise ValueError("station endpoints must be inlet zero and the full field length")
        if i and value <= d[f"stations.{i-1}.distance_m"]:
            raise ValueError("station distances must strictly increase")
        if i < d["stations_count"]-1 and value >= d["geometry.length_m"]:
            raise ValueError("only the final station can be at the field end")
    elif name == "advance_min":
        number(key, value, 0 if i == 0 else d[f"stations.{i-1}.advance_min"], end - d["infiltration.min_opportunity_min"])
        if i == 0 and value != 0:
            raise ValueError("inlet advance must be zero relative to event start")
    else:
        number(key, value, d[prefix + "advance_min"], end)
        number("station opportunity time", value - d[prefix + "advance_min"],
               d["infiltration.min_opportunity_min"], d["infiltration.max_opportunity_min"])


def _flow_value(d, key, value):
    group, index, name = key.split(".")
    i = int(index)
    prefix = f"{group}.{i}."
    end, cutoff = d["event.monitoring_end_min"], d["event.cutoff_min"]
    if name == "start_min":
        expected = 0 if i == 0 else d[f"{group}.{i-1}.end_min"]
        if value != expected:
            raise ValueError(f"{group} intervals must cover time continuously; this start must be {expected}")
    elif name == "end_min":
        start = d[prefix + "start_min"]
        number(key, value, 0, end)
        if value <= start:
            raise ValueError("flow interval end must follow its start")
        last = i == d[f"{group}_count"] - 1
        if (last and value != end) or (not last and value >= end):
            raise ValueError("only the final interval must end at monitoring end")
        if group == "inflow":
            if start < cutoff < value:
                raise ValueError("split inflow intervals exactly at cutoff")
            if i == d["inflow_count"]-2 and value < cutoff < end:
                raise ValueError("leave the last interval for explicit zero flow after cutoff; this interval must reach cutoff")
    else:
        number(key, value, 0, 1e6)
        if group == "inflow":
            if d[prefix + "end_min"] <= cutoff and value <= 0:
                raise ValueError("single continuous positive inflow is required before cutoff; surge is unsupported")
            if d[prefix + "start_min"] >= cutoff and value != 0:
                raise ValueError("inflow after cutoff must be an explicit zero")
        elif value > 0 and d[prefix + "end_min"] <= d[f"stations.{d['stations_count']-1}.advance_min"]:
            raise ValueError("tailwater cannot occur entirely before advance reaches the field end")


def inputs(draft):
    d = draft["data"]
    result = dict(schema_version=1, site=dict(name=draft["unit_name"], timezone=draft["timezone"]))
    result.update({group: {f.name: d[f"{group}.{f.name}"] for f in fields(cls)} for group, cls in RECORDS.items()})
    result.update({group: [{f.name: d[f"{group}.{i}.{f.name}"] for f in fields(cls)}
                           for i in range(d[f"{group}_count"])] for group, cls in ARRAYS.items()})
    return result
