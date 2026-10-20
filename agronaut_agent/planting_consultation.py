"""Resumable actual-planting intake with an atomic reviewed crop-cycle save."""

import json
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import text

from .crop_records import _json, _scope, create_planting
from .field_io import _date
from .planting_language import (
    TEXT,
    PlantingError,
    new_cycle_token,
    planting_text,
    translation_note,
    validate_language,
)

SCHEMA='''CREATE TABLE IF NOT EXISTS planting_intake_drafts (
 unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
 id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, payload TEXT NOT NULL)'''


def _next(draft):
    for key in ('crop','planted_on','source'):
        if key not in draft['data']:
            return key
    if draft['matches'] and not draft['data'].get('duplicate_confirmed'):
        return 'duplicate'
    return None


def _view(draft):
    if draft is None:
        return None
    step=_next(draft)
    return dict(draft_id=draft['id'],revision=draft['revision'],unit_id=draft['unit_id'],unit_name=draft['unit_name'],
                language=draft['language'],translation_status='original' if draft['language']=='en' else 'draft',
                timezone=draft['timezone'],answers=draft['data'],matching_cycles=draft['matches'],
                status='collecting' if step else 'ready',next_field=step,
                question=planting_text(draft,step or 'ready',timezone=draft['timezone']))


def render_planting_intake(view):
    if view is None:
        return 'No planting draft is active for this unit. Start with /plant.'
    if view['status']=='saved':
        return planting_text(view,'saved',id=view['cycle']['id'])
    lines=[planting_text(view,'title')+' — '+view['unit_name']]
    if note:=translation_note(view):
        lines.append(note)
    lines.append(view['question'])
    if view['status']=='ready':
        values=[view['answers']['crop'],view['answers']['planted_on']+' ('+view['timezone']+')',view['answers']['source']]
        lines += [label+': '+value for label,value in zip(TEXT[view['language']]['labels'],values)]
    if view['matching_cycles']:
        lines.append(planting_text(view,'matches'))
        lines += [f"{row['crop']} · {row['planted_on']} · {row['id']}" for row in view['matching_cycles']]
    lines.append(planting_text(view,'footer'))
    return '\n'.join(lines)


def _matches(conn,draft):
    data=draft['data']
    if 'crop' not in data or 'planted_on' not in data:
        return []
    return [dict(row) for row in conn.execute('SELECT * FROM production_cycles WHERE unit_id=? AND planted_on=? ORDER BY id',
            (draft['unit_id'],data['planted_on'])) if row['crop'].casefold()==data['crop'].casefold()]


class PlantingConsultation:
    def __init__(self,db):
        self.db=db

    def active(self,owner,unit_id):
        return bool(self.db.query('SELECT 1 FROM planting_intake_drafts d JOIN production_units u ON u.id=d.unit_id '
                                 'JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?',(unit_id,owner)))

    def _read(self,conn,owner,unit_id):
        _scope(conn,owner,unit_id)
        row=conn.execute('SELECT payload FROM planting_intake_drafts WHERE unit_id=?',(unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write(self,conn,draft):
        draft.update(revision=uuid.uuid4().hex,updated_at=datetime.now(timezone.utc).isoformat())
        conn.execute('UPDATE planting_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
                     (draft['revision'],draft['updated_at'],_json(draft),draft['unit_id']))

    def get(self,owner,unit_id):
        with self.db.transaction() as conn:
            return _view(self._read(conn,owner,unit_id))

    def start(self,owner,unit_id,language=None):
        if language is not None:
            validate_language(language)
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM task_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel task entry for this unit before another intake.')
            draft=self._read(conn,owner,unit_id)
            if draft:
                matches=_matches(conn,draft)
                changed=matches!=draft['matches']
                if changed:
                    draft['matches']=matches
                    for data in [draft['data'],*draft['history']]:
                        data.pop('duplicate_confirmed',None)
                if language is not None and language!=draft['language']:
                    draft['language']=language
                    changed=True
                if changed:
                    self._write(conn,draft)
                return _view(draft)
            if conn.execute('SELECT 1 FROM harvest_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel harvest entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM measurement_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel measurement entry for this unit before another intake.')
            if conn.execute('SELECT 1 FROM water_log_drafts WHERE unit_id=?', (unit_id,)).fetchone():
                raise ValueError('Finish or cancel water logging for this unit before another intake.')
            if conn.execute('SELECT 1 FROM unit_setup_drafts WHERE owner_id=?',(owner,)).fetchone():
                raise PlantingError('setup')
            if any(conn.execute(f'SELECT 1 FROM {table} WHERE unit_id=?',(unit_id,)).fetchone()
                   for table in ('scouting_intake_drafts','field_intake_drafts')):
                raise PlantingError('conflict')
            tz,_=_scope(conn,owner,unit_id)
            now=datetime.now(timezone.utc).isoformat()
            draft=dict(id=uuid.uuid4().hex,revision=uuid.uuid4().hex,unit_id=unit_id,language=language or 'en',timezone=tz,
                unit_name=conn.execute('SELECT name FROM production_units WHERE id=?',(unit_id,)).fetchone()[0],
                created_at=now,updated_at=now,data={},history=[],matches=[])
            conn.execute('INSERT INTO planting_intake_drafts VALUES(?,?,?,?,?,?)',
                         (unit_id,draft['id'],draft['revision'],now,now,_json(draft)))
            return _view(draft)

    def _check(self,conn,owner,draft):
        tz,_=_scope(conn,owner,draft['unit_id'])
        if tz!=draft['timezone']:
            raise PlantingError('timezone')
        if _matches(conn,draft)!=draft['matches']:
            raise PlantingError('changed')
        return tz

    def answer(self,owner,unit_id,answer,expected_revision,*,back=False):
        if type(back) is not bool:
            raise ValueError('back must be a boolean')
        with self.db.transaction() as conn:
            draft=self._read(conn,owner,unit_id)
            if draft is None or draft['revision']!=expected_revision:
                raise PlantingError('stale')
            tz=self._check(conn,owner,draft)
            if back:
                if draft['history']:
                    draft['data']=draft['history'].pop()
            else:
                key=_next(draft)
                if key is None:
                    raise PlantingError('already_ready')
                value=text('answer',answer).strip()
                if len(value)>(200 if key=='crop' else 12000):
                    raise PlantingError('long_answer')
                if key=='planted_on':
                    try:
                        if _date(value)>datetime.now(ZoneInfo(tz)).date():
                            raise ValueError('future planting')
                    except ValueError as exc:
                        raise PlantingError('date_error') from exc
                if key=='duplicate':
                    if not new_cycle_token(draft,value):
                        raise PlantingError('duplicate_answer')
                    key,value='duplicate_confirmed',True
                draft['history'].append(dict(draft['data']))
                draft['data'][key]=value
            draft['matches']=_matches(conn,draft)
            self._write(conn,draft)
            return _view(draft)

    def cancel(self,owner,unit_id,expected_revision):
        with self.db.transaction() as conn:
            draft=self._read(conn,owner,unit_id)
            if draft is None or draft['revision']!=expected_revision:
                raise PlantingError('stale')
            conn.execute('DELETE FROM planting_intake_drafts WHERE unit_id=?',(unit_id,))
            return dict(status='cancelled')

    def save(self,owner,unit_id,draft_id,expected_revision):
        with self.db.transaction() as conn:
            _scope(conn,owner,unit_id)
            text('draft_id',draft_id)
            text('expected_revision',expected_revision)
            request_id='planting-intake:'+draft_id
            previous=conn.execute('SELECT payload FROM crop_records WHERE unit_id=? AND request_id=?',(unit_id,request_id)).fetchone()
            if previous:
                record=json.loads(previous[0])
                receipt=record['operation'].get('intake',{})
                if receipt.get('draft_id')!=draft_id or receipt.get('revision')!=expected_revision:
                    raise PlantingError('stale')
                cycle=dict(id=record['cycle_id'],unit_id=unit_id,crop=record['data']['crop'],planted_on=record['data']['planted_on'])
                return dict(status='saved',language=receipt['language'],cycle=cycle,record=record)
            draft=self._read(conn,owner,unit_id)
            if draft is None or draft['id']!=draft_id or draft['revision']!=expected_revision:
                raise PlantingError('stale')
            self._check(conn,owner,draft)
            if _next(draft):
                raise PlantingError('incomplete')
            result=create_planting(conn,owner,unit_id,**{key:draft['data'][key] for key in ('crop','planted_on','source')},
                request_id=request_id,intake=dict(draft_id=draft_id,revision=expected_revision,language=draft['language'],matching_cycle_ids=[r['id'] for r in draft['matches']]))
            conn.execute('DELETE FROM planting_intake_drafts WHERE unit_id=?',(unit_id,))
            return dict(status='saved',language=draft['language'],**result)
