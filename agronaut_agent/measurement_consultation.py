"""Reviewed raw readings with explicit units and unambiguous observation times."""

import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from agro_model.farm import OBSERVATION_UNITS
from agro_model.validation import number, text

from .crop_records import _json, _scope, record_measurement
from .cycle_lifecycle import ensure_cycle_day
from .measurement_language import (
    TEXT,
    MeasurementError,
    translation_note,
    validate_language,
    words,
)
from .water_log_language import provenance, token

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS measurement_intake_drafts (
    unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
    id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS measurement_intake_receipts (
    id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL UNIQUE REFERENCES field_observations(id) ON DELETE CASCADE,
    payload TEXT NOT NULL)''',
)
FIELDS = ('cycle_id', 'quantity', 'value', 'observed_at', 'source', 'provenance')


def reading_time(value, farm_timezone):
    """Resolve a supplied time; never choose a fold or normalize a missing wall time."""
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?', value):
        raise MeasurementError('timestamp')
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MeasurementError('timestamp') from exc
    if stamp.tzinfo is None:
        zone = ZoneInfo(farm_timezone)
        candidates = {}
        for fold in (0, 1):
            candidate = stamp.replace(tzinfo=zone, fold=fold)
            instant = candidate.astimezone(timezone.utc)
            if instant.astimezone(zone).replace(tzinfo=None) == stamp:
                candidates[instant] = candidate
        if not candidates:
            raise MeasurementError('nonexistent')
        if len(candidates) != 1:
            raise MeasurementError('ambiguous')
        stamp = next(iter(candidates.values()))
    if stamp > datetime.now(timezone.utc):
        raise MeasurementError('future')
    return stamp.isoformat()


def _next(draft):
    return next((key for key in FIELDS if key not in draft['data']), None)


def _view(draft):
    if draft is None:
        return None
    step = _next(draft)
    data = draft['data']
    unit = OBSERVATION_UNITS.get(data.get('quantity'))
    question = words(draft, step or 'ready', unit=unit, timezone=draft['timezone'])
    if step == 'cycle_id':
        question += '\n'+'\n'.join(f"{i}. {c['crop']} · {c['planted_on']} · {c['id']}" for i, c in enumerate(draft['cycles'], 1))
    if step == 'quantity':
        question += '\n'+'\n'.join(f'{i}. {label} · {key} ({OBSERVATION_UNITS[key]})'
            for i, (key, label) in enumerate(zip(OBSERVATION_UNITS, TEXT[draft['language']]['quantities']), 1))
    return dict(draft_id=draft['id'], revision=draft['revision'], unit_id=draft['unit_id'],
        unit_name=draft['unit_name'], timezone=draft['timezone'], language=draft['language'],
        translation_status='original' if draft['language']=='en' else 'draft', cycles=draft['cycles'],
        answers=data, unit=unit, next_field=step, status='collecting' if step else 'ready', question=question)


def render_measurement_intake(view):
    if view['status'] == 'saved':
        return words(view, 'saved', id=view['record']['id'])
    lines = [words(view, 'title')+' — '+view['unit_name']]
    if note := translation_note(view):
        lines.append(note)
    lines.append(view['question'])
    if view['status'] == 'ready':
        for key, label in zip(FIELDS, TEXT[view['language']]['labels']):
            value = view['answers'][key]
            if key == 'cycle_id':
                value = next((f"{c['crop']} · {c['id']}" for c in view['cycles'] if c['id']==value), words(view, 'none'))
            if key == 'value':
                value = f"{value} {view['unit']}"
            lines.append(f'{label}: {value}')
        lines += [view['timezone']+': '+datetime.fromisoformat(view['answers']['observed_at']).astimezone(ZoneInfo(view['timezone'])).isoformat(),
                  words(view, 'quality')]
    lines.append(words(view, 'footer'))
    return '\n'.join(lines)


class MeasurementConsultation:
    def __init__(self, db):
        self.db = db

    def active(self, owner, unit_id):
        return bool(self.db.query('SELECT 1 FROM measurement_intake_drafts d JOIN production_units u ON u.id=d.unit_id '
                                 'JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?', (unit_id, owner)))

    def _read(self, conn, owner, unit_id):
        _scope(conn, owner, unit_id)
        row = conn.execute('SELECT payload FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write(self, conn, draft):
        draft.update(revision=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat())
        conn.execute('UPDATE measurement_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
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
                if language is not None and language != draft['language']:
                    draft['language'] = language
                    self._write(conn, draft)
                return _view(draft)
            if conn.execute('SELECT 1 FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel harvest entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM unit_setup_drafts WHERE owner_id=?', (owner,)).fetchone() or any(
                conn.execute(f'SELECT 1 FROM {table} WHERE unit_id=?', (unit_id,)).fetchone()
                for table in ('planting_intake_drafts', 'scouting_intake_drafts', 'field_intake_drafts', 'water_log_drafts')):
                raise MeasurementError('conflict')
            tz, _ = _scope(conn, owner, unit_id)
            now = datetime.now(timezone.utc).isoformat()
            draft = dict(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, unit_id=unit_id,
                unit_name=conn.execute('SELECT name FROM production_units WHERE id=?', (unit_id,)).fetchone()[0],
                timezone=tz, language=language or 'en', created_at=now, updated_at=now, data={}, history=[],
                cycles=[dict(c) for c in conn.execute('SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id', (unit_id,))])
            conn.execute('INSERT INTO measurement_intake_drafts VALUES(?,?,?,?,?,?)',
                         (unit_id, draft['id'], draft['revision'], now, now, _json(draft)))
            return _view(draft)

    def _check(self, conn, owner, draft, *, check_time=True):
        data = draft['data']
        tz, cycle = _scope(conn, owner, draft['unit_id'], data.get('cycle_id'))
        if tz != draft['timezone'] or (cycle and dict(cycle) not in draft['cycles']):
            raise MeasurementError('context')
        if check_time and 'observed_at' in data:
            self._time(conn, draft, data['observed_at'])

    def _time(self, conn, draft, value):
        stamp = reading_time(value, draft['timezone'])
        # Point measurements may predate planting, but cannot follow a closed cycle.
        day = datetime.fromisoformat(stamp).astimezone(ZoneInfo(draft['timezone'])).date().isoformat()
        ensure_cycle_day(conn, draft['unit_id'], draft['data'].get('cycle_id'), day)
        return stamp

    def answer(self, owner, unit_id, answer, expected_revision, *, back=False):
        if type(back) is not bool:
            raise ValueError('back must be a boolean')
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise MeasurementError('stale')
            self._check(conn, owner, draft, check_time=not back)
            if back:
                if draft['history']:
                    draft['data'] = draft['history'].pop()
            else:
                key = _next(draft)
                if key is None:
                    raise MeasurementError('incomplete')
                value = text('answer', answer).strip()
                if len(value) > 12000:
                    raise ValueError('Keep each answer within 12000 characters.')
                data = dict(draft['data'])
                if key in ('cycle_id', 'quantity'):
                    if key == 'cycle_id' and token(draft, value, 'none'):
                        value = None
                    else:
                        choices = [c['id'] for c in draft['cycles']] if key=='cycle_id' else list(OBSERVATION_UNITS)
                        if value.isascii() and value.isdigit() and len(value)<10 and 1<=int(value)<=len(choices):
                            value = choices[int(value)-1]
                        if value not in choices:
                            raise ValueError(words(draft, key))
                        if key == 'cycle_id':
                            _, cycle = _scope(conn, owner, unit_id, value)
                            if dict(cycle) not in draft['cycles']:
                                raise MeasurementError('context')
                elif key == 'value':
                    try:
                        if not re.fullmatch(r'-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)', value):
                            raise ValueError('Use plain decimal digits.')
                        decimal = Decimal(value)
                        value = float(value)
                        number('value', value, low=None if data['quantity']=='air_temperature' else 0,
                               high=1 if data['quantity']=='soil_water_content' else None)
                        if decimal != 0 and value == 0:
                            raise ValueError('Value is too small to retain.')
                    except ValueError as exc:
                        raise MeasurementError('invalid') from exc
                elif key == 'observed_at':
                    data['entered_time'] = value
                    value = self._time(conn, draft, value)
                elif key == 'provenance':
                    value = provenance(draft, value)
                    if value not in ('measured', 'reported', 'estimated'):
                        raise ValueError(words(draft, 'provenance'))
                draft['history'].append(dict(draft['data']))
                data[key] = value
                draft['data'] = data
            self._write(conn, draft)
            return _view(draft)

    def cancel(self, owner, unit_id, expected_revision):
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise MeasurementError('stale')
            conn.execute('DELETE FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='cancelled')

    def save(self, owner, unit_id, draft_id, expected_revision):
        text('draft_id', draft_id)
        text('expected_revision', expected_revision)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            row = conn.execute('SELECT payload FROM measurement_intake_receipts WHERE id=? AND unit_id=?', (draft_id, unit_id)).fetchone()
            if row:
                receipt = json.loads(row[0])
                if receipt['revision'] != expected_revision:
                    raise MeasurementError('stale')
                return receipt['result']
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['id'] != draft_id or draft['revision'] != expected_revision:
                raise MeasurementError('stale')
            self._check(conn, owner, draft)
            if _next(draft):
                raise MeasurementError('incomplete')
            data = {key: draft['data'][key] for key in FIELDS if key != 'cycle_id'}
            data['unit'] = OBSERVATION_UNITS[data['quantity']]
            record = record_measurement(conn, owner, unit_id, data, cycle_id=draft['data']['cycle_id'], request_id='measurement-intake:'+draft_id)
            result = dict(status='saved', language=draft['language'], record=record)
            receipt = dict(draft_id=draft_id, revision=expected_revision, timezone=draft['timezone'],
                           answers=draft['data'], result=result)
            conn.execute('INSERT INTO measurement_intake_receipts VALUES(?,?,?,?)', (draft_id, unit_id, record['id'], _json(receipt)))
            conn.execute('DELETE FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,))
            return result
