"""One owned snapshot of crop cycles and recorded work across production units."""

import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.validation import text

from .crop_calendar import calendar_summary
from .cycle_lifecycle import lifecycle
from .equipment_calendar import calendar_snapshot

NOTE = ("Recorded crop cycles, crop-calendar tasks and active equipment reservations; follow-ups have a separate view. "
        "No tasks means no recorded work, "
        "not that the farm needs no attention. Each farm uses its own local date. Review flags "
        "and due windows are not agronomic recommendations or proof of performed work. "
        "Parent fields and irrigation zones remain separate; areas, water and yields are not summed. "
        "Equipment intervals are reservations, not actual operation; elapsed intervals do not prove completed work. "
        "This snapshot does not select a unit, change records or send notifications.")


def _equipment(conn, farm, instant, today):
    calendar = calendar_snapshot(conn, farm)
    zone = ZoneInfo(farm['timezone']) if today is not None else None
    bounds = None
    if zone is not None:
        day = date.fromisoformat(today)
        bounds = [datetime.combine(d, time.min, zone).astimezone(timezone.utc)
                  for d in (day, day + timedelta(days=1))]
    bookings = []
    for booking in calendar['active']:
        intervals = []
        for interval in booking['intervals']:
            start, end = (datetime.fromisoformat(interval[key]) for key in ('start', 'end'))
            timing = 'elapsed' if end <= instant else 'upcoming' if start > instant else 'ongoing'
            asset = booking['equipment_snapshots'][interval['equipment_root_id']]
            intervals.append(dict(interval, equipment_name=asset['data']['name'],
                timing=timing, today=(start < bounds[1] and end > bounds[0]) if bounds else None,
                local_start=start.astimezone(zone).isoformat() if zone else None,
                local_end=end.astimezone(zone).isoformat() if zone else None))
        timing = next((state for state in ('ongoing', 'upcoming')
                       if any(i['timing'] == state for i in intervals)), 'elapsed')
        bookings.append(dict(booking_id=booking['id'], title=booking['title'], source=booking['source'],
            plan_id=booking['plan_id'], booked_timezone=booking['timezone'],
            timing=timing, today=any(i['today'] for i in intervals) if bounds else None,
            review=booking['review'], intervals=intervals))
    bookings.sort(key=lambda b: (min(i['start'] for i in b['intervals']), b['booking_id']))
    counts = {state: sum(b['timing'] == state for b in bookings)
              for state in ('ongoing', 'upcoming', 'elapsed')}
    counts.update(active=len(bookings), needs_review=sum(b['review']['needs_review'] for b in bookings),
                  today=sum(b['today'] for b in bookings) if bounds else None)
    return dict(equipment_count=len(calendar['equipment']), counts=counts, bookings=bookings,
        attention=[b for b in bookings if b['today'] is not False or b['timing'] == 'ongoing' or b['review']['needs_review']])


class FarmOverview:
    def __init__(self, db):
        self.db = db

    def get(self, owner, farm_id=None, *, now=None):
        text("owner", owner)
        if farm_id is not None:
            text("farm_id", farm_id)
        instant = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("overview time must be an aware datetime")
        farms = []
        with self.db.transaction() as conn:
            owned = conn.execute("SELECT * FROM farms WHERE owner_id=? AND (? IS NULL OR id=?) ORDER BY name,id",
                                 (owner, farm_id, farm_id)).fetchall()
            if farm_id is not None and not owned:
                raise ValueError("unknown farm for this owner")
            for row in owned:
                farm = dict(row)
                issue, today = None, None
                try:
                    if not farm["timezone"]:
                        raise ValueError("missing timezone")
                    today = instant.astimezone(ZoneInfo(farm["timezone"])).date().isoformat()
                except (ValueError, ZoneInfoNotFoundError):
                    issue = "Farm timezone is missing or invalid; task timing and current crop dates cannot be determined."
                units = []
                for unit_row in conn.execute("SELECT u.*,p.name AS parent_name FROM production_units u "
                    "LEFT JOIN production_units p ON p.id=u.parent_unit_id WHERE u.farm_id=? ORDER BY u.name,u.id", (farm["id"],)):
                    unit = dict(unit_row)
                    cycles = []
                    for cycle_row in conn.execute("SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id", (unit["id"],)):
                        cycle = dict(cycle_row)
                        state = lifecycle(conn, unit["id"], cycle["id"])
                        status = "closed" if state["status"] == "closed" else "date_unknown" if today is None else (
                            "future_dated" if cycle["planted_on"] > today else "open")
                        records = [dict(json.loads(r[0]), superseded=False) for r in conn.execute(
                            "SELECT r.payload FROM crop_records r WHERE r.unit_id=? AND r.cycle_id=? AND r.kind='calendar' "
                            "AND NOT EXISTS (SELECT 1 FROM crop_records n WHERE n.supersedes_id=r.id) ORDER BY r.rowid",
                            (unit["id"], cycle["id"]))]
                        calendar = calendar_summary(conn, unit["id"], cycle, records, today) if today is not None else None
                        # Keep the full task fields, including original windows and evidence review reasons.
                        tasks = calendar["tasks"] if calendar else []
                        cycles.append(dict(cycle=cycle, lifecycle=state, status=status,
                            task_counts=calendar["counts"] if calendar else None,
                            tasks=tasks, unclassified_task_count=len(records) if calendar is None else 0,
                            attention=[t for t in tasks if t["needs_review"] or t["timing"] in ("due", "overdue", "unknown")]))
                    units.append(dict(unit=unit, cycles=cycles,
                        cycle_counts={key: sum(c["status"] == key for c in cycles)
                                      for key in ("open", "closed", "future_dated", "date_unknown")}))
                farms.append(dict(farm=farm, local_date=today, issue=issue, units=units,
                                  equipment=_equipment(conn, farm, instant, today)))
        return dict(schema_version=1, generated_at=instant.astimezone(timezone.utc).isoformat(),
                    farms=farms, farm_count=len(farms), unit_count=sum(len(f["units"]) for f in farms), note=NOTE)
