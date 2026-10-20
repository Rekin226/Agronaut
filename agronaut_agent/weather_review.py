"""Read-only timestamp review of the weather retained in an owned calculation."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import aware_time, number

from .field_io import parse_weather_records
from .field_workflow import _read, _scope

NOTE = (
    "The age limit is supplied by the operator, not an agronomic freshness standard. "
    "Download age does not establish model issuance age. Passing an age check does not "
    "establish forecast accuracy, measurement quality or site representativeness. "
    "Only weather directly retained in this calculation is reviewed; linked calculations "
    "have separate reviews. No weather is fetched, relabelled or replaced, and no plan or actual is changed."
)


class WeatherReview:
    def __init__(self, db):
        self.db = db

    def review(self, owner, unit_id, calculation_id, max_age_hours, as_of=None):
        limit = number("max_age_hours", max_age_hours, positive=True)
        instant = datetime.now(timezone.utc) if as_of is None else as_of
        if isinstance(instant, str):
            try:
                instant = datetime.fromisoformat(instant)
            except ValueError as exc:
                raise ValueError("as_of requires an ISO timestamp with UTC offset") from exc
        if not isinstance(instant, datetime):
            raise ValueError("as_of requires an ISO timestamp with UTC offset")
        aware_time("as_of", instant)
        instant = instant.astimezone(timezone.utc)
        with self.db.transaction() as conn:
            farm_timezone, _ = _scope(conn, owner, unit_id)
            saved = _read(conn, "field_calculations", unit_id, calculation_id)
        inputs = saved["inputs"]
        records = inputs.get("weather", [])
        rows, issues = [], []
        site_timezone = inputs.get("site", {}).get("timezone")
        local_day = None
        if records:
            if not site_timezone:
                raise ValueError("saved weather requires its site timezone")
            local_day = instant.astimezone(ZoneInfo(site_timezone)).date()
            if farm_timezone != site_timezone:
                issues.append("farm_timezone_differs_from_saved_site")
            for raw, weather in zip(records, parse_weather_records(records)):
                issued = getattr(weather, "issued_at", None)
                retrieved = getattr(weather, "retrieved_at", None)
                flags = []
                for name, stamp in (("issued_at", issued), ("retrieved_at", retrieved)):
                    if stamp is not None and stamp > instant:
                        flags.append(name + "_after_review_time")
                if issued is not None and retrieved is not None and issued > retrieved:
                    flags.append("issuance_after_retrieval")
                basis, stamp, age, within_limit = None, None, None, None
                if weather.kind == "forecast":
                    basis = "issued_at" if issued is not None else "retrieved_at" if retrieved is not None else None
                    stamp = issued if issued is not None else retrieved
                    if issued is None:
                        flags.append("model_issuance_unknown")
                    if stamp is not None:
                        age = (instant - stamp.astimezone(timezone.utc)).total_seconds() / 3600
                        within_limit = 0 <= age <= limit
                        if age > limit:
                            flags.append("age_limit_exceeded")
                    if weather.day < local_day:
                        flags.append("forecast_day_has_elapsed")
                elif weather.kind in ("observed", "historical") and weather.day > local_day:
                    flags.append("nonforecast_day_after_review_date")
                rows.append(dict(day=weather.day.isoformat(), kind=weather.kind, source=weather.source,
                    source_snapshot_id=raw.get("source_snapshot_id"), issued_at=raw.get("issued_at"),
                    retrieved_at=raw.get("retrieved_at"), age_basis=basis, age_hours=age,
                    within_age_limit=within_limit, flags=flags,
                    status="needs_review" if flags else "within_age_limit" if weather.kind == "forecast" else "not_a_forecast"))
        return dict(schema_version=1, calculation_id=saved["id"], unit_id=unit_id,
            calculation_created_at=saved["created_at"], as_of=instant.isoformat(),
            site_timezone=site_timezone, farm_timezone=farm_timezone,
            local_date=local_day.isoformat() if local_day else None, max_age_hours=limit,
            status="no_weather_inputs" if not rows else "needs_review" if issues or any(r["flags"] for r in rows) else "reviewed",
            issues=issues, rows=rows, forecast_count=sum(r["kind"] == "forecast" for r in rows),
            flagged_day_count=sum(bool(r["flags"]) for r in rows), note=NOTE)
