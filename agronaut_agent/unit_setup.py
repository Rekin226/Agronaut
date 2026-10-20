"""Durable, deterministic production-unit setup for text channels.

Setup drafts live at account scope, outside any selected unit's facts/history.
No farm is created until all required answers validate. Completion, unit selection,
and draft removal share one transaction; cancel leaves the original selection alone.
"""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.validation import text

from .farm_setup import create_unit
from .unit_context import UnitContexts

SCHEMA = """CREATE TABLE unit_setup_drafts (
    owner_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL)"""

_CHOICES = {
    "production_system": ("soil", "hydroponics", "aquaponics"),
    "environment": ("open_field", "greenhouse", "indoor"),
    "medium": ("water", "substrate"),
    "water_strategy": ("rainfed", "supplemental", "fully_irrigated"),
    "irrigation_method": ("drip", "sprinkler", "surface"),
}
_LABELS = {
    "soil": "Soil cultivation", "hydroponics": "Hydroponics", "aquaponics": "Aquaponics",
    "open_field": "Open field / outdoors", "greenhouse": "Greenhouse", "indoor": "Indoors",
    "water": "Water", "substrate": "Substrate",
    "rainfed": "Rainfall only", "supplemental": "Rainfall plus supplemental irrigation",
    "fully_irrigated": "Fully irrigated", "drip": "Drip", "sprinkler": "Sprinkler", "surface": "Surface irrigation",
}
_QUESTIONS = {
    "production_system": "How will this unit produce crops?",
    "farm_name": "What is the farm's name?",
    "timezone": "What is the farm's timezone? Use an IANA name, such as Africa/Accra, Asia/Taipei, or UTC.",
    "name": "What should I call this production unit? For example, North tomato field.",
    "environment": "Where is this unit grown?",
    "medium": "What growing medium does this unit use?",
    "water_strategy": "How is this field supplied with water?",
    "irrigation_method": "Which irrigation method does this field use?",
}


def requests_soil_setup(message):
    """A routing hint only; never assigns a domain or saves inferred farm facts."""
    if re.search(r"\b(aquaponics?|hydroponics?|fish|ponds?)\b", message, re.I):
        return False
    return bool(re.search(r"\b(drip|sprinkler|rainfed|furrow|soil[ -]grown|open[ -]field|"
                          r"surface irrigation|field crops?|hectares?)\b", message, re.I))


def _next(draft):
    data = draft["data"]
    if "production_system" not in data:
        return "production_system"
    if "farm_id" not in data:
        if draft["farms"]:
            return "farm"
        data["farm_id"] = None
    if data["farm_id"] is None:
        for name in ("farm_name", "timezone"):
            if name not in data:
                return name
    else:
        farm = next(f for f in draft["farms"] if f["id"] == data["farm_id"])
        if not farm["timezone"] and "timezone" not in data:
            return "timezone"
    for name in ("name", "environment"):
        if name not in data:
            return name
    if data["production_system"] != "soil":
        return "medium" if "medium" not in data else None
    if "water_strategy" not in data:
        return "water_strategy"
    if data["water_strategy"] != "rainfed" and "irrigation_method" not in data:
        return "irrigation_method"
    return None


def _question(draft):
    step = _next(draft)
    if step == "farm":
        choices = ["0. New farm"] + [f"{i}. {farm['name']}" for i, farm in enumerate(draft["farms"], 1)]
        question = "Which farm should this unit belong to?\n" + "\n".join(choices)
    else:
        question = _QUESTIONS[step]
        if step in _CHOICES:
            question += "\n" + "\n".join(f"{i}. {_LABELS[v]} ({v})" for i, v in enumerate(_CHOICES[step], 1))
    return question + "\nReply with a choice or value. /back edits the previous answer; /cancel stops setup."


def _answer(draft, answer):
    answer = text("answer", answer).strip()
    step = _next(draft)
    data = draft["data"]
    if step in _CHOICES:
        key = answer.lower().replace(" ", "_")
        choices = _CHOICES[step]
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            key = choices[int(answer) - 1]
        labels = {_LABELS[c].lower(): c for c in choices}
        key = labels.get(answer.lower(), key)
        aliases = {"outdoors": "open_field", "outdoor": "open_field",
                   "drip_irrigation": "drip", "sprinkler_irrigation": "sprinkler"}
        key = aliases.get(key, key)
        if key not in choices:
            raise ValueError("Choose one of the listed options; I won't guess this detail.")
        data[step] = key
    elif step == "farm":
        if answer.lower() in ("0", "new"):
            data["farm_id"] = None
        elif answer.isdigit() and 1 <= int(answer) <= len(draft["farms"]):
            data["farm_id"] = draft["farms"][int(answer)-1]["id"]
        elif answer in {f["id"] for f in draft["farms"]}:
            data["farm_id"] = answer
        else:
            raise ValueError("Choose a listed farm or 0 for a new farm.")
    elif step == "timezone":
        try:
            ZoneInfo(answer)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use an IANA timezone name so records use the correct local day.") from exc
        data[step] = answer
    else:
        if len(answer) > 200 or "\n" in answer or answer.startswith("/"):
            raise ValueError("Use a name of at most 200 characters on one line.")
        data[step] = answer


class UnitSetup:
    def __init__(self, db):
        self.db = db

    def active(self, owner):
        return bool(self.db.query("SELECT 1 FROM unit_setup_drafts WHERE owner_id=?", (owner,)))

    def start(self, owner, production_system=None):
        if production_system is not None and production_system not in _CHOICES["production_system"]:
            raise ValueError("Use /newunit, /newunit soil, /newunit hydroponics, or /newunit aquaponics.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT payload FROM unit_setup_drafts WHERE owner_id=?", (owner,)).fetchone()
            if row:
                return "Resuming your unfinished setup.\n" + _question(json.loads(row[0]))
            farms = [dict(r) for r in conn.execute("SELECT id,name,timezone FROM farms WHERE owner_id=? ORDER BY id", (owner,))]
            draft = dict(data={} if production_system is None else {"production_system": production_system},
                         history=[], farms=farms)
            question = _question(draft)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO unit_setup_drafts VALUES(?,?,?,?)", (owner, now, now, json.dumps(draft)))
            return "Let's create a production unit. Your current unit stays selected until setup is finished.\n" + question

    def cancel(self, owner):
        deleted = self.db.execute("DELETE FROM unit_setup_drafts WHERE owner_id=?", (owner,)).rowcount
        return ("Unit setup cancelled. Your existing farms and selection are unchanged." if deleted
                else "No unit setup is in progress. Start with /newunit.")

    def respond(self, owner, answer, *, back=False):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT payload FROM unit_setup_drafts WHERE owner_id=?", (owner,)).fetchone()
            if row is None:
                return "No unit setup is in progress. Start with /newunit."
            draft = json.loads(row[0])
            if back:
                if draft["history"]:
                    draft["data"] = draft["history"].pop()
                else:
                    return "This is the first question.\n" + _question(draft)
            else:
                before = dict(draft["data"])
                try:
                    _answer(draft, answer)
                except ValueError as exc:
                    return str(exc) + "\n" + _question(draft)
                draft["history"].append(before)
            if _next(draft) is None:
                data = draft["data"]
                if data["farm_id"] and "timezone" in data:
                    row = conn.execute("SELECT timezone FROM farms WHERE id=? AND owner_id=?",
                                       (data["farm_id"], owner)).fetchone()
                    if row is None:
                        raise ValueError("the chosen farm no longer exists")
                    if row[0] not in (None, data["timezone"]):
                        raise ValueError("the farm timezone changed during setup; restart to use its current settings")
                    conn.execute("UPDATE farms SET timezone=? WHERE id=? AND owner_id=?",
                                 (data["timezone"], data["farm_id"], owner))
                farm = ({"id": data["farm_id"]} if data["farm_id"] else
                        {"name": data["farm_name"], "timezone": data["timezone"]})
                soil = data["production_system"] == "soil"
                request = dict(farm=farm, unit=dict(name=data["name"], production_system=data["production_system"],
                    environment=data["environment"], medium="soil" if soil else data["medium"],
                    water_strategy=data["water_strategy"] if soil else "fully_irrigated",
                    irrigation_method=data.get("irrigation_method")))
                unit = create_unit(self.db, owner, request, connection=conn)
                UnitContexts(self.db).select(owner, unit["id"], connection=conn)
                conn.execute("DELETE FROM unit_setup_drafts WHERE owner_id=?", (owner,))
                return (f"Created and selected {unit['name']} ({unit['production_system']}). "
                        f"Its records will stay separate. Unit ID: {unit['id']}.\n"
                        "Tell me which crop you grow and what you want to plan or investigate.")
            conn.execute("UPDATE unit_setup_drafts SET updated_at=?,payload=? WHERE owner_id=?",
                         (datetime.now(timezone.utc).isoformat(), json.dumps(draft), owner))
            return _question(draft)
