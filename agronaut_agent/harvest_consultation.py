"""Resumable actual harvest entry; one reviewed save and its receipt are atomic."""

import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from agro_model.validation import number, text

from .crop_records import _json, _scope, record_harvest
from .cycle_lifecycle import ensure_cycle_day
from .field_io import _date
from .harvest_language import (
    BASIS,
    BASIS_LABELS,
    PROVENANCE_LABELS,
    TEXT,
    HarvestError,
    basis,
    translation_note,
    validate_language,
    words,
)
from .water_log_language import provenance

SCHEMA = '''CREATE TABLE IF NOT EXISTS harvest_intake_drafts (
 unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
 id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, payload TEXT NOT NULL)'''
FIELDS = ('cycle_id', 'batch', 'day', 'product', 'unit', 'quantity', 'basis', 'grade', 'source', 'provenance')


def _next(draft):
    return next((key for key in FIELDS if key not in draft['data']), None)


def _view(draft):
    if draft is None:
        return None
    step = _next(draft)
    question = (TEXT[draft['language']]['questions'][FIELDS.index(step)].format(
        timezone=draft['timezone'], unit=draft['data'].get('unit')) if step else words(draft, 'ready'))
    if step == 'cycle_id':
        question += '\n'+'\n'.join(f"{i}. {c['crop']} · {c['planted_on']} · {c['id']}" for i, c in enumerate(draft['cycles'], 1))
    return dict(draft_id=draft['id'], revision=draft['revision'], unit_id=draft['unit_id'],
        unit_name=draft['unit_name'], timezone=draft['timezone'], language=draft['language'],
        translation_status='original' if draft['language']=='en' else 'draft', cycles=draft['cycles'],
        answers=draft['data'], status='collecting' if step else 'ready', next_field=step, question=question)


def render_harvest_intake(view):
    if view is None:
        return 'No harvest draft is active for this unit. Start with /harvest.'
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
                cycle = next(c for c in view['cycles'] if c['id'] == value)
                value = cycle['crop']+' · '+value
            elif key == 'day':
                value += ' ('+view['timezone']+')'
            elif key == 'basis':
                value = BASIS_LABELS[view['language']][BASIS.index(value)]
            elif key == 'provenance':
                value = PROVENANCE_LABELS[view['language']][('measured', 'reported', 'estimated').index(value)]
            lines.append(f'{label}: {value}')
    lines.append(words(view, 'footer'))
    return '\n'.join(lines)


class HarvestConsultation:
    def __init__(self, db):
        self.db = db

    def active(self, owner, unit_id):
        return bool(self.db.query('SELECT 1 FROM harvest_intake_drafts d JOIN production_units u ON u.id=d.unit_id '
            'JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?', (unit_id, owner)))

    def _read(self, conn, owner, unit_id):
        _scope(conn, owner, unit_id)
        row = conn.execute('SELECT payload FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write(self, conn, draft):
        draft.update(revision=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat())
        conn.execute('UPDATE harvest_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
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
            if conn.execute('SELECT 1 FROM unit_setup_drafts WHERE owner_id=?', (owner,)).fetchone():
                raise HarvestError('conflict')
            if any(conn.execute(f'SELECT 1 FROM {table} WHERE unit_id=?', (unit_id,)).fetchone() for table in (
                'measurement_intake_drafts', 'planting_intake_drafts', 'scouting_intake_drafts', 'water_log_drafts', 'field_intake_drafts')):
                raise HarvestError('conflict')
            tz, _ = _scope(conn, owner, unit_id)
            cycles = [dict(c) for c in conn.execute('SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id', (unit_id,))]
            if not cycles:
                raise HarvestError('no_cycles')
            now = datetime.now(timezone.utc).isoformat()
            draft = dict(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, unit_id=unit_id,
                unit_name=conn.execute('SELECT name FROM production_units WHERE id=?', (unit_id,)).fetchone()[0],
                timezone=tz, language=language or 'en', cycles=cycles, data={}, history=[], created_at=now, updated_at=now)
            conn.execute('INSERT INTO harvest_intake_drafts VALUES(?,?,?,?,?,?)',
                (unit_id, draft['id'], draft['revision'], now, now, _json(draft)))
            return _view(draft)

    def _check(self, conn, owner, draft):
        tz, cycle = _scope(conn, owner, draft['unit_id'], draft['data'].get('cycle_id'))
        if tz != draft['timezone']:
            raise HarvestError('context')
        if cycle:
            original = next(c for c in draft['cycles'] if c['id'] == cycle['id'])
            if any(cycle[k] != original[k] for k in ('crop', 'planted_on')):
                raise HarvestError('context')
            if 'day' in draft['data']:
                self._day(conn, draft, cycle, draft['data']['day'])
        return cycle

    def _day(self, conn, draft, cycle, value):
        try:
            if not _date(cycle['planted_on']) <= _date(value) <= datetime.now(ZoneInfo(draft['timezone'])).date():
                raise ValueError('date outside cycle')
            ensure_cycle_day(conn, draft['unit_id'], cycle['id'], value)
        except ValueError as exc:
            raise HarvestError('date_error') from exc

    def _batch(self, conn, draft, value):
        if conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND batch=? AND kind='harvest'",
            (draft['unit_id'], draft['data']['cycle_id'], value)).fetchone():
            raise HarvestError('duplicate')

    def answer(self, owner, unit_id, answer, expected_revision, *, back=False):
        if type(back) is not bool:
            raise ValueError('back must be a boolean')
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise HarvestError('stale')
            # Back remains usable after a new closure invalidates an entered day.
            if back:
                if draft['history']:
                    draft['data'] = draft['history'].pop()
            else:
                cycle = self._check(conn, owner, draft)
                key = _next(draft)
                if key is None:
                    raise HarvestError('ready_error')
                value = text('answer', answer).strip()
                if len(value) > (2000 if key == 'source' else 200):
                    raise HarvestError('long')
                if key == 'cycle_id':
                    if re.fullmatch(r'[0-9]{1,9}', value) and 1 <= int(value) <= len(draft['cycles']):
                        value = draft['cycles'][int(value)-1]['id']
                    if value not in {c['id'] for c in draft['cycles']}:
                        raise HarvestError('cycle')
                elif key == 'batch':
                    self._batch(conn, draft, value)
                elif key == 'day':
                    self._day(conn, draft, cycle, value)
                elif key == 'unit' and value not in ('kg', 'g', 't', 'count'):
                    raise HarvestError('unit')
                elif key == 'quantity':
                    try:
                        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
                            raise ValueError('plain decimal required')
                        decimal = Decimal(value)
                        if not 0 < decimal <= Decimal('1000000000000'):
                            raise ValueError('quantity outside range')
                        if draft['data']['unit'] == 'count' and decimal != decimal.to_integral_value():
                            raise ValueError('whole count required')
                        value = number('quantity', float(value), high=1e12, positive=True)
                    except ValueError as exc:
                        raise HarvestError('quantity') from exc
                elif key == 'basis':
                    value = basis(draft, value)
                elif key == 'provenance':
                    value = provenance(draft, value)
                    if value not in ('measured', 'reported', 'estimated'):
                        raise HarvestError('provenance')
                draft['history'].append(dict(draft['data']))
                draft['data'][key] = value
                self._check(conn, owner, draft)
            self._write(conn, draft)
            return _view(draft)

    def cancel(self, owner, unit_id, expected_revision):
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise HarvestError('stale')
            conn.execute('DELETE FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='cancelled')

    def save(self, owner, unit_id, draft_id, expected_revision):
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            text('draft_id', draft_id)
            text('expected_revision', expected_revision)
            request_id = 'harvest-intake:'+draft_id
            previous = conn.execute('SELECT payload FROM crop_records WHERE unit_id=? AND request_id=?', (unit_id, request_id)).fetchone()
            if previous:
                record = json.loads(previous[0])
                receipt = record['operation'].get('intake', {})
                if receipt.get('draft_id') != draft_id or receipt.get('revision') != expected_revision:
                    raise HarvestError('stale')
                return dict(status='saved', record=record, language=receipt['language'])
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['id'] != draft_id or draft['revision'] != expected_revision:
                raise HarvestError('stale')
            self._check(conn, owner, draft)
            if _next(draft):
                raise HarvestError('incomplete')
            self._batch(conn, draft, draft['data']['batch'])
            receipt = dict(draft_id=draft_id, revision=expected_revision, language=draft['language'])
            record = record_harvest(conn, owner, unit_id, draft['data']['cycle_id'],
                {k: draft['data'][k] for k in FIELDS if k != 'cycle_id'}, request_id=request_id, intake=receipt)
            conn.execute('DELETE FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='saved', record=record, language=draft['language'])
