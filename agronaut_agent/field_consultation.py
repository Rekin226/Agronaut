"""Resumable, unit-scoped field-water and equipment questions.

The wizard accepts supplied daily reference ET, meteorology or explicitly fetched model weather. Advanced
curves, root growth and measurement adoption remain available as saved revisions.
"""

import json
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import aware_time, number, text

from . import equipment_consultation as equipment
from . import equipment_targets
from . import meteorology_consultation as meteorology
from . import surface_consultation as surface
from .field_io import _date, render_field_report
from .field_planning_language import normalize_answer, translate, validate_language, words
from .field_workflow import _calculate, _json, _scope

SCHEMA = """CREATE TABLE IF NOT EXISTS field_intake_drafts (
    unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
    id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, payload TEXT NOT NULL)"""


def _provider(draft):
    return draft["data"].get("weather_input") == "open_meteo"


def _weather_request(draft):
    data = draft["data"]
    return dict(latitude=data["latitude"], longitude=data["longitude"], timezone=draft["timezone"],
                start=data["start"], end=(_date(data["start"]) + timedelta(days=data["days"] - 1)).isoformat())


def _steps(draft):
    data = draft["data"]
    task_question = "Choose 1 for crop-water demand, or 2 for a soil-water budget (needs soil properties)."
    if draft.get("intake_version", 1) >= 3:
        task_question += (" Choose 3 to check a flat pressure-compensating drip layout, or 4 to check a flat "
                          "fixed full-circle sprinkler grid. Equipment checks are experimental and require supplied specifications.")
    if draft.get("intake_version", 1) >= 5:
        task_question += " Choose 5 to evaluate a supplied surface-irrigation event (furrow, border or basin)."
    steps = [("task", task_question, "task")]
    if draft["cycles"]:
        choices = "\n".join(f"{i}. {c['crop']} · planted {c['planted_on']} · {c['id']}" for i, c in enumerate(draft["cycles"], 1))
        steps.append(("cycle_id", "Which crop cycle? Reply 0 for no assigned cycle, or choose:\n" + choices, "cycle"))
    if data.get("task") in equipment.TASKS:
        return steps + equipment.steps(draft)
    if data.get("task") == "surface_evaluation":
        return steps + surface.steps(draft)
    if not data.get("cycle_id"):
        steps.append(("crop", "Which crop will this calculation describe?", "text"))
    steps.extend([
        ("area_m2", "What planted area does this calculation cover? Include units, for example 1 ha or 10000 m2.", "area"),
        ("start", "What is the first weather date? Use YYYY-MM-DD in the farm's local timezone.", "date"),
        ("days", "How many consecutive days? Enter a whole number from 1 to 31.", "days"),
        ("stage", "What crop stage does your supplied coefficient represent?", "text"),
        ("kc", "What applicable crop coefficient (Kc) will you use? Enter a supplied value from 0 to 3; no value is inferred from the crop.", "kc"),
        ("kc_source", "What is the source and local applicability of that Kc? This intake uses one Kc; dated curves can be added after saving.", "text"),
    ])
    # Drafts created before this choice existed keep their original manual flow.
    if draft.get("intake_version", 1) >= 2:
        question = (meteorology.WEATHER_CHOICE if draft.get("intake_version", 1) >= 6 else
                    "Choose manual to enter daily ET0/rainfall, or open_meteo to fetch model estimates. "
                    "Open-Meteo supports windows of 1–16 days, within the last 90 days through 14 days ahead.")
        steps.append(("weather_input", question, "weather_input"))
    if _provider(draft):
        steps.extend([
            ("latitude", "What is the field's WGS84 latitude in decimal degrees (−90 to 90)?", "latitude"),
            ("longitude", "What is the field's WGS84 longitude in decimal degrees (−180 to 180)?", "longitude"),
        ])
        details = ""
        if "latitude" in data and "longitude" in data:
            request = _weather_request(draft)
            details = (f" Coordinates: {request['latitude']}, {request['longitude']}; "
                       f"dates: {request['start']} through {request['end']}; timezone: {request['timezone']}.")
        steps.append(("weather_bundle", "Reply fetch to send these coordinates and dates to Open-Meteo and retrieve model weather."
                      + details + " /back edits the location or returns to manual input. Coverage may vary by model.", "fetch"))
    else:
        steps.append(("weather_kind", "What type of weather input is this? Choose observed, historical (model estimate), scenario, or forecast.", "kind"))
        steps.extend(meteorology.setup_steps(draft) if meteorology.active(draft) else [
            ("weather_method", "What method produced your daily grass-reference ET0 totals? This intake requires supplied ET0 in mm per local day.", "text"),
            ("weather_source", "What is the source of these daily ET0 and rainfall values? Unknown weather must not be entered as zero.", "text")])
    if not _provider(draft) and data.get("weather_kind") == "forecast":
        steps.append(("issued_at", "When was this forecast issued? Supply an ISO timestamp with UTC offset.", "timestamp"))
    if data.get("task") == "root_zone_budget":
        steps.extend([
            ("fc", "What is soil field capacity as a volumetric percentage (0–100)? Example: enter 32 for 0.32 m³/m³.", "percent"),
            ("wp", "What is soil wilting point as a volumetric percentage? It must be below field capacity.", "wp"),
            ("root_depth", "What effective root depth applies initially, in metres? This intake keeps it fixed; daily depths can be added after saving.", "depth"),
            ("p", "What fraction of available water may be depleted before stress? Enter a supplied percentage strictly between 0 and 100.", "p"),
            ("soil_source", "What is the source and applicability of those soil properties, depth and depletion fraction?", "text"),
            ("depletion", "What is root-zone depletion in mm at the end of the day BEFORE the window? Reply unknown if unavailable; zero means field capacity.", "depletion"),
        ])
        if data.get("depletion") is not None:
            steps.append(("initial_source", "What is the source of that initial depletion?", "text"))
        steps.extend([
            ("efficiency", "What application efficiency applies, as a percentage greater than 0 and at most 100?", "efficiency"),
            ("efficiency_source", "What is the source and applicability of that efficiency?", "text"),
            ("event_source", "What is the source of the irrigation and rainfall-runoff assumptions you will supply? These will be calculation inputs, not completed-work logs.", "text"),
        ])
    for index in range(data.get("days", 0)):
        day = (_date(data["start"]) + timedelta(days=index)).isoformat()
        if not _provider(draft):
            if meteorology.active(draft):
                steps.extend(meteorology.daily_steps(draft, index))
            else:
                steps.append((f"et0_{index}", f"{day}: what is total grass-reference ET0 in mm over this entire local day?", "amount"))
        if data.get("task") == "root_zone_budget":
            if not _provider(draft):
                steps.append((f"rain_{index}", f"{day}: what is total rainfall in mm? Enter zero only if explicitly known or assumed in your scenario.", "amount"))
            steps.extend([
                (f"irrigation_{index}", f"{day}: what net irrigation in mm enters the root zone? This is after application losses, not pumped volume; explicit zero is allowed.", "amount"),
                (f"runoff_{index}", f"{day}: what rainfall runoff in mm is assumed? It cannot exceed that day's rainfall.", "runoff"),
            ])
    return steps


def _next(draft):
    return next((step for step in _steps(draft) if step[0] not in draft["data"]), None)


def _remember(draft, before):
    if draft.get("history_format", 1) == 1:
        draft["history"].append(before)
        return
    after = draft["data"]
    draft["history"].append(dict(added=[key for key in after if key not in before],
                                 previous={key: value for key, value in before.items()
                                           if key not in after or value != after[key]}))


def _back(draft):
    if not draft["history"]:
        return
    entry = draft["history"].pop()
    if draft.get("history_format", 1) == 1:
        draft["data"] = entry
    else:
        for key in entry["added"]:
            draft["data"].pop(key, None)
        draft["data"].update(entry["previous"])


def _answer(draft, answer):
    answer = text("answer", answer).strip()
    key, _, kind = _next(draft)
    data = draft["data"]
    if kind == "equipment":
        equipment.answer(draft, key, answer)
        return
    if kind == "surface":
        surface.answer(draft, key, answer)
        return
    if kind == "meteorology":
        meteorology.answer(draft, key, answer)
        return
    if kind == "text":
        if len(answer) > 2000 or answer.startswith("/"):
            raise ValueError("Supply a description or source of at most 2000 characters.")
        value = answer
    elif kind == "task":
        choices = {"1": "crop_water", "2": "root_zone_budget", "demand": "crop_water", "budget": "root_zone_budget"}
        if draft.get("intake_version", 1) >= 3:
            choices.update({"3": "drip_design", "4": "sprinkler_design", "drip": "drip_design", "sprinkler": "sprinkler_design"})
        if draft.get("intake_version", 1) >= 5:
            choices.update({"5": "surface_evaluation", "surface": "surface_evaluation"})
        value = choices.get(answer.lower(), answer.lower())
        if value not in choices.values():
            raise ValueError("Choose one of the listed calculation types.")
    elif kind == "cycle":
        options = {str(i): c["id"] for i, c in enumerate(draft["cycles"], 1)}
        value = None if answer.lower() in ("0", "none") else options.get(answer, answer)
        if value is not None and value not in {c["id"] for c in draft["cycles"]}:
            raise ValueError("Choose one of the listed cycles, or 0.")
    elif kind == "kind":
        value = answer.lower()
        if value not in ("observed", "historical", "scenario", "forecast"):
            raise ValueError("Choose observed, historical, scenario or forecast.")
    elif kind == "weather_input":
        value = answer.lower()
        allowed = ("manual", "open_meteo", "meteorology") if draft.get("intake_version", 1) >= 6 else ("manual", "open_meteo")
        if value not in allowed:
            raise ValueError("Choose " + ", ".join(allowed) + ".")
        if value == "open_meteo" and data["days"] > 16:
            raise ValueError("Open-Meteo supports at most 16 days; choose manual or /back to shorten the window.")
    elif kind == "date":
        value = _date(answer).isoformat()
        cycle = next((c for c in draft["cycles"] if c["id"] == data.get("cycle_id")), None)
        if cycle and value < cycle["planted_on"]:
            raise ValueError("The calculation cannot begin before this cycle's planting date.")
        if _date(value).year >= 9999 or _date(value).year <= 1:
            raise ValueError("Choose a date with room for the preceding state and following weather days.")
    elif kind == "timestamp":
        stamp = datetime.fromisoformat(answer)
        aware_time("forecast issuance", stamp)
        value = stamp.isoformat()
    elif kind == "area":
        match = re.fullmatch(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(ha|hectares?|m2|m²|square metres?|square meters?)", answer, re.I)
        if not match:
            raise ValueError("Supply an area with explicit units, such as 1 hectare or 10000 m2.")
        value = float(match[1]) * (10000 if match[2].lower().startswith(('ha', 'hectare')) else 1)
        number("planted area", value, positive=True)
    elif kind == "depletion" and answer.lower() == "unknown":
        value = None
    else:
        try:
            value = float(answer)
        except ValueError as exc:
            raise ValueError("Supply a number in the stated units. Missing values cannot be assumed; /back edits earlier answers.") from exc
        high = {"days": 31, "kc": 3, "percent": 100, "wp": 100, "depth": 10, "p": 100, "efficiency": 100,
                "latitude": 90, "longitude": 180}.get(kind)
        if kind == "runoff":
            index = key.split("_")[-1]
            high = data["weather_bundle"]["weather"][int(index)]["rainfall_mm"] if _provider(draft) else data["rain_" + index]
        if kind == "depletion":
            high = 1000 * (data["fc"] - data["wp"]) / 100 * data["root_depth"]
        number(key, value, {"latitude": -90, "longitude": -180}.get(kind, 0), high,
               positive=kind in ("days", "depth", "p", "efficiency"))
        if kind == "days":
            if not value.is_integer():
                raise ValueError("The number of days must be a whole number.")
            value = int(value)
        if kind == "wp" and value >= data["fc"]:
            raise ValueError("Wilting point must be below field capacity.")
        if kind == "p" and value >= 100:
            raise ValueError("The depletion threshold must be strictly below 100 percent.")
    data[key] = value


def _inputs(draft):
    data = draft["data"]
    if data["task"] in equipment.TASKS:
        return equipment.inputs(draft)
    if data["task"] == "surface_evaluation":
        return surface.inputs(draft)
    cycle = next((c for c in draft["cycles"] if c["id"] == data.get("cycle_id")), None)
    payload = dict(schema_version=1, site=dict(name=draft["unit_name"], timezone=draft["timezone"]),
        area_m2=data["area_m2"], crop=dict(name=cycle["crop"] if cycle else data["crop"],
        coefficient=dict(value=data["kc"], stage=data["stage"], source=data["kc_source"])), weather=[])
    zone = ZoneInfo(draft["timezone"])
    if _provider(draft):
        payload["weather"] = data["weather_bundle"]["weather"]
        payload["weather_sources"] = data["weather_bundle"]["weather_sources"]
    for index in range(0 if _provider(draft) else data["days"]):
        if meteorology.active(draft):
            payload["weather"].append(meteorology.weather_record(draft, index))
            continue
        day = _date(data["start"]) + timedelta(days=index)
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        payload["weather"].append(dict(input_type="supplied_et0", day=day.isoformat(), et0_mm_day=data[f"et0_{index}"],
            rainfall_mm=data.get(f"rain_{index}"), source=data["weather_source"], kind=data["weather_kind"],
            timezone=draft["timezone"], period_hours=(end-start).total_seconds()/3600, method=data["weather_method"],
            **(dict(issued_at=data["issued_at"]) if data["weather_kind"] == "forecast" else {})))
    if data["task"] == "root_zone_budget":
        payload["soil"] = dict(field_capacity=data["fc"]/100, wilting_point=data["wp"]/100, root_depth_m=data["root_depth"],
                               depletion_fraction=data["p"]/100, source=data["soil_source"])
        payload["application_efficiency"] = dict(value=data["efficiency"]/100, source=data["efficiency_source"])
        if data["depletion"] is not None:
            payload["initial_state"] = dict(as_of=(_date(data["start"])-timedelta(days=1)).isoformat(),
                depletion_mm=data["depletion"], source=data["initial_source"])
        payload["events"] = [dict(day=w["day"], net_irrigation_mm=data[f"irrigation_{i}"], runoff_mm=data[f"runoff_{i}"],
                                  source=data["event_source"]) for i, w in enumerate(payload["weather"])]
    return payload


def _view(draft):
    from .domains import run_capability
    step = _next(draft)
    result = dict(draft_id=draft["id"], revision=draft["revision"], unit_id=draft["unit_id"], unit_name=draft["unit_name"],
                  status="collecting" if step else "ready", answers=draft["data"], next_field=step[0] if step else None,
                  question=step[1] if step else "Review the calculation below. Reply save to store it, /back to edit, or /cancel to discard the draft.")
    result["language"] = draft.get("language", "en")
    result["question"] = translate(result["question"], result["language"], draft)
    if not step:
        result["inputs"] = _inputs(draft)
        result["preview"] = run_capability("soil", draft["data"]["task"], result["inputs"])
    if draft["data"].get("target_accepted"):
        result["water_target"] = equipment_targets.receipt(draft)
    return result


def render_intake(view):
    if view is None:
        return "No field-planning intake is active for this unit. Start with /fieldplan."
    if view.get("status") == "saved":
        saved = view["calculation"]
        language = saved.get("intake_language", "en")
        if language != "en":
            return (words(language)["saved"].format(id=saved["id"]) + "\n" + words(language)["note"] + "\n"
                    + (equipment_targets.describe(saved["water_target"]) + "\n" if saved.get("water_target") else "")
                    + _render_calculation(saved["task"], saved["result"]["result"]))
        if saved["task"] == "surface_evaluation":
            return (f"Saved surface-event evaluation {saved['id']}. No daily water log or equipment operation was recorded.\n"
                    + _render_calculation(saved["task"], saved["result"]["result"]))
        if saved["task"] in equipment.TASKS:
            return (f"Saved irrigation-equipment calculation {saved['id']}. No irrigation or equipment operation was recorded.\n"
                    + (equipment_targets.describe(saved["water_target"]) + "\n" if saved.get("water_target") else "")
                    + _render_calculation(saved["task"], saved["result"]["result"]))
        return f"Saved field-water calculation {saved['id']}. No completed irrigation was recorded.\n" + render_field_report(saved["result"]["result"])
    is_equipment = view.get("answers", {}).get("task") in equipment.TASKS
    title = "Irrigation-equipment intake" if is_equipment else "Field-water intake"
    if view.get("answers", {}).get("task") == "surface_evaluation":
        title = "Surface-event intake"
    body = f"{title} — {view['unit_name']}\n{view['question']}"
    language = view.get("language", "en")
    if language != "en":
        body = f"{words(language)['title']} — {view['unit_name']}\n{words(language)['note']}\n{view['question']}"
    if view.get("water_target"):
        body += "\n" + equipment_targets.describe(view["water_target"])
    bundle = view.get("answers", {}).get("weather_bundle")
    if bundle:
        source = bundle["weather_sources"][0]
        body += (f"\n\n{bundle['provider']}\nRetrieved: {source['retrieved_at']} (not model issuance). "
                 f"Model grid: {bundle['grid']['latitude']}, {bundle['grid']['longitude']}; check site representativeness.")
        for weather in bundle["weather"]:
            body += (f"\n{weather['day']} · {weather['kind']} · {weather['period_hours']:g} h · "
                     f"ET0 {weather['et0_mm_day']:g} mm · rain {weather['rainfall_mm']:g} mm")
    if view.get("preview"):
        body += "\n\n" + _render_calculation(view["answers"]["task"], view["preview"]["result"])
    else:
        body += ("\n/back edits the previous answer; /cancel discards this unit's draft. /fieldplan resumes it."
                 if language == "en" else "\n" + words(language)["footer"])
    return body


def _render_calculation(task, result):
    from .irrigation_io import render_drip_report, render_sprinkler_report
    from .surface_io import render_surface_report
    renderer = {"drip_design": render_drip_report, "sprinkler_design": render_sprinkler_report,
                "surface_evaluation": render_surface_report}.get(task, render_field_report)
    return renderer(result)


class FieldConsultation:
    def __init__(self, db):
        self.db = db

    def active(self, owner, unit_id):
        return bool(self.db.query("SELECT 1 FROM field_intake_drafts d JOIN production_units u ON u.id=d.unit_id "
                                  "JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?", (unit_id, owner)))

    def _read(self, conn, owner, unit_id):
        _scope(conn, owner, unit_id)
        row = conn.execute("SELECT payload FROM field_intake_drafts WHERE unit_id=?", (unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, owner, unit_id):
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            return _view(draft) if draft else None

    def start(self, owner, unit_id, language=None):
        validate_language(language)
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM task_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel task entry for this unit before another intake.')
            draft = self._read(conn, owner, unit_id)
            if draft:
                if language is not None and language != draft.get("language", "en"):
                    draft["language"] = language
                    return self._store(conn, draft)
                return _view(draft)
            if conn.execute('SELECT 1 FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel harvest entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel measurement entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM water_log_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel water logging for this unit before another intake.')
            if conn.execute("SELECT 1 FROM planting_intake_drafts WHERE unit_id=?", (unit_id,)).fetchone():
                raise ValueError("Finish or cancel planting entry for this unit before field planning.")
            if conn.execute("SELECT 1 FROM scouting_intake_drafts WHERE unit_id=?", (unit_id,)).fetchone():
                raise ValueError("Finish or cancel scouting for this unit before starting /fieldplan.")
            tz, _ = _scope(conn, owner, unit_id)
            unit = conn.execute("SELECT name FROM production_units WHERE id=?", (unit_id,)).fetchone()
            draft = dict(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, unit_id=unit_id, unit_name=unit[0], timezone=tz,
                language=language or "en",
                intake_version=6, data={}, history=[], history_format=2, water_budgets=equipment_targets.catalog(conn, unit_id),
                cycles=[dict(c) for c in conn.execute(
                    "SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id", (unit_id,))])
            now = datetime.now(timezone.utc).isoformat()
            draft.update(created_at=now, updated_at=now)
            conn.execute("INSERT INTO field_intake_drafts VALUES(?,?,?,?,?,?)",
                         (unit_id, draft["id"], draft["revision"], now, now, _json(draft)))
            return _view(draft)

    def answer(self, owner, unit_id, answer, expected_revision, *, back=False):
        if type(back) is not bool:
            raise ValueError("back must be a boolean")
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft["revision"] != expected_revision:
                raise ValueError("field intake changed or ended; resume it before answering")
            if not back and _next(draft) is not None:
                answer = normalize_answer(draft, _next(draft)[0], answer)
            if not back and _next(draft) is not None and _next(draft)[2] == "equipment_target":
                before = dict(draft["data"])
                equipment_targets.answer(conn, owner, draft, _next(draft)[0], answer)
                _remember(draft, before)
                return self._store(conn, draft)
            fetch = not back and _next(draft) is not None and _next(draft)[2] == "fetch"
            if fetch:
                if text("answer", answer).strip() != "fetch":
                    raise ValueError("Reply fetch to retrieve weather, or /back to edit. No weather was requested.")
                tz, _ = _scope(conn, owner, unit_id)
                if tz != draft["timezone"]:
                    raise ValueError("farm timezone changed; cancel and restart the intake before fetching")
                request = _weather_request(draft)
            else:
                return self._answer_local(conn, draft, answer, back)

        # Network I/O must not hold a database transaction. Recheck after it returns.
        from .weather_provider import adapt_response, fetch_weather
        bundle = fetch_weather(request)
        sources = bundle.get("weather_sources", [])
        if len(sources) != 1 or sources[0].get("request") != request:
            raise ValueError("weather provider snapshot does not match the requested location and dates")
        source = sources[0]
        if bundle != adapt_response(request, source["response"], source["retrieved_at"]):
            raise ValueError("weather provider bundle does not match its retained snapshot")
        with self.db.transaction() as conn:
            current = self._read(conn, owner, unit_id)
            tz, _ = _scope(conn, owner, unit_id)
            if (current is None or current["id"] != draft["id"] or current["revision"] != expected_revision
                    or tz != draft["timezone"]):
                raise ValueError("field intake changed or ended during weather retrieval; resume it before answering")
            before = dict(current["data"])
            current["data"]["weather_bundle"] = bundle
            _remember(current, before)
            return self._store(conn, current)

    def _answer_local(self, conn, draft, answer, back):
        if back:
            _back(draft)
        else:
            if _next(draft) is None:
                raise ValueError("The draft is ready. Reply save, use /back, or /cancel.")
            before = dict(draft["data"])
            _answer(draft, answer)
            _remember(draft, before)
        return self._store(conn, draft)

    def _store(self, conn, draft):
        draft["revision"] = uuid.uuid4().hex
        draft["updated_at"] = datetime.now(timezone.utc).isoformat()
        view = _view(draft)
        conn.execute("UPDATE field_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?",
                     (draft["revision"], draft["updated_at"], _json(draft), draft["unit_id"]))
        return view

    def cancel(self, owner, unit_id, expected_revision):
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft["revision"] != expected_revision:
                raise ValueError("field intake changed or ended; resume it before cancelling")
            conn.execute("DELETE FROM field_intake_drafts WHERE unit_id=?", (unit_id,))
            return dict(status="cancelled", unit_id=unit_id)

    def save(self, owner, unit_id, draft_id, expected_revision):
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            text("draft_id", draft_id)
            text("expected_revision", expected_revision)
            operation = dict(kind="save_intake", draft_id=draft_id, expected_revision=expected_revision)
            request_id = "field-intake:" + draft_id
            from .field_workflow import _retry
            previous = _retry(conn, "field_calculations", unit_id, request_id, operation)
            if previous:
                return dict(status="saved", calculation=previous)
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft["id"] != draft_id or draft["revision"] != expected_revision:
                raise ValueError("field intake changed or ended; review its current version before saving")
            if _next(draft):
                raise ValueError("answer the remaining field-planning questions before saving")
            target = equipment_targets.validate(conn, owner, draft) if draft["data"].get("target_accepted") else None
            saved = _calculate(conn, owner, unit_id, draft["data"].get("cycle_id"), request_id,
                               draft["data"]["task"], _inputs(draft), operation)
            if target:
                saved["water_target"] = target
            if draft.get("language", "en") != "en":
                saved["intake_language"] = draft["language"]
            if target or saved.get("intake_language"):
                conn.execute("UPDATE field_calculations SET payload=? WHERE id=?", (_json(saved), saved["id"]))
            conn.execute("DELETE FROM field_intake_drafts WHERE unit_id=?", (unit_id,))
            return dict(status="saved", calculation=saved)
