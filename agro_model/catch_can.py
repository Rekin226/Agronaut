"""Equal-area sprinkler catch-grid arithmetic, with explicit test applicability."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import fsum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .resources import _object
from .validation import aware_time, choice, number, text

SOURCES = [
    "UF/IFAS AE194: low-quarter catch depth divided by overall mean depth. https://ask.ifas.ufl.edu/publication/AE194",
    "USU Extension: one-zone grid catch tests, measured depths and elapsed runtime. https://extension.usu.edu/cwel/research/simple-sprinkler-performance-testing-for-washington-county",
]


def evaluate_catch_test(payload):
    data = _object(payload, ("schema_version", "site", "event", "grid", "catches"), "catch test")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _object(data["site"], ("name", "timezone"), "site")
    text("site name", site["name"])
    text("site timezone", site["timezone"])
    try:
        zone = ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    event = _object(data["event"], ("zone_name", "started_at", "duration_seconds", "kind", "rain_free",
        "same_exposure", "wind_m_s", "conditions_source"), "event")
    for key in ("zone_name", "started_at", "conditions_source"):
        text(key, event[key])
    choice("event kind", event["kind"], ("observed", "scenario"))
    started = aware_time("started_at", datetime.fromisoformat(event["started_at"]))
    duration = number("duration_seconds", event["duration_seconds"], low=1, high=86400)
    try:
        ended = started.astimezone(timezone.utc)+timedelta(seconds=duration)
        started.astimezone(zone)
        ended_local = ended.astimezone(zone)
    except OverflowError as exc:
        raise ValueError("test dates fall outside supported dates") from exc
    if event["kind"] == "observed" and ended > datetime.now(timezone.utc):
        raise ValueError("an observed catch test must be completed, not future")
    for key in ("rain_free", "same_exposure"):
        if event[key] is not True:
            raise ValueError(f"{key} must be explicitly true: all collectors need the same full rain-free exposure in one zone")
    if event["wind_m_s"] is not None:
        number("wind_m_s", event["wind_m_s"], low=0, high=100)
    grid = _object(data["grid"], ("rows", "columns", "spacing_x_m", "spacing_y_m", "equal_area", "source"), "grid")
    for key in ("rows", "columns"):
        if type(grid[key]) is not int or not 2 <= grid[key] <= 100:
            raise ValueError(f"grid {key} must be an integer from 2 to 100")
    count = grid["rows"]*grid["columns"]
    if count > 2500 or count % 4:
        raise ValueError("this method requires 4–2500 grid positions with a count divisible by four; do not discard readings to fit")
    for key in ("spacing_x_m", "spacing_y_m"):
        number(key, grid[key], positive=True, high=1000)
    if grid["equal_area"] is not True:
        raise ValueError("equal_area must be explicitly true: each grid point represents equal area")
    text("grid source", grid["source"])
    if not isinstance(data["catches"], list) or len(data["catches"]) != count:
        raise ValueError("include every grid position exactly once; use null depth for an unknown or unreadable catch")
    seen, depths, missing = set(), [], []
    for row in data["catches"]:
        row = _object(row, ("row", "column", "depth_mm", "source"), "catch")
        for key, limit in (("row", grid["rows"]), ("column", grid["columns"])):
            if type(row[key]) is not int or not 1 <= row[key] <= limit:
                raise ValueError(f"catch {key} must identify a position in the grid")
        position = row["row"], row["column"]
        if position in seen:
            raise ValueError("duplicate catch grid position")
        seen.add(position)
        text("catch source", row["source"])
        if row["depth_mm"] is None:
            missing.append(f"depth_mm at row {position[0]}, column {position[1]}")
        else:
            depths.append((number("catch depth_mm", row["depth_mm"], low=0, high=1000), *position))
    stats = dict(mean_depth_mm=None, low_quarter_mean_depth_mm=None, low_quarter_du_pct=None,
                 mean_catch_rate_mm_h=None, minimum_depth_mm=None, maximum_depth_mm=None)
    low = []
    notes = []
    if not missing:
        depths.sort()
        low = depths[:count//4]
        mean = fsum(d[0] for d in depths)/count
        lower = fsum(d[0] for d in low)/len(low)
        stats.update(mean_depth_mm=mean, low_quarter_mean_depth_mm=lower,
            low_quarter_du_pct=100*(lower/mean) if mean else None,
            mean_catch_rate_mm_h=mean*3600/duration, minimum_depth_mm=depths[0][0], maximum_depth_mm=depths[-1][0])
        if mean == 0:
            notes.append("All depths are explicitly zero. Uniformity is undefined because mean catch is zero.")
    else:
        notes.append("Statistics are withheld because the complete declared grid has unknown depths. Missing is not zero.")
    if event["wind_m_s"] is None:
        notes.append("Wind speed is unknown; test conditions are incomplete.")
    return dict(model_version="sprinkler-catch-grid-v1", evidence_status="experimental", inputs=deepcopy(data),
        event_ended_at=ended_local.isoformat(), sample_count=count, missing_inputs=missing,
        statistics=stats, low_quarter_positions=[dict(row=r, column=c) for _, r, c in low],
        feasible=None, binding_constraints=[], sources=list(SOURCES), notes=notes,
        assumptions=["All listed straight-sided collectors report calibrated depths in mm for one zone and the same complete exposure.",
            "A complete rectangular grid represents equal areas. Layout, spacing, rain-free exposure and conditions are operator assertions.",
            "Low quarter is the lowest N/4 depths; row/column order breaks equal-depth ties without changing the statistic."],
        limitations=["Catch-grid uniformity describes this sample and exposure, not infiltrated root-zone water, application efficiency, water savings or crop response.",
            "No universal pass/fail threshold, runtime recommendation, terrain correction or whole-field extrapolation is applied.",
            "Unsupported: unequal-area or radial center-pivot weighting, rainfall-contaminated tests, different exposure times, volume-only readings and fractional-quarter sample counts.",
            "Collector calibration, evaporation, splashing, obstruction and spatial representativeness require field review. A small grid is not proof of field performance.",
            "Observed is an operator label, not independent verification. Scenarios remain hypothetical; saving creates no daily water logs or equipment actions."])
