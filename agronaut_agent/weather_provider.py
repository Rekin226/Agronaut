"""Explicit Open-Meteo intake with reproducible, offline-verifiable snapshots.

Hourly accumulations end at the labelled UTC timestamp. Local-day boundaries
come from zoneinfo, not the provider's fixed-offset daily aggregation.
"""

import hashlib
import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.validation import aware_time, number, text

ADAPTER = "open-meteo-hourly-v1"
DOCS = "https://open-meteo.com/en/docs"
SOURCE = "Open-Meteo weather model estimates; https://open-meteo.com/ (CC BY 4.0)"
METHOD = "Provider FAO-56 grass-reference ET0; hourly sums aggregated to local days"
VARIABLES = ("et0_fao_evapotranspiration", "precipitation", "snowfall")
MAX_BYTES = 2_000_000
ASSUMPTIONS = [
    "Hourly totals are distributed uniformly within each hour when local midnight splits an hour.",
    "Precipitation is used as liquid rainfall only when every requested snowfall value is zero.",
    "Past model estimates are historical, not observed measurements.",
    "Model issuance is unavailable; retrieved_at is the download time, not the forecast issue time.",
    "Grid coordinates may differ from requested coordinates; assess site representativeness.",
]


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _date(value):
    text("weather date", value)
    day = date.fromisoformat(value)
    if value != day.isoformat():
        raise ValueError("weather date requires YYYY-MM-DD")
    return day


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {"latitude", "longitude", "timezone", "start", "end"}:
        raise ValueError("weather request requires latitude, longitude, timezone, start and end")
    number("latitude", request["latitude"], -90, 90)
    number("longitude", request["longitude"], -180, 180)
    text("timezone", request["timezone"])
    try:
        zone = ZoneInfo(request["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("weather timezone must be an IANA timezone") from exc
    start, end = _date(request["start"]), _date(request["end"])
    if not 0 <= (end - start).days < 16:
        raise ValueError("weather window must contain 1 to 16 consecutive days")
    bounds = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        first = datetime.combine(day, time.min, zone).timestamp()
        last = datetime.combine(day + timedelta(days=1), time.min, zone).timestamp()
        if last <= first:
            raise ValueError("weather window includes a nonexistent local calendar day")
        bounds.append((day, int(first), int(last)))
    return zone, bounds


def _hours(bounds):
    return list(range((bounds[0][1] // 3600 + 1) * 3600,
                      math.ceil(bounds[-1][2] / 3600) * 3600 + 1, 3600))


def adapt_response(request, response, retrieved_at):
    """Pure transformation. No network, current-clock dependence, or filled gaps."""
    zone, bounds = validate_request(request)
    if isinstance(retrieved_at, str):
        retrieved_at = datetime.fromisoformat(retrieved_at)
    aware_time("retrieved_at", retrieved_at)
    retrieved_at = retrieved_at.astimezone(timezone.utc)
    if not isinstance(response, dict) or response.get("error"):
        raise ValueError("weather provider did not return a successful object")
    if len(_json(response).encode()) > MAX_BYTES:
        raise ValueError("weather provider response exceeds the size limit")
    number("provider latitude", response.get("latitude"), -90, 90)
    number("provider longitude", response.get("longitude"), -180, 180)
    number("provider elevation", response.get("elevation"))
    if (type(response.get("utc_offset_seconds")) is not int or response["utc_offset_seconds"] != 0
            or response.get("timezone") not in ("GMT", "UTC", "Etc/GMT", "Etc/UTC")):
        raise ValueError("weather provider must return UTC hourly data")
    units, hourly = response.get("hourly_units"), response.get("hourly")
    expected_units = dict(time="unixtime", et0_fao_evapotranspiration="mm", precipitation="mm", snowfall="cm")
    if not isinstance(units, dict) or any(units.get(k) != v for k, v in expected_units.items()):
        raise ValueError("weather provider units must be Unix seconds, mm ET0, mm precipitation and cm snowfall")
    if not isinstance(hourly, dict):
        raise ValueError("weather provider hourly arrays are missing")
    hours = _hours(bounds)
    timestamps = hourly.get("time")
    if (not isinstance(timestamps, list) or any(type(t) is not int for t in timestamps)
            or timestamps != hours):
        raise ValueError("weather provider must cover every requested UTC hour exactly once and in order")
    for variable in VARIABLES:
        values = hourly.get(variable)
        if not isinstance(values, list) or len(values) != len(hours):
            raise ValueError(f"weather provider {variable} must cover every requested hour")
        for value in values:
            number(variable, value, 0)
    if any(hourly["snowfall"]):
        raise ValueError("snowfall is unsupported by the liquid-water bucket; supply reviewed snowmelt inputs separately")
    snapshot = dict(adapter=ADAPTER, request=request, retrieved_at=retrieved_at.isoformat(), response=response,
                    assumptions=ASSUMPTIONS)
    snapshot["id"] = hashlib.sha256(_json(snapshot).encode()).hexdigest()
    records = []
    for day, start, end in bounds:
        sums = {v: math.fsum(hourly[v][i] * max(0, min(hour, end) - max(hour - 3600, start)) / 3600
                             for i, hour in enumerate(hours)) for v in VARIABLES[:2]}
        records.append(dict(input_type="supplied_et0", day=day.isoformat(),
                            et0_mm_day=sums[VARIABLES[0]], rainfall_mm=sums[VARIABLES[1]],
                            source=SOURCE, method=METHOD, reference_surface="grass",
                            kind="historical" if day < retrieved_at.astimezone(zone).date() else "forecast",
                            timezone=request["timezone"], period_hours=(end - start) / 3600,
                            retrieved_at=retrieved_at.isoformat(), source_snapshot_id=snapshot["id"]))
    # Detach all caller-owned objects before returning the reproducible snapshot.
    return json.loads(_json(dict(weather=records, weather_sources=[snapshot],
        provider=SOURCE, documentation=DOCS,
        assumptions=ASSUMPTIONS,
        grid={key: response[key] for key in ("latitude", "longitude", "elevation")})))


def validate_sources(records, sources, site_timezone):
    """Require exact snapshot-derived provider records; reject tampering and dangling references."""
    if not isinstance(sources, list) or len(sources) > 64:
        raise ValueError("weather_sources must be an array of at most 64 snapshots")
    expected = {}
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"adapter", "request", "response", "retrieved_at", "id", "assumptions"}:
            raise ValueError("invalid weather source snapshot")
        if source["adapter"] != ADAPTER:
            raise ValueError("unsupported weather source adapter")
        bundle = adapt_response(source["request"], source["response"], source["retrieved_at"])
        identifier = bundle["weather_sources"][0]["id"]
        if source != bundle["weather_sources"][0] or identifier in expected:
            raise ValueError("weather source snapshot hash mismatch or duplicate")
        if source["request"]["timezone"] != site_timezone:
            raise ValueError("weather source timezone must match the site timezone")
        expected[identifier] = {w["day"]: w for w in bundle["weather"]}
    used = set()
    for record in records:
        identifier = record.get("source_snapshot_id")
        if identifier is not None:
            if not isinstance(identifier, str) or record != expected.get(identifier, {}).get(record["day"]):
                raise ValueError("weather record must match its retained provider snapshot")
            used.add(identifier)
    if used != set(expected):
        raise ValueError("weather_sources contains an unreferenced snapshot")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("weather provider redirects are not accepted")


def _download(params):
    key = os.environ.get("OPEN_METEO_API_KEY")
    host = "customer-api.open-meteo.com" if key else "api.open-meteo.com"
    query = dict(params, apikey=key) if key else params
    request = Request(f"https://{host}/v1/forecast?{urlencode(query)}",
                      headers={"User-Agent": "Agronaut-weather/1", "Accept": "application/json"})
    try:
        with build_opener(_NoRedirect).open(request, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"weather provider returned HTTP {exc.code}; check access and date availability") from None
    except (URLError, OSError):
        raise ValueError("weather provider could not be reached; retry or use manual weather inputs") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("weather provider response exceeds the size limit")
    from .field_cli import _reject_constant, _unique_keys
    try:
        return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
    except (ValueError, UnicodeError):
        raise ValueError("weather provider returned invalid JSON") from None


def fetch_weather(request):
    """One explicit read-only request. Coordinates leave the device only here."""
    zone, bounds = validate_request(request)
    today = datetime.now(timezone.utc).astimezone(zone).date()
    if bounds[0][0] < today - timedelta(days=90) or bounds[-1][0] > today + timedelta(days=14):
        raise ValueError("weather fetch supports the last 90 days through 14 days ahead; actual coverage varies by model")
    hours = _hours(bounds)
    params = dict(latitude=request["latitude"], longitude=request["longitude"],
                  timezone="GMT", timeformat="unixtime", precipitation_unit="mm",
                  hourly=",".join(VARIABLES),
                  start_hour=datetime.fromtimestamp(hours[0], timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                  end_hour=datetime.fromtimestamp(hours[-1], timezone.utc).strftime("%Y-%m-%dT%H:%M"))
    response = _download(params)
    return adapt_response(request, response, datetime.now(timezone.utc))
