"""Exact source records behind operator task decisions, without inferred causality."""
import json

from agro_model.validation import choice, text

from .field_io import _mapping
from .observation_quality import reviewed_observation

KINDS = ('crop_record', 'measurement', 'water_day')
CROP_KINDS = ('scouting', 'stage', 'transplant', 'harvest', 'fertilizer')


def reference(value):
    if value is None:
        return None
    value = dict(_mapping(value, ('kind', 'id', 'reason', 'review_revision_id'), 'task trigger'))
    choice('task trigger kind', value.get('kind'), KINDS)
    for key in ('id', 'reason'):
        value[key] = text('trigger.'+key, value.get(key)).strip()
    if value['kind'] == 'measurement':
        if 'review_revision_id' not in value:
            raise ValueError('Measurement trigger requires review_revision_id; use null only when no review exists.')
        if value['review_revision_id'] is not None:
            text('trigger.review_revision_id', value['review_revision_id'])
    elif 'review_revision_id' in value:
        raise ValueError('Only measurement triggers accept review_revision_id.')
    return value


def _source(conn, unit_id, cycle_id, kind, key):
    choice('trigger kind', kind, KINDS)
    table = dict(crop_record='crop_records', measurement='field_observations', water_day='field_water_days')[kind]
    row = conn.execute(f'SELECT payload FROM {table} WHERE id=? AND unit_id=? AND cycle_id=?',
                       (key, unit_id, cycle_id)).fetchone()
    if row is None:
        return None, 'missing'
    record = json.loads(row[0])
    if kind == 'measurement':
        record = reviewed_observation(conn, record)
        record.pop('quality_history')
        return record, 'rejected' if record['effective_quality'] == 'rejected' else 'current'
    if kind == 'crop_record' and record['kind'] not in CROP_KINDS:
        return record, 'unsupported'
    if conn.execute(f'SELECT 1 FROM {table} WHERE supersedes_id=?', (key,)).fetchone():
        return record, 'superseded'
    if record.get('voided'):
        return record, 'voided'
    if record['data'].get('quality') == 'rejected':
        return record, 'rejected'
    return record, 'current'


def adopt(conn, unit_id, cycle_id, value):
    """Retain a source only after the caller has checked owner/unit scope."""
    value = reference(value)
    if value is None:
        return None
    record, state = _source(conn, unit_id, cycle_id, value['kind'], value['id'])
    if state != 'current':
        raise ValueError('Task trigger must be a current, non-rejected supported record owned by the same unit and cycle: '+state)
    if value['kind'] == 'measurement':
        revision = record['quality_review']['id'] if record['quality_review'] else None
        if revision != value['review_revision_id']:
            raise ValueError('Measurement trigger review changed; inspect its current review before adopting it.')
    return record


def review(conn, unit_id, cycle_id, value, snapshot):
    if value is None:
        return dict(state='not_linked', needs_review=False)
    record, state = _source(conn, unit_id, cycle_id, value['kind'], value['id'])
    if state == 'current' and record != snapshot:
        state = 'review_changed' if value['kind'] == 'measurement' else 'changed'
    return dict(state=state, needs_review=state != 'current')


def record_summary(record):
    """Brief supplied details for identification; no inferred diagnosis or totals."""
    data = record.get('data', record)
    details = [str(data[key]) for key in ('location', 'symptoms', 'stage', 'product', 'value', 'quantity', 'unit') if key in data]
    if 'net_irrigation_mm' in data:
        details = [f"rain {data['rainfall_mm']} mm", f"net irrigation {data['net_irrigation_mm']} mm"]
    return ' '.join(' · '.join(details).split())[:160]


def candidates(conn, unit_id, cycle_id):
    result = []
    for kind, table in (('crop_record','crop_records'), ('measurement','field_observations'), ('water_day','field_water_days')):
        for row in conn.execute(f'SELECT id FROM {table} WHERE unit_id=? AND cycle_id=? ORDER BY rowid', (unit_id, cycle_id)):
            record, state = _source(conn, unit_id, cycle_id, kind, row[0])
            if state != 'current':
                continue
            if kind == 'measurement':
                result.append(dict(kind=kind, id=record['id'], label=record['quantity'], when=record['observed_at'],
                    provenance=record['provenance'], quality=record['effective_quality'],
                    review_revision_id=record['quality_review']['id'] if record['quality_review'] else None))
            else:
                data = record['data']
                result.append(dict(kind=kind, id=record['id'], label=record.get('kind', 'daily_water'), when=data['day'],
                    provenance=data['provenance'], quality=data.get('quality')))
            # Short, supplied details help operators distinguish entries on the same day.
            result[-1]['summary'] = record_summary(record)
    return result
