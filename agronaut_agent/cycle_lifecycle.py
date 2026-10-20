"""Crop closure checks shared by journals and actual field records.

Callers must establish ownership first and use the same transaction for reads/writes.
Closure is a recorded end date, not a forecast or evidence that a crop succeeded.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .observation_quality import reviewed_observation


def lifecycle(conn, unit_id, cycle_id):
    row = conn.execute("SELECT payload FROM crop_records WHERE unit_id=? AND cycle_id=? "
                       "AND kind='lifecycle' ORDER BY rowid DESC LIMIT 1", (unit_id, cycle_id)).fetchone()
    if row is None:
        return dict(status="open", ended_on=None, revision_id=None, reason=None, source=None, recorded_at=None)
    record = json.loads(row[0])
    return dict(record["data"], revision_id=record["id"], recorded_at=record["created_at"])


def ensure_cycle_day(conn, unit_id, cycle_id, day):
    if cycle_id is None:
        return
    state = lifecycle(conn, unit_id, cycle_id)
    if state["status"] == "closed" and day > state["ended_on"]:
        raise ValueError(f"record date is after the crop cycle end ({state['ended_on']}); reopen or correct the closure first")


def latest_actual_day(conn, unit_id, cycle_id, timezone):
    days = []
    for table in ("crop_records", "field_water_days", "field_surface_water_days"):
        # table names are code-owned constants; only current, accepted actuals constrain closure.
        rows = conn.execute(f"SELECT r.payload FROM {table} r WHERE r.unit_id=? AND r.cycle_id=? "
                            f"AND NOT EXISTS (SELECT 1 FROM {table} n WHERE n.supersedes_id=r.id)",
                            (unit_id, cycle_id))
        for row in rows:
            record = json.loads(row[0])
            if record.get("voided") or record["data"].get("quality") == "rejected":
                continue
            if table == "crop_records" and record["kind"] not in ("stage", "scouting", "harvest", "fertilizer", "transplant"):
                continue
            days.append(record["data"]["day"])
    for row in conn.execute("SELECT payload FROM field_observations WHERE unit_id=? AND cycle_id=?", (unit_id, cycle_id)):
        data = reviewed_observation(conn, json.loads(row[0]))
        if data["provenance"] != "forecast" and data["effective_quality"] != "rejected":
            days.append(datetime.fromisoformat(data["observed_at"]).astimezone(ZoneInfo(timezone)).date().isoformat())
    return max(days, default=None)
