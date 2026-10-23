"""Guided operator work plans and resolutions with atomic, revision-bound saves."""
import json
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.validation import choice, text

from . import task_triggers
from .crop_calendar import ACTIVITIES, completion_evidence, task_plan, task_window
from .crop_records import _json, _latest, _read, _scope, plan_task, resolve_task
from .cycle_lifecycle import lifecycle
from .field_io import _date
from .task_language import (
    LABELS,
    LANGUAGES,
    question,
    translation_note,
    validate_language,
    words,
)

SCHEMA = '''CREATE TABLE IF NOT EXISTS task_intake_drafts (
 unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
 id TEXT NOT NULL UNIQUE, revision TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, payload TEXT NOT NULL)'''
OTHER_DRAFTS = ('harvest_intake_drafts', 'measurement_intake_drafts', 'planting_intake_drafts',
                'scouting_intake_drafts', 'water_log_drafts', 'field_intake_drafts')
CHOICE_PAGE_SIZE = 5


def _task_candidates(conn, draft):
    cycles = {cycle['id']: cycle for cycle in draft['cycles']}
    rows = conn.execute("SELECT r.payload FROM crop_records r WHERE r.unit_id=? AND r.kind='calendar' "
        "AND NOT EXISTS (SELECT 1 FROM crop_records newer WHERE newer.supersedes_id=r.id)", (draft['unit_id'],))
    items = []
    for row in rows:
        record = json.loads(row[0])
        data = record['data']
        if record['cycle_id'] not in cycles or (draft['data']['action'] == 'reopen' and data['status'] == 'planned'):
            continue
        cycle = cycles[record['cycle_id']]
        items.append(dict(id=record['id'], cycle_id=cycle['id'], crop=cycle['crop'], planted_on=cycle['planted_on'],
            **{key: data[key] for key in ('entry', 'title', 'status', 'activity', 'location')}))
    items.sort(key=lambda item: ({'planned': 0, 'completed': 1, 'cancelled': 2}[item['status']],
        item['planted_on'], item['entry'], item['id']))
    return items


def _prepare_choices(conn, draft):
    if draft.get('intake_version', 1) < 3 or _next(draft) != 'trigger':
        draft.pop('source_browser', None)
    elif 'source_browser' not in draft:
        items = task_triggers.candidates(conn, draft['unit_id'], draft['data']['cycle_id'])
        # Retain the displayed ordering and review revisions until explicit refresh.
        items.sort(key=lambda item: (item['when'], item['kind'], item['id']), reverse=True)
        draft['source_browser'] = dict(items=items, page=1)
    if draft.get('intake_version', 1) < 4 or _next(draft) != 'record_id':
        draft.pop('task_browser', None)
    elif 'task_browser' not in draft:
        draft['task_browser'] = dict(items=_task_candidates(conn, draft), page=1)
    if draft.get('intake_version', 1) < 5 or _next(draft) != 'evidence':
        draft.pop('evidence_browser', None)
    elif 'evidence_browser' not in draft:
        data = draft['data']
        items = []
        for candidate in task_triggers.candidates(conn, draft['unit_id'], data['cycle_id']):
            if candidate['kind'] not in ('crop_record', 'water_day'):
                continue
            reference = {key: candidate[key] for key in ('kind', 'id')}
            try:
                completion_evidence(conn, draft['unit_id'], data['cycle_id'], data['task_snapshot']['data']['activity'],
                    data['completed_on'], reference)
            except ValueError:
                continue
            items.append(candidate)
        items.sort(key=lambda item: (item['when'], item['kind'], item['id']), reverse=True)
        draft['evidence_browser'] = dict(items=items, page=1)


def _choice_page(draft, browser_key):
    browser = draft.get(browser_key)
    if browser is None:
        return None
    offset = (browser['page']-1)*CHOICE_PAGE_SIZE
    return dict(page=browser['page'], pages=max(1, (len(browser['items'])+CHOICE_PAGE_SIZE-1)//CHOICE_PAGE_SIZE),
        total=len(browser['items']), items=[dict(item, number=offset+i) for i, item in
            enumerate(browser['items'][offset:offset+CHOICE_PAGE_SIZE], 1)])


def _short(value, length):
    value = ' '.join(str(value).encode('utf-8', errors='replace').decode('utf-8').split())
    return value if len(value) <= length else value[:length-1]+'…'


def _task_prompt(draft, page):
    lines = [words(draft, 'task_choose'), words(draft, 'task_page', page=page['page'], pages=page['pages'], total=page['total'])]
    if not page['items']:
        lines.append(words(draft, 'no_tasks'))
    for item in page['items']:
        parts = [_short(item['title'], 48), item['status'], item['activity'],
            _short(item['crop'], 32)+' · '+item['planted_on'], _short(item['entry'], 24), _short(item['location'], 32)]
        lines.append(f"{item['number']}. "+' · '.join(parts))
    lines.append(words(draft, 'task_navigation'))
    return '\n'.join(lines)


def _record_prompt(draft, page, kind):
    lines = [words(draft, kind+'_page', page=page['page'], pages=page['pages'], total=page['total'])]
    if not page['items']:
        lines.append(words(draft, 'no_sources' if kind == 'source' else 'no_evidence'))
    for item in page['items']:
        details = ' · '.join(str(item[key]) for key in ('when', 'label', 'provenance', 'quality', 'summary') if item.get(key))
        # One compact line per option, including when user text contains line breaks.
        lines.append(f"{item['number']}. {_short(details, 220)}")
    lines.append(words(draft, kind+'_navigation'))
    return '\n'.join(lines)


def _fields(draft):
    data = draft['data']
    if data.get('action', 'plan') == 'plan':
        dates = ('start', 'end') if data.get('basis', 'date') == 'date' else ('start_offset_days', 'end_offset_days')
        trigger = ('trigger', *(('trigger_reason',) if data.get('trigger') not in (None, 'none') else ())) if draft.get('intake_version', 1) >= 2 else ()
        return ('action', 'cycle_id', 'entry', 'title', 'activity', 'basis', *dates, 'location', 'priority', 'notes', *trigger, 'source')
    return ('action', 'record_id', *(('completed_on', 'evidence') if data['action'] == 'completed' else ()), 'reason', 'source')


def _next(draft):
    return next((key for key in _fields(draft) if key not in draft['data']), None)


def _cycle(conn, unit_id, cycle):
    return dict(cycle, lifecycle=lifecycle(conn, unit_id, cycle['id']))


def _plan(draft):
    data = draft['data']
    due = {key: data[key] for key in ('start', 'end') if key in data} if data['basis'] == 'date' else {
        key: data[key] for key in ('start_offset_days', 'end_offset_days') if key in data}
    plan = dict(due=dict(basis=data['basis'], **due),
        **{key: data[key] for key in ('entry', 'title', 'activity', 'location', 'priority', 'notes', 'source')})
    if data.get('trigger_reference'):
        plan['trigger'] = dict(data['trigger_reference'], reason=data['trigger_reason'])
    return task_plan(plan)


def _reference(value):
    if value == 'none':
        return None
    kind, separator, key = value.partition(':')
    if not separator or kind not in ('crop_record', 'water_day') or not key:
        raise ValueError('Use none, crop_record:ID or water_day:ID.')
    return dict(kind=kind, id=key)


def _view(draft):
    if draft is None:
        return None
    step = _next(draft)
    prompt = question(draft, step) if step else words(draft, 'ready')
    if step == 'cycle_id':
        prompt += '\n'+'\n'.join(f"{i}. {c['crop']} · {c['planted_on']} · {c['id']}" for i, c in enumerate(draft['cycles'], 1))
    source_page = _choice_page(draft, 'source_browser')
    if step == 'trigger' and source_page is not None:
        prompt += '\n'+_record_prompt(draft, source_page, 'source')
    task_page = _choice_page(draft, 'task_browser')
    if step == 'record_id' and task_page is not None:
        prompt = _task_prompt(draft, task_page)
    evidence_page = _choice_page(draft, 'evidence_browser')
    if step == 'evidence' and evidence_page is not None:
        prompt += '\n'+_record_prompt(draft, evidence_page, 'evidence')
    window = None
    if step is None and draft['data']['action'] == 'plan':
        cycle = next(c for c in draft['cycles'] if c['id'] == draft['data']['cycle_id'])
        window = task_window(_plan(draft)['due'], cycle)
    return dict(draft_id=draft['id'], revision=draft['revision'], unit_id=draft['unit_id'],
        unit_name=draft['unit_name'], language=draft['language'], timezone=draft['timezone'],
        translation_status='original' if draft['language'] == 'en' else 'draft',
        cycles=draft['cycles'], answers=draft['data'], fields=_fields(draft), window=window,
        source_options=source_page, task_options=task_page, evidence_options=evidence_page,
        status='collecting' if step else 'ready', next_field=step, question=prompt)


def render_task_intake(view):
    if view is None:
        return 'No task draft is active. Start with /task.'
    if view['status'] == 'saved':
        return words(view, 'saved', id=view['record']['id'])
    lines = [words(view, 'title')+' — '+view['unit_name']+' ('+view['timezone']+')', words(view, 'note')]
    if note := translation_note(view):
        lines.append(note)
    lines.append(view['question'])
    if selected := view['answers'].get('task_snapshot'):
        lines.append(_short(selected['data']['title'], 160)+' · '+selected['data']['status']+' · '+_short(selected['cycle_id'], 64))
    if trigger := view['answers'].get('trigger_snapshot'):
        lines.append(words(view, 'trigger_details'))
        details = trigger.get('data', trigger)
        keys = ('day', 'observed_at', 'location', 'symptoms', 'stage', 'inspected_count', 'affected_count',
                'quantity', 'value', 'unit', 'provenance', 'effective_quality', 'source')
        lines.extend(key.replace('_', ' ')+': '+str(details[key]) for key in keys if key in details)
    if evidence := view['answers'].get('evidence_snapshot'):
        details = evidence['data']
        lines.append(words(view, 'evidence_details'))
        lines.append(' · '.join(str(value) for value in (details['day'], evidence.get('kind', 'daily_water'),
            details['provenance'], details.get('quality'), task_triggers.record_summary(evidence)) if value is not None))
    if view['status'] == 'ready':
        for key in view['fields']:
            lines.append(LABELS[key][LANGUAGES.index(view['language'])]+': '+str(view['answers'][key]))
        if view['window']:
            lines.append(f"{view['window']['start']} – {view['window']['end']} ({view['timezone']})")
    lines.append(words(view, 'footer'))
    return '\n'.join(lines)


class TaskConsultation:
    def __init__(self, db):
        self.db = db

    def active(self, owner, unit_id):
        return bool(self.db.query('SELECT 1 FROM task_intake_drafts d JOIN production_units u ON u.id=d.unit_id '
            'JOIN farms f ON f.id=u.farm_id WHERE d.unit_id=? AND f.owner_id=?', (unit_id, owner)))

    def _read(self, conn, owner, unit_id):
        _scope(conn, owner, unit_id)
        row = conn.execute('SELECT payload FROM task_intake_drafts WHERE unit_id=?', (unit_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write(self, conn, draft):
        draft.update(revision=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat())
        conn.execute('UPDATE task_intake_drafts SET revision=?,updated_at=?,payload=? WHERE unit_id=?',
            (draft['revision'], draft['updated_at'], _json(draft), draft['unit_id']))

    def get(self, owner, unit_id):
        with self.db.transaction() as conn:
            return _view(self._read(conn, owner, unit_id))

    def start(self, owner, unit_id, language=None):
        if language is not None:
            validate_language(language)
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft:
                if language is not None and language != draft['language']:
                    draft['language'] = language
                    self._write(conn, draft)
                return _view(draft)
            if conn.execute('SELECT 1 FROM unit_setup_drafts WHERE owner_id=?', (owner,)).fetchone() or any(
                conn.execute(f'SELECT 1 FROM {table} WHERE unit_id=?', (unit_id,)).fetchone() for table in OTHER_DRAFTS):
                raise ValueError('Finish or cancel the active setup or record-entry draft before /task.')
            tz, _ = _scope(conn, owner, unit_id)
            try:
                ZoneInfo(tz)
            except ZoneInfoNotFoundError as exc:
                raise ValueError('A valid farm timezone is required before /task.') from exc
            cycles = [_cycle(conn, unit_id, dict(c)) for c in conn.execute(
                'SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id', (unit_id,))]
            if not cycles:
                raise ValueError('Record a crop cycle with /plant before /task.')
            now = datetime.now(timezone.utc).isoformat()
            draft = dict(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, unit_id=unit_id,
                unit_name=conn.execute('SELECT name FROM production_units WHERE id=?', (unit_id,)).fetchone()[0],
                timezone=tz, language=language or 'en', intake_version=5, cycles=cycles, data={}, history=[], created_at=now, updated_at=now)
            conn.execute('INSERT INTO task_intake_drafts VALUES(?,?,?,?,?,?)',
                (unit_id, draft['id'], draft['revision'], now, now, _json(draft)))
            return _view(draft)

    def _check(self, conn, owner, draft):
        data = draft['data']
        tz, cycle = _scope(conn, owner, draft['unit_id'], data.get('cycle_id'))
        if tz != draft['timezone']:
            raise ValueError('Farm timezone changed; /cancel and start /task again.')
        if cycle and _cycle(conn, draft['unit_id'], cycle) != next((c for c in draft['cycles'] if c['id'] == cycle['id']), None):
            raise ValueError('Crop cycle changed; /cancel and start /task again to review it.')
        if 'task_snapshot' in data:
            _latest(conn, data['task_snapshot'], ('calendar',))
        if 'completed_on' in data:
            if _date(data['completed_on']) > datetime.now(ZoneInfo(tz)).date():
                raise ValueError('Completion cannot be later than today in the farm timezone.')
        if 'evidence' in data:
            completion_evidence(conn, draft['unit_id'], data['cycle_id'], data['task_snapshot']['data']['activity'],
                                data['completed_on'], _reference(data['evidence']))
        if data.get('trigger_reference') and task_triggers.review(conn, draft['unit_id'], data['cycle_id'],
                data['trigger_reference'], data['trigger_snapshot'])['needs_review']:
            raise ValueError('Task trigger changed; /back to select and review its current record again.')
        if data.get('action') == 'plan' and 'entry' in data:
            if conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind='calendar' AND batch=?",
                (draft['unit_id'], data['cycle_id'], data['entry'])).fetchone():
                raise ValueError('Task entry already exists; /back to choose a distinct entry or /cancel.')
        if 'basis' in data:
            keys = ('start', 'end') if data['basis'] == 'date' else ('start_offset_days', 'end_offset_days')
            if all(key in data for key in keys):
                if data[keys[0]] > data[keys[1]]:
                    raise ValueError('Window end must be on or after its start.')
                task_window(dict(basis=data['basis'], **{key: data[key] for key in keys}), cycle)

    def answer(self, owner, unit_id, answer, expected_revision, *, back=False):
        if type(back) is not bool:
            raise ValueError('back must be a boolean')
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise ValueError('Task draft changed or ended; resume /task.')
            if back:
                if draft['history']:
                    draft['data'] = draft['history'].pop()
            else:
                self._check(conn, owner, draft)
                key = _next(draft)
                if key is None:
                    raise ValueError('Review the task and reply save, /back or /cancel.')
                value = text('answer', answer).strip()
                if len(value) > (1000 if key in ('source', 'notes', 'reason', 'trigger_reason') else 200) or value.startswith('/'):
                    raise ValueError('Use text up to 200 characters, or 1000 for notes, reason and source; not a command.')
                browser_key = {'trigger': 'source_browser', 'record_id': 'task_browser', 'evidence': 'evidence_browser'}.get(key)
                if browser_key in draft:
                    page = _choice_page(draft, browser_key)
                    if value in ('next', 'previous', 'refresh'):
                        if value == 'refresh':
                            draft.pop(browser_key)
                            _prepare_choices(conn, draft)
                        else:
                            target = page['page'] + (1 if value == 'next' else -1)
                            if not 1 <= target <= page['pages']:
                                raise ValueError('No page in that direction. Select a displayed number or refresh.')
                            draft[browser_key]['page'] = target
                        self._write(conn, draft)
                        return _view(draft)
                    # UUID record IDs can contain digits only; they remain exact IDs.
                    if re.fullmatch(r'[0-9]+', value) and not (key == 'record_id' and len(value) == 32):
                        selected = next((item for item in page['items'] if str(item['number']) == value), None)
                        if selected is None:
                            raise ValueError('Choose a number displayed on this page, or next/previous/refresh.')
                        if key == 'trigger':
                            reference = {key: selected[key] for key in ('kind', 'id', 'review_revision_id') if key in selected}
                            try:
                                task_triggers.adopt(conn, unit_id, draft['data']['cycle_id'], dict(reference, reason='Source selection'))
                            except ValueError as exc:
                                raise ValueError('Source list changed; reply refresh and review the source again. '+str(exc)) from exc
                            value = selected['kind']+':'+selected['id']
                        elif key == 'evidence':
                            data = draft['data']
                            try:
                                completion_evidence(conn, unit_id, data['cycle_id'], data['task_snapshot']['data']['activity'],
                                    data['completed_on'], {key: selected[key] for key in ('kind', 'id')})
                            except ValueError as exc:
                                raise ValueError('Completion records changed; reply refresh and review the record again. '+str(exc)) from exc
                            value = selected['kind']+':'+selected['id']
                        else:
                            try:
                                _latest(conn, _read(conn, unit_id, selected['id']), ('calendar',))
                            except ValueError as exc:
                                raise ValueError('Task list changed; reply refresh and review the task again. '+str(exc)) from exc
                            value = selected['id']
                data = draft['data']
                draft['history'].append(dict(data))
                options = dict(action=('plan', 'completed', 'cancelled', 'reopen'), activity=ACTIVITIES,
                               basis=('date', 'planting'), priority=('low', 'normal', 'high'))
                if key in options:
                    value = choice(key, value.lower(), options[key])
                elif key == 'cycle_id':
                    if re.fullmatch(r'[0-9]{1,9}', value) and 1 <= int(value) <= len(draft['cycles']):
                        value = draft['cycles'][int(value)-1]['id']
                    if value not in {c['id'] for c in draft['cycles']}:
                        raise ValueError('Choose a listed crop cycle.')
                elif key == 'record_id':
                    selected = _read(conn, unit_id, value)
                    _latest(conn, selected, ('calendar',))
                    if data['action'] == 'reopen' and selected['data']['status'] == 'planned':
                        raise ValueError('This task is already planned.')
                    data.update(task_snapshot=selected, cycle_id=selected['cycle_id'])
                elif key == 'trigger' and value != 'none':
                    kind, separator, identifier = value.partition(':')
                    if not separator or kind not in task_triggers.KINDS or not identifier:
                        raise ValueError('Use none, crop_record:ID, measurement:ID or water_day:ID.')
                    record, state = task_triggers._source(conn, unit_id, data['cycle_id'], kind, identifier)
                    if state != 'current':
                        raise ValueError('Task trigger must be current, non-rejected and in this unit and cycle: '+state)
                    reference = dict(kind=kind, id=identifier)
                    if kind == 'measurement':
                        reference['review_revision_id'] = record['quality_review']['id'] if record['quality_review'] else None
                    data.update(trigger_reference=reference, trigger_snapshot=record)
                elif key == 'evidence' and value != 'none' and draft.get('intake_version', 1) >= 5:
                    evidence = completion_evidence(conn, unit_id, data['cycle_id'], data['task_snapshot']['data']['activity'],
                        data['completed_on'], _reference(value))
                    data['evidence_snapshot'] = evidence['snapshot']
                elif key in ('start', 'end', 'completed_on'):
                    value = _date(value).isoformat()
                elif key in ('start_offset_days', 'end_offset_days'):
                    if not re.fullmatch(r'-?[0-9]{1,4}', value) or not -365 <= int(value) <= 3650:
                        raise ValueError('Enter an integer from -365 to 3650.')
                    value = int(value)
                data[key] = value
                self._check(conn, owner, draft)
            _prepare_choices(conn, draft)
            self._write(conn, draft)
            return _view(draft)

    def cancel(self, owner, unit_id, expected_revision):
        with self.db.transaction() as conn:
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['revision'] != expected_revision:
                raise ValueError('Task draft changed or ended; resume /task.')
            conn.execute('DELETE FROM task_intake_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='cancelled')

    def save(self, owner, unit_id, draft_id, expected_revision):
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            text('draft_id', draft_id)
            text('expected_revision', expected_revision)
            request_id = 'task-intake:'+draft_id
            previous = conn.execute('SELECT payload FROM crop_records WHERE unit_id=? AND request_id=?', (unit_id, request_id)).fetchone()
            if previous:
                record = json.loads(previous[0])
                receipt = record['operation'].get('intake', {})
                if receipt.get('draft_id') != draft_id or receipt.get('revision') != expected_revision:
                    raise ValueError('Task save receipt does not match this draft revision.')
                return dict(status='saved', record=record, language=receipt['language'])
            draft = self._read(conn, owner, unit_id)
            if draft is None or draft['id'] != draft_id or draft['revision'] != expected_revision:
                raise ValueError('Task draft changed or ended; resume /task.')
            self._check(conn, owner, draft)
            if _next(draft):
                raise ValueError('Complete the task questions and review the preview before saving.')
            data = draft['data']
            receipt = dict(draft_id=draft_id, revision=expected_revision, language=draft['language'])
            if data['action'] == 'plan':
                record = plan_task(conn, owner, unit_id, data['cycle_id'], _plan(draft), request_id=request_id, intake=receipt)
            else:
                record = resolve_task(conn, owner, unit_id, data['record_id'],
                    'planned' if data['action'] == 'reopen' else data['action'], data['reason'], data['source'],
                    completed_on=data.get('completed_on'), completion_record=_reference(data['evidence']) if 'evidence' in data else None,
                    request_id=request_id, intake=receipt)
            conn.execute('DELETE FROM task_intake_drafts WHERE unit_id=?', (unit_id,))
            return dict(status='saved', record=record, language=draft['language'])
