"""Surface-drip sampled discharge evaluation; no assumed causes or field totals."""

from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .resources import _object
from .validation import aware_time, choice, number, text

SOURCES = (
    "UC ANR: timed emitter-volume sampling and location/pressure evidence. https://ucanr.edu/site/maintenance-microirrigation-systems/emitter-evaluations",
    "UC ANR: low-quarter discharge divided by mean discharge. https://www.ucanr.edu/site/maintenance-microirrigation-systems/what-does-professional-evaluation-information-mean",
)


def evaluate_emitter_test(payload):
    data = _object(payload, ("schema_version", "site", "event", "sampling", "samples"), "emitter test")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _object(data["site"], ("name", "timezone"), "site")
    text("site name", site["name"])
    text("timezone", site["timezone"])
    try:
        zone = ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    event = _object(data["event"], ("zone_name", "started_at", "ended_at", "kind", "stable_operation", "conditions_source"), "event")
    for key in ("zone_name", "started_at", "ended_at", "conditions_source"):
        text(key, event[key])
    choice("kind", event["kind"], ("observed", "scenario"))
    start = aware_time("started_at", datetime.fromisoformat(event["started_at"]))
    end = aware_time("ended_at", datetime.fromisoformat(event["ended_at"]))
    try:
        start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        elapsed = (end_utc-start_utc).total_seconds()
        start.astimezone(zone)
        end_local = end.astimezone(zone)
    except OverflowError as exc:
        raise ValueError("test dates fall outside supported dates") from exc
    number("test elapsed seconds", elapsed, low=1, high=86400)
    if event["kind"] == "observed" and end_utc > datetime.now(timezone.utc):
        raise ValueError("an observed emitter test must have ended")
    if event["stable_operation"] is not True:
        raise ValueError("stable_operation must be explicitly true for this one-zone test")
    sampling = _object(data["sampling"], ("expected_count", "same_emitter_type", "equal_representation", "source"), "sampling")
    count = sampling["expected_count"]
    if type(count) is not int or not 4 <= count <= 2500 or count % 4:
        raise ValueError("expected_count must be 4–2500 and divisible by four; do not discard measurements to fit")
    for key in ("same_emitter_type", "equal_representation"):
        if sampling[key] is not True:
            raise ValueError(key+" must be explicitly true; mixed emitter specifications or unequal sampling weights need another method")
    text("sampling source", sampling["source"])
    if not isinstance(data["samples"], list) or len(data["samples"]) != count:
        raise ValueError("include every expected emitter; use null volume for an unreadable measurement")
    ids, locations, rates, rows, pressures, missing = set(), set(), [], [], [], []
    for row in data["samples"]:
        row = _object(row, ("id", "lateral", "position", "volume_ml", "duration_seconds", "pressure_kpa", "source"), "sample")
        for key in ("id", "lateral", "position", "source"):
            text(key, row[key])
        identifier = row["id"].strip()
        location = row["lateral"].strip().casefold(), row["position"].strip().casefold()
        if identifier in ids or location in locations:
            raise ValueError("sample IDs and lateral/position pairs must be unique; repeated emitters need a separate test")
        ids.add(identifier)
        locations.add(location)
        number("collection duration_seconds", row["duration_seconds"], low=1, high=elapsed)
        if row["pressure_kpa"] is not None:
            pressures.append(number("pressure_kpa", row["pressure_kpa"], low=0, high=10000))
        rate = None
        if row["volume_ml"] is None:
            missing.append("volume_ml for "+identifier)
        else:
            number("volume_ml", row["volume_ml"], low=0, high=1000000)
            rate = Fraction(str(row["volume_ml"]))*Fraction(18, 5)/Fraction(str(row["duration_seconds"]))
            rates.append((rate, identifier))
        rows.append(dict(row, flow_l_h=float(rate) if rate is not None else None,
                         flow_l_h_exact=str(rate) if rate is not None else None))
    stats = dict(mean_flow_l_h=None, low_quarter_mean_flow_l_h=None, low_quarter_du_pct=None,
                 minimum_flow_l_h=None, maximum_flow_l_h=None)
    exact, low_ids, notes = {}, [], []
    if missing:
        notes.append("Uniformity and sample-wide statistics are withheld while expected volumes are unknown. Known individual flows remain visible.")
    else:
        rates.sort()
        low = rates[:count//4]
        mean = sum((r[0] for r in rates), Fraction())/count
        lower = sum((r[0] for r in low), Fraction())/len(low)
        values = dict(mean_flow_l_h=mean, low_quarter_mean_flow_l_h=lower,
            low_quarter_du_pct=100*lower/mean if mean else None, minimum_flow_l_h=rates[0][0], maximum_flow_l_h=rates[-1][0])
        stats = {key: float(value) if value is not None else None for key, value in values.items()}
        exact = {key: str(value) if value is not None else None for key, value in values.items()}
        low_ids = [r[1] for r in low]
        if not mean:
            notes.append("All sampled flows are explicitly zero; low-quarter uniformity is undefined.")
    if len(pressures) != count:
        notes.append("Some operating pressures are unknown. Pressure summaries cover only supplied readings.")
    return dict(model_version="drip-emitter-test-v1", evidence_status="experimental", inputs=deepcopy(data),
        event_ended_at=end_local.isoformat(), sample_count=count, samples=deepcopy(rows), statistics=stats, exact_statistics=exact,
        low_quarter_sample_ids=low_ids, missing_inputs=missing, notes=notes,
        pressure_summary=dict(known_samples=len(pressures), unknown_samples=count-len(pressures),
            minimum_kpa=min(pressures) if pressures else None, maximum_kpa=max(pressures) if pressures else None),
        feasible=None, binding_constraints=[], sources=list(SOURCES),
        assumptions=["One surface-drip zone under stable operation; selected emitters share a specification and equal representation weights.",
            "Each distinct emitter's collected volume is normalized by its own timed collection. Test-window timing does not establish when each sample was taken.",
            "The lowest N/4 discharge rates define the low quarter; IDs break equal-rate ties without changing statistics."],
        limitations=["Sampled emitter discharge uniformity is not whole-field soil-water uniformity, design emission uniformity, irrigation efficiency or evidence of savings.",
            "No extrapolated zone flow, total irrigation volume, runtime recommendation, automatic diagnosis or universal pass/fail threshold.",
            "Sample placement, pressure representativeness, stable conditions and complete collection are operator assertions requiring field review.",
            "Unsupported: mixed emitter specifications, unequal sampling weights, repeat samples of one emitter and count not divisible by four. Do not remove measurements to fit.",
            "Observed remains an operator label; scenarios are hypothetical. Saving this calculation creates no measurements, daily water totals, task completion or equipment action."])
