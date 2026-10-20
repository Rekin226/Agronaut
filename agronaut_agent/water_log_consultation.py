"""Reviewed whole-day water totals, with atomic corrections and durable drafts."""

import json
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import number, text

from .cycle_lifecycle import ensure_cycle_day
from .field_io import _date
from .field_workflow import _json, _scope, record_water_day
from .water_log_language import (
    TEXT,
    WaterLogError,
    provenance,
    token,
    translation_note,
    validate_language,
    words,
)

SCHEMA = '''CREATE TABLE IF NOT EXISTS water_log_drafts (
 unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
 id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, payload TEXT NOT NULL)'''
FIELDS = ('cycle_id', 'day', 'rainfall_mm', 'net_irrigation_mm', 'runoff_mm', 'source', 'provenance')


def _next(draft):
    for key in FIELDS:
        if key not in draft['data']:
            return key
    if draft['prior']:
        for key in ('correction', 'reason'):
            if key not in draft['data']:
                return key
    return None


def _view(draft):
    if draft is None:
        return None
    step = _next(draft)
    question = words(draft, step or 'ready', timezone=draft['timezone'])
    if step == 'cycle_id':
        question += '\n'+'\n'.join(f"{i}. {c['crop']} · {c['planted_on']} · {c['id']}" for i, c in enumerate(draft['cycles'], 1))
    return dict(draft_id=draft['id'], revision=draft['revision'], unit_id=draft['unit_id'],
                unit_name=draft['unit_name'], timezone=draft['timezone'], language=draft['language'],
                translation_status='original' if draft['language']=='en' else 'draft',
                cycles=draft['cycles'], answers=draft['data'], previous_record=draft['prior'],
                status='collecting' if step else 'ready', next_field=step, question=question)


def render_water_log(view):
    if view['status'] == 'saved':
        return words(view, 'saved', id=view['record']['id'])
    lines = [words(view, 'title')+' — '+view['unit_name']]
    if note := translation_note(view):
        lines.append(note)
    lines.append(view['question'])
    if view['status'] == 'ready':
        for key, label in zip((*FIELDS, 'reason'), TEXT[view['language']]['labels']):
            if key in view['answers']:
                value = view['answers'][key]
                if key == 'cycle_id':
                    value = next((f"{c['crop']} · {c['id']}" for c in view['cycles'] if c['id']==value), words(view, 'none'))
                lines.append(f'{label}: {value}')
        lines.append(words(view, 'quality'))
    if view['previous_record']:
        lines += [words(view, 'prior'), _json(view['previous_record']['data']), view['previous_record']['id']]
    lines.append(words(view, 'footer'))
    return '\n'.join(lines)


def _latest(conn, draft):
    data = draft['data']
    if 'day' not in data:
        return None
    row = conn.execute('SELECT d.payload FROM field_water_days d WHERE d.unit_id=? AND d.cycle_id IS ? AND d.day=? '
                       'AND NOT EXISTS (SELECT 1 FROM field_water_days n WHERE n.supersedes_id=d.id)',
                       (draft['unit_id'], data['cycle_id'], data['day'])).fetchone()
    return json.loads(row[0]) if row else None


class WaterLogConsultation:
    def __init__(self, db):
        self.db = db

    def active(self, owner, unit_id):
        return bool(self.db.query('SELECT 1 FROM water_log_drafts d JOIN production_units u ON u.id=d.unit_id '
                                 'JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?', (unit_id, owner)))

    def _read(self, conn, owner, unit_id):
        _scope(conn, owner, unit_id)
        row = conn.execute('SELECT payload FROM water_log_drafts WHERE unit_id=?', (unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write(self, conn, draft):
        draft.update(revision=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat())
        conn.execute('UPDATE water_log_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
                     (draft['revision'], draft['updated_at'], _json(draft), draft['unit_id']))

    def get(self, owner, unit_id):
        with self.db.transaction() as conn:
            return _view(self._read(conn, owner, unit_id))

    def start(self, owner, unit_id, language=None):
        if language is not None:
            validate_language(language)
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM task_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel task entry for this unit before another intake.')
            draft = self._read(conn, owner, unit_id)
            if draft:
                prior = _latest(conn, draft)
                changed = prior != draft['prior']
                if changed:
                    draft['prior'] = prior
                    for data in [draft['data'], *draft['history']]:
                        data.pop('correction', None)
                        data.pop('reason', None)
                if language is not None and language != draft['language']:
                    draft['language'] = language
                    changed = True
                if changed:
                    self._write(conn, draft)
                return _view(draft)
            if conn.execute('SELECT 1 FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel harvest entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel measurement entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM unit_setup_drafts WHERE owner_id=?', (owner,)).fetchone() or any(
                conn.execute(f'SELECT 1 FROM {table} WHERE unit_id=?', (unit_id,)).fetchone()
                for table in ('planting_intake_drafts', 'scouting_intake_drafts', 'field_intake_drafts')):
                raise WaterLogError('conflict')
            tz, _ = _scope(conn, owner, unit_id)
            now = datetime.now(timezone.utc).isoformat()
            draft = dict(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, unit_id=unit_id,
                unit_name=conn.execute('SELECT name FROM production_units WHERE id=?', (unit_id,)).fetchone()[0],
                timezone=tz, language=language or 'en', created_at=now, updated_at=now, data={}, history=[], prior=None,
                cycles=[dict(c) for c in conn.execute('SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id', (unit_id,))])
            conn.execute('INSERT INTO water_log_drafts VALUES(?,?,?,?,?,?)',
                         (unit_id, draft['id'], draft['revision'], now, now, _json(draft)))
            return _view(draft)

    def _check(self, conn, owner, draft, *, check_day=True):
        tz, cycle = _scope(conn, owner, draft['unit_id'], draft['data'].get('cycle_id'))
        if tz != draft['timezone'] or (cycle and dict(cycle) not in draft['cycles']):
            raise WaterLogError('context')
        if _latest(conn, draft) != draft['prior']:
            raise WaterLogError('changed')
        if check_day and 'day' in draft['data']:
            self._day(conn, draft, draft['data']['day'], cycle)
        return cycle

    def _day(self, conn, draft, value, cycle):
        try:
            day = _date(value)
            if day >= datetime.now(ZoneInfo(draft['timezone'])).date() or (cycle and value < cycle['planted_on']):
                raise ValueError('outside completed cycle days')
            ensure_cycle_day(conn, draft['unit_id'], cycle['id'] if cycle else None, value)
        except ValueError as exc:
            raise WaterLogError('date_error') from exc

    def answer(self, owner, unit_id, answer, expected_revision, *, back=False):
        if type(back) is not bool:
            raise ValueError('back must be a boolean')
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise WaterLogError('stale')
            cycle = self._check(conn, owner, draft, check_day=not back)
            if back:
                if draft['history']:
                    draft['data'] = draft['history'].pop()
            else:
                key = _next(draft)
                if key is None:
                    raise WaterLogError('incomplete')
                value = text('answer', answer).strip()
                if len(value) > 12000:
                    raise ValueError('Keep each answer within 12000 characters.')
                if key == 'cycle_id':
                    if token(draft, value, 'none'):
                        value = None
                    else:
                        choices = draft['cycles']
                        if value.isascii() and value.isdigit() and len(value)<10 and 1<=int(value)<=len(choices):
                            value = choices[int(value)-1]['id']
                        if value not in {c['id'] for c in choices}:
                            raise ValueError(words(draft, 'cycle_id'))
                        # Revalidate the selected cycle immediately, before advancing.
                        _, current = _scope(conn, owner, unit_id, value)
                        if dict(current) not in choices:
                            raise WaterLogError('context')
                elif key == 'day':
                    self._day(conn, draft, value, cycle)
                elif key.endswith('_mm'):
                    try:
                        if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)', value):
                            raise ValueError('Use plain decimal millimetres without separators.')
                        value = float(value)
                        number(key, value, 0)
                        if key == 'runoff_mm':
                            number(key, value, 0, draft['data']['rainfall_mm'])
                    except ValueError as exc:
                        raise WaterLogError('invalid') from exc
                elif key == 'provenance':
                    value = provenance(draft, value)
                    if value not in ('measured', 'reported', 'estimated'):
                        raise ValueError(words(draft, 'provenance'))
                elif key == 'correction':
                    if not token(draft, value, 'replace'):
                        raise ValueError(words(draft, 'correction'))
                    value = True
                draft['history'].append(dict(draft['data']))
                draft['data'][key] = value
            draft['prior'] = _latest(conn, draft)
            self._write(conn, draft)
            return _view(draft)

    def cancel(self, owner, unit_id, expected_revision):
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise WaterLogError('stale')
            conn.execute('DELETE FROM water_log_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='cancelled')

    def save(self, owner, unit_id, draft_id, expected_revision):
        text('draft_id', draft_id)
        text('expected_revision', expected_revision)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            request_id = 'water-log:'+draft_id
            row = conn.execute('SELECT payload FROM field_water_days WHERE unit_id=? AND request_id=?', (unit_id, request_id)).fetchone()
            if row:
                record = json.loads(row[0])
                receipt = record['operation'].get('intake', {})
                if receipt.get('draft_id') != draft_id or receipt.get('revision') != expected_revision:
                    raise WaterLogError('stale')
                return dict(status='saved', language=receipt['language'], record=record)
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['id'] != draft_id or draft['revision'] != expected_revision:
                raise WaterLogError('stale')
            self._check(conn, owner, draft)
            if _next(draft):
                raise WaterLogError('incomplete')
            data = {key: draft['data'][key] for key in FIELDS if key != 'cycle_id'}
            record = record_water_day(conn, owner, unit_id, dict(data, quality='unverified'),
                cycle_id=draft['data']['cycle_id'], supersedes_id=draft['prior']['id'] if draft['prior'] else None,
                request_id=request_id, intake=dict(draft_id=draft_id, revision=expected_revision,
                    language=draft['language'], reason=draft['data'].get('reason')))
            conn.execute('DELETE FROM water_log_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='saved', language=draft['language'], record=record)
