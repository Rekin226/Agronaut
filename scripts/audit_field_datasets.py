"""Reproduce the local screening of licensed field-data candidates; no model scoring.

Run from the checkout: python -m scripts.audit_field_datasets --output report.json
Original source bytes are checksum-pinned. No network, farm database, data ingestion,
parameter fitting or external author code is involved.
"""

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rows(raw, *, encoding, delimiter):
    text = raw.decode(encoding)
    reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter)
    fields = reader.fieldnames
    if not fields or len(set(key.strip() for key in fields)) != len(fields):
        raise ValueError('missing or duplicate column headers')
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('inconsistent column count')
        rows.append({key.strip(): value for key, value in row.items()})
    if not rows:
        raise ValueError('empty data table')
    return rows


def _numeric(value, *, comma_decimal=False):
    # This choice applies only to the explicitly identified source, not generic imports.
    if comma_decimal:
        if '.' in value and ',' in value:
            raise ValueError('ambiguous decimal notation')
        value = value.replace(',', '.')
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('unexpected nonnumeric source cell') from exc
    if not parsed.is_finite():
        raise ValueError('nonfinite source cell')
    return parsed


def _range(values):
    values = list(values)
    return {'minimum': str(min(values)), 'maximum': str(max(values))}


def _counts(rows, key):
    return dict(sorted(Counter(row[key] for row in rows).items()))


def audit(root=ROOT):
    registry = json.loads((root/'data/field_dataset_registry.json').read_text(encoding='utf-8'))
    if registry['schema_version'] != 1:
        raise ValueError('unsupported field dataset registry version')
    reports = []
    for dataset in registry['datasets']:
        checked, contents = [], {}
        for item in dataset['files']:
            path = (root/item['path']).resolve()
            path.relative_to(root.resolve())
            raw = path.read_bytes()
            if (len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']
                    or hashlib.md5(raw).hexdigest() != item['repository_md5']):
                raise ValueError(f"source file differs from retained repository checksums: {item['path']}")
            contents[path.name] = raw
            checked.append(dict(path=item['path'], bytes=len(raw), sha256=item['sha256']))
        summary = None
        if dataset['id'] == 'burkina_soil_2018':
            rows = _rows(contents['burkina_soil_2018.tsv'], encoding='utf-8', delimiter='\t')
            numeric_columns = [key for key in rows[0] if key not in ('N_Labo', 'Field_ID', 'Zone', 'Rice_system', 'Site')]
            for row in rows:
                for column in numeric_columns:
                    _numeric(row[column], comma_decimal=True)
            summary = dict(rows=len(rows), columns=len(rows[0]),
                unique_samples=len({r['N_Labo'] for r in rows}), unique_fields=len({r['Field_ID'] for r in rows}),
                sites=_counts(rows, 'Site'), rice_systems=_counts(rows, 'Rice_system'),
                blank_cells=sum(not v.strip() for r in rows for v in r.values()),
                ph_water=_range(_numeric(r['pHeau'], comma_decimal=True) for r in rows),
                ph_kcl=_range(_numeric(r['pHkcl'], comma_decimal=True) for r in rows),
                particle_fraction_sum_percent=_range(sum(_numeric(r[k], comma_decimal=True)
                    for k in ('Argile_%', 'Limons_totaux_%', 'Sables_totaux_%')) for r in rows),
                parsing='UTF-8 TSV; whitespace trimmed from headers only; decimal commas parsed explicitly for this source; original files unchanged.')
        elif dataset['id'] == 'irridrone_2026':
            # Windows-1252 is an explicit decoding choice for the supplied non-UTF-8
            # multiplication sign. Only ASCII identifiers/numbers enter the summaries.
            rows = _rows(contents['irridrone_metadata.csv'], encoding='cp1252', delimiter=',')
            for row in rows:
                if not row['Mean_Discharge_lps'].isascii():
                    raise ValueError('discharge must use ASCII numeric notation')
                _numeric(row['Mean_Discharge_lps'])
            summary = dict(rows=len(rows), columns=len(rows[0]),
                unique_recording_names=len({r['Video_Name_mp4'] for r in rows}),
                crops=_counts(rows, 'Crop'), active_sprinklers=_counts(rows, 'Active_Sprinklers'),
                relative_pressure_categories=_counts(rows, 'Pressure'),
                blank_cells=sum(not v.strip() for r in rows for v in r.values()),
                discharge_lps=_range(_numeric(r['Mean_Discharge_lps']) for r in rows),
                distinct_discharge_values=len({_numeric(r['Mean_Discharge_lps']) for r in rows}),
                parsing='CSV decoded as Windows-1252 (audit choice); no date/time interpretation, unit conversion or video download.')
        reports.append(dict(id=dataset['id'], doi=dataset['doi'], version=dataset['version'],
            license=dataset['license'], checked_files=checked, observed_file_summary=summary,
            intended_audit_role=dataset['audit_role'], reviewer_assessed_limitations=dataset['limitations'],
            access_evidence=dataset.get('access_evidence'), model_validation_performed=False))
    return dict(schema_version=1, registry_audited_on=registry['audited_on'],
        scope=registry['scope'], datasets=reports,
        conclusion='No candidate in this screening supplies a complete held-out validation case for the implemented irrigation models. '
                   'No performance score, field-validation status, pilot partnership or observed farm record was created.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='save JSON report; default stdout')
    args = parser.parse_args()
    try:
        result = json.dumps(audit(), indent=2, ensure_ascii=False, allow_nan=False)+'\n'
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'Field evidence audit failed: {exc}\n')
    if args.output:
        args.output.write_text(result, encoding='utf-8')
    else:
        print(result, end='')


if __name__ == '__main__':
    main()
