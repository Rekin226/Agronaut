"""Resumable crop scouting intake; only a reviewed save appends an observation."""

import json
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import text

from .crop_records import _json, _scope, record_crop_observation
from .cycle_lifecycle import ensure_cycle_day
from .field_io import _date
from .scouting_language import TEXT, scouting_text, scouting_token, validate_language

SCHEMA = """CREATE TABLE IF NOT EXISTS scouting_intake_drafts (
    unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
    id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, payload TEXT NOT NULL)"""


def _steps(draft):
    words = TEXT[draft.get('language', 'en')]
    cycles = '\n'.join(f"{i}. {c['crop']} · {words['planted']} {c['planted_on']} · {c['id']}" for i,c in enumerate(draft['cycles'],1))
    questions = list(words['questions'])
    questions[0] += '\n'+cycles
    questions[1] = questions[1].format(timezone=draft['timezone'])
    return list(zip(('cycle_id', 'day', 'location', 'symptoms', 'counts', 'source', 'provenance'), questions))


def _next(draft):
    return next((s for s in _steps(draft) if s[0] not in draft['data']), None)


def _data(draft):
    data = draft['data']
    return dict(entry='scout:'+draft['id'], **{k:data[k] for k in ('day','location','symptoms','source','provenance')},
                inspected_count=data['counts'][0], affected_count=data['counts'][1])


def _view(draft):
    if draft is None:
        return None
    step = _next(draft)
    view = dict(draft_id=draft['id'], revision=draft['revision'], unit_id=draft['unit_id'], unit_name=draft['unit_name'],
                language=draft.get('language', 'en'), translation_status='original' if draft.get('language', 'en') == 'en' else 'draft',
                timezone=draft['timezone'], answers=draft['data'], status='collecting' if step else 'ready',
                next_field=step[0] if step else None,
                question=step[1] if step else scouting_text(draft, 'ready'))
    if not step:
        view['preview'] = dict(cycle=next(c for c in draft['cycles'] if c['id']==draft['data']['cycle_id']), data=_data(draft))
    return view


def render_scouting_intake(view):
    if view is None:
        return 'No scouting draft is active for this unit. Start with /scout.'
    if view['status']=='saved':
        return scouting_text(view, 'saved', id=view['record']['id'])
    words = TEXT[view.get('language', 'en')]
    lines = [words['title']+' — '+view['unit_name']]
    if words['note']:
        lines.append(words['note'])
    lines.append(view['question'])
    if view.get('preview'):
        data = view['preview']['data']
        values = [view['preview']['cycle']['crop'], data['day']+' ('+view['timezone']+')',
                  data['location'], data['symptoms'],
                  words['unknown'] if data['inspected_count'] is None else str(data['inspected_count']),
                  words['unknown'] if data['affected_count'] is None else str(data['affected_count']),
                  data['source'], words[data['provenance']]]
        lines += [label+': '+value for label,value in zip(words['labels'],values)]
    lines.append(words['footer'])
    return '\n'.join(lines)


class ScoutingConsultation:
    def __init__(self, db):
        self.db = db

    def active(self, owner, unit_id):
        return bool(self.db.query('SELECT 1 FROM scouting_intake_drafts d JOIN production_units u ON u.id=d.unit_id '
                                 'JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?',(unit_id,owner)))

    def _read(self, conn, owner, unit_id):
        _scope(conn,owner,unit_id)
        row = conn.execute('SELECT payload FROM scouting_intake_drafts WHERE unit_id=?',(unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, owner, unit_id):
        with self.db.transaction() as conn:
            return _view(self._read(conn,owner,unit_id))

    def start(self, owner, unit_id, language=None):
        if language is not None:
            validate_language(language)
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM task_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel task entry for this unit before another intake.')
            draft = self._read(conn,owner,unit_id)
            if draft:
                # Changing display language leaves the observation intact, but an
                # earlier preview/revision must be reviewed again before saving.
                if language is not None and language != draft.get('language', 'en'):
                    draft.update(language=language, revision=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat())
                    conn.execute('UPDATE scouting_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
                                 (draft['revision'],draft['updated_at'],_json(draft),unit_id))
                return _view(draft)
            if conn.execute('SELECT 1 FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel harvest entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel measurement entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM water_log_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel water logging for this unit before another intake.')
            if conn.execute('SELECT 1 FROM unit_setup_drafts WHERE owner_id=?',(owner,)).fetchone():
                raise ValueError('Finish or cancel unit setup before starting /scout.')
            if conn.execute('SELECT 1 FROM field_intake_drafts WHERE unit_id=?',(unit_id,)).fetchone():
                raise ValueError('Finish or cancel field planning for this unit before starting /scout.')
            if conn.execute('SELECT 1 FROM planting_intake_drafts WHERE unit_id=?',(unit_id,)).fetchone():
                raise ValueError('Finish or cancel planting entry for this unit before starting /scout.')
            tz,_ = _scope(conn,owner,unit_id)
            cycles = [dict(c) for c in conn.execute('SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id',(unit_id,))]
            if not cycles:
                raise ValueError('Create a crop cycle with /plant or Crop records first, then use /scout.')
            now = datetime.now(timezone.utc).isoformat()
            draft = dict(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, unit_id=unit_id,
                unit_name=conn.execute('SELECT name FROM production_units WHERE id=?',(unit_id,)).fetchone()[0],
                timezone=tz,cycles=cycles,data={},history=[],created_at=now,updated_at=now,language=language or 'en')
            conn.execute('INSERT INTO scouting_intake_drafts VALUES(?,?,?,?,?,?)',
                         (unit_id,draft['id'],draft['revision'],now,now,_json(draft)))
            return _view(draft)

    def _check(self, conn, owner, draft):
        tz,cycle = _scope(conn,owner,draft['unit_id'],draft['data'].get('cycle_id'))
        if tz != draft['timezone']:
            raise ValueError('Farm timezone changed; /cancel and start /scout again.')
        if cycle:
            original = next(c for c in draft['cycles'] if c['id']==cycle['id'])
            if any(cycle[k] != original[k] for k in ('crop','planted_on')):
                raise ValueError('Crop planting details changed; /cancel and start /scout again.')
        return tz,cycle

    def answer(self, owner, unit_id, answer, expected_revision, *, back=False):
        if type(back) is not bool:
            raise ValueError('back must be a boolean')
        with self.db.transaction() as conn:
            draft = self._read(conn,owner,unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise ValueError('Scouting draft changed or ended; resume /scout before answering.')
            tz,cycle = self._check(conn,owner,draft)
            if back:
                if draft['history']:
                    draft['data'] = draft['history'].pop()
            else:
                step = _next(draft)
                if step is None:
                    raise ValueError('The observation is ready; reply save, /back or /cancel.')
                value = text('answer',answer).strip()
                if len(value)>12000:
                    raise ValueError('Keep each answer within 12000 characters.')
                key = step[0]
                if key=='cycle_id':
                    choices = draft['cycles']
                    if value.isascii() and value.isdigit() and 1<=int(value)<=len(choices):
                        value = choices[int(value)-1]['id']
                    if value not in {c['id'] for c in choices}:
                        raise ValueError('Choose a listed crop cycle number or exact ID.')
                elif key=='day':
                    try:
                        day = _date(value)
                    except ValueError as exc:
                        raise ValueError('Use a valid observation date in YYYY-MM-DD format.') from exc
                    if day < _date(cycle['planted_on']) or day > datetime.now(ZoneInfo(tz)).date():
                        raise ValueError('Observation date must fall between planting and today in the farm timezone.')
                    ensure_cycle_day(conn,unit_id,cycle['id'],value)
                elif key=='counts':
                    if scouting_token(draft, value, 'unknown'):
                        value = [None,None]
                    else:
                        match = re.fullmatch(r'([0-9]+)\s*,\s*([0-9]+)',value)
                        if match is None:
                            raise ValueError('Use inspected,affected whole PLANT counts, or unknown.')
                        inspected,affected = map(int,match.groups())
                        if not 1<=inspected<=1_000_000_000 or not 0<=affected<=inspected:
                            raise ValueError('Inspected must be 1–1000000000 and affected 0–inspected.')
                        value = [inspected,affected]
                elif key=='provenance':
                    value = next((kind for kind in ('observed', 'reported') if scouting_token(draft, value, kind)), None)
                    if value is None:
                        raise ValueError('Choose observed or reported explicitly.')
                draft['history'].append(dict(draft['data']))
                draft['data'][key] = value
                self._check(conn,owner,draft)
            draft.update(revision=uuid.uuid4().hex,updated_at=datetime.now(timezone.utc).isoformat())
            conn.execute('UPDATE scouting_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
                         (draft['revision'],draft['updated_at'],_json(draft),unit_id))
            return _view(draft)

    def cancel(self, owner, unit_id, expected_revision):
        with self.db.transaction() as conn:
            draft = self._read(conn,owner,unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise ValueError('Scouting draft changed or ended; resume it before cancelling.')
            conn.execute('DELETE FROM scouting_intake_drafts WHERE unit_id=?',(unit_id,))
            return dict(status='cancelled')

    def save(self, owner, unit_id, draft_id, expected_revision):
        with self.db.transaction() as conn:
            _scope(conn,owner,unit_id)
            text('draft_id',draft_id)
            text('expected_revision',expected_revision)
            receipt = dict(draft_id=draft_id,revision=expected_revision)
            request_id = 'scouting-intake:'+draft_id
            previous = conn.execute('SELECT payload FROM crop_records WHERE unit_id=? AND request_id=?',(unit_id,request_id)).fetchone()
            if previous:
                record = json.loads(previous[0])
                saved_receipt = record['operation'].get('intake', {})
                if any(saved_receipt.get(key) != value for key,value in receipt.items()):
                    raise ValueError('Saved scouting receipt differs; resume the current draft.')
                return dict(status='saved',record=record,language=saved_receipt.get('language', 'en'))
            draft = self._read(conn,owner,unit_id)
            if draft is None or draft['id']!=draft_id or draft['revision']!=expected_revision:
                raise ValueError('Scouting draft changed or ended; review it before saving.')
            self._check(conn,owner,draft)
            if _next(draft):
                raise ValueError('Complete the scouting questions before saving.')
            receipt['language'] = draft.get('language', 'en')
            record = record_crop_observation(conn,owner,unit_id,draft['data']['cycle_id'],'scouting',_data(draft),request_id,intake=receipt)
            conn.execute('DELETE FROM scouting_intake_drafts WHERE unit_id=?',(unit_id,))
            return dict(status='saved',record=record,language=receipt['language'])
