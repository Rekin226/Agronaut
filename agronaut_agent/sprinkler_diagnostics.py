"""Read-only comparison of owned sprinkler scenarios and reviewed field readings."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.hydraulics import EPA_NETWORK
from agro_model.resources import _object
from agro_model.sprinkler_diagnostics import predicted_pressure, summarize, summarize_events
from agro_model.validation import aware_time, choice, number, text

from .cycle_lifecycle import ensure_cycle_day
from .field_workflow import _read, _scope
from .observation_quality import reviewed_observation
from .prediction_timing import (
    LIMITATION,
    prediction_event_timing,
    render_timing,
    timing_counts,
)

METRICS=('nozzle_flow_lph','nozzle_pressure_kpa','base_pressure_kpa','inlet_flow_lph','inlet_pressure_kpa')
EVENT_FIELDS=('event_id','started_at','ended_at','stable_operation','conditions_note','representation_note',
              'independence','water_density_kg_m3','density_source','mappings')
MAPPING_FIELDS=('observation_id','expected_review','target','station_id','mapping_note','pressure_reference')


def describe_sprinkler_comparison():
    return dict(operations=dict(review_sprinkler_measurements='{calculation_id}',
        compare_sprinkler_lateral='{calculation_id,dataset_kind:synthetic|field,selection_note,comparisons:[event]}'),
        event_fields=list(EVENT_FIELDS),mapping_fields=list(MAPPING_FIELDS),
        choices=dict(target=['inlet','base','nozzle','unmatched'],independence=['held_out','used_for_calibration','unknown'],
                     pressure_reference=['gauge_at_target','unknown','not_applicable']),
        rules=[
            'Each event requires its complete start/end timestamps with offsets, declared stable operation true, matching-equipment/source notes and density or null with a reason.',
            'Map existing flow (L/h) or pressure (kPa) observations from the same owned unit and exact crop cycle. Use current quality_review.id as expected_review, or null when no review exists.',
            'Nozzle and base targets require an exact modeled station ID. Inlet and unmatched targets require station_id null. Base targets accept pressure only; inlet/nozzle accept pressure or flow.',
            'Pressure needs gauge_at_target or unknown; flow needs not_applicable. Density is required only to compare pressure and must have an explicit source or reason it is unknown.',
            'Select each observation once in the report and each target/quantity once per event. Events must be completed, nonoverlapping windows; a reading timestamp must fall inside its selected event.',
            'All readings remain visible. Only mapped measured/reported readings marked checked with known pressure reference/density and held_out events enter aggregate metrics.',
            'Unknown mappings, rejected readings, estimates/forecasts and calibration-use declarations never become validation evidence. Counts do not establish independent samples.',
        ])


def _scenario(conn, owner, unit_id, calculation_id):
    _scope(conn,owner,unit_id)
    saved=_read(conn,'field_calculations',unit_id,calculation_id)
    if saved['task']!='sprinkler_lateral' or saved['result']['result']['prediction'] is None:
        raise ValueError('comparison requires a solved sprinkler_lateral scenario')
    tz,cycle=_scope(conn,owner,unit_id,saved['cycle_id'])
    if saved['inputs']['site']['timezone']!=tz:
        raise ValueError('scenario timezone must match the current farm timezone')
    return saved,tz,cycle


def _targets(saved):
    prediction=saved['result']['result']['prediction']
    targets={('inlet',None,'flow'):prediction['inlet_flow_lph'],
             ('inlet',None,'pressure'):prediction['inlet_pressure_head_m']}
    for row in prediction['nodes']:
        targets[('nozzle',row['identifier'],'flow')]=row['nozzle_flow_lph']
        targets[('nozzle',row['identifier'],'pressure')]=row['nozzle_pressure_head_m']
        targets[('base',row['identifier'],'pressure')]=row['base_pressure_head_m']
    return targets


def review_sprinkler_measurements(workflow, owner, unit_id, calculation_id):
    with workflow.db.transaction() as conn:
        saved,tz,cycle=_scenario(conn,owner,unit_id,calculation_id)
        records=conn.execute("SELECT payload FROM field_observations WHERE unit_id=? AND cycle_id IS ? ORDER BY observed_at,id",
                         (unit_id,saved['cycle_id'])).fetchall()
        originals=[json.loads(row[0]) for row in records]
        observations=[reviewed_observation(conn,row) for row in originals if row['quantity'] in ('flow','pressure')]
        return dict(calculation_id=calculation_id,cycle=cycle,timezone=tz,observations=observations,
                    targets=[dict(target=k[0],station_id=k[1],quantity=k[2],predicted_value=v,
                                  predicted_unit='L/h' if k[2]=='flow' else 'm pressure head') for k,v in _targets(saved).items()],
                    contract=describe_sprinkler_comparison())


def compare_sprinkler_lateral(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note):
    choice('dataset_kind',dataset_kind,('synthetic','field'))
    text('selection_note',selection_note)
    if not isinstance(comparisons,list) or not 1<=len(comparisons)<=100:
        raise ValueError('select 1–100 completed comparison events')
    with workflow.db.transaction() as conn:
        saved,tz,cycle=_scenario(conn,owner,unit_id,calculation_id)
        targets=_targets(saved)
        used_observations,event_ids,windows,rows=set(),set(),[],[]
        for supplied in comparisons:
            event=_object(supplied,EVENT_FIELDS,'sprinkler comparison event')
            for key in ('event_id','conditions_note','representation_note','density_source'):
                text(key,event[key])
            event_id=event['event_id'].strip()
            if event_id in event_ids:
                raise ValueError('event IDs must be unique')
            event_ids.add(event_id)
            start,end=(aware_time(key,datetime.fromisoformat(text(key,event[key]))).astimezone(timezone.utc)
                       for key in ('started_at','ended_at'))
            number('event elapsed seconds',(end-start).total_seconds(),low=1,high=86400)
            if end>datetime.now(timezone.utc):
                raise ValueError('comparison events must have ended')
            if any(start<other_end and end>other_start for other_start,other_end in windows):
                raise ValueError('comparison event windows must not overlap')
            windows.append((start,end))
            if event['stable_operation'] is not True:
                raise ValueError('stable_operation must be explicitly true')
            choice('independence',event['independence'],('held_out','used_for_calibration','unknown'))
            density=event['water_density_kg_m3']
            if density is not None:
                number('water_density_kg_m3',density,500,2000)
            if cycle:
                for stamp in (start,end):
                    day=stamp.astimezone(ZoneInfo(tz)).date().isoformat()
                    if day<cycle['planted_on']:
                        raise ValueError('comparison event precedes the current crop planting date')
                    ensure_cycle_day(conn,unit_id,saved['cycle_id'],day)
            mappings=event['mappings']
            if not isinstance(mappings,list) or not 1<=len(mappings)<=20000:
                raise ValueError('each event needs 1–20000 explicit reading mappings')
            mapped,pairs=set(),[]
            for item in mappings:
                mapping=_object(item,MAPPING_FIELDS,'sprinkler reading mapping')
                identifier=text('observation_id',mapping['observation_id'])
                text('mapping_note',mapping['mapping_note'])
                if identifier in used_observations:
                    raise ValueError('select each observation only once across events')
                used_observations.add(identifier)
                if len(used_observations)>20000:
                    raise ValueError('selected events exceed 20000 readings')
                original=_read(conn,'field_observations',unit_id,identifier)
                if original['cycle_id']!=saved['cycle_id']:
                    raise ValueError('reading must belong to the exact same crop cycle as the scenario')
                quantity=choice('reading quantity',original['quantity'],('flow','pressure'))
                unit='L/h' if quantity=='flow' else 'kPa'
                if original['unit']!=unit:
                    raise ValueError('reading units do not match the hydraulic quantity')
                number('recorded hydraulic reading',original['value'],0,1e9)
                stamp=datetime.fromisoformat(original['observed_at']).astimezone(timezone.utc)
                if not start<=stamp<=end:
                    raise ValueError('reading timestamp must fall inside its comparison event')
                observation=reviewed_observation(conn,original)
                review=observation['quality_review']
                if mapping['expected_review']!=(review['id'] if review else None):
                    raise ValueError('measurement review changed; refresh review_sprinkler_measurements')
                target=choice('target',mapping['target'],('inlet','base','nozzle','unmatched'))
                station=mapping['station_id']
                if target in ('base','nozzle'):
                    text('station_id',station)
                elif station is not None:
                    raise ValueError('inlet and unmatched targets require station_id null')
                key=(target,station,quantity)
                if target!='unmatched':
                    if key not in targets or key in mapped:
                        raise ValueError('target must support this quantity and be mapped at most once per event')
                    mapped.add(key)
                reference=mapping['pressure_reference']
                choice('pressure_reference',reference,('not_applicable',) if quantity=='flow' else ('gauge_at_target','unknown'))
                predicted=targets.get(key)
                if predicted is not None and quantity=='pressure':
                    predicted=predicted_pressure(predicted,density)
                status='compared'
                if target=='unmatched':
                    status='unmatched_target'
                elif observation['effective_quality']=='rejected':
                    status='rejected_reading'
                elif observation['provenance'] not in ('measured','reported'):
                    status='unsupported_provenance'
                elif predicted is None:
                    status='missing_water_density'
                elif reference=='unknown':
                    status='unknown_pressure_reference'
                error=predicted-original['value'] if status=='compared' else None
                exclusions=[]
                if status!='compared':
                    exclusions.append(status)
                if observation['effective_quality']!='checked':
                    exclusions.append('quality_not_checked')
                if event['independence']!='held_out':
                    exclusions.append(event['independence'])
                metric=target+('_flow_lph' if quantity=='flow' else '_pressure_kpa') if target!='unmatched' else None
                pairs.append(dict(mapping=mapping,observation=observation,predicted=predicted,recorded=original['value'],
                    unit=unit,error=error,status=status,metric=metric,included_in_metrics=not exclusions,exclusion_reasons=exclusions))
            rows.append(dict(comparison_input=event,event_start_utc=start.isoformat(),event_end_utc=end.isoformat(),pairs=pairs,
                prediction_timing=prediction_event_timing(saved.get('created_at'), start, end),
                metrics={metric:summarize(p['error'] for p in pairs if p['metric']==metric and p['included_in_metrics']) for metric in METRICS},
                unmapped_targets=[dict(target=k[0],station_id=k[1],quantity=k[2]) for k in targets if k not in mapped]))
        rows.sort(key=lambda row:row['event_start_utc'])
        return dict(schema_version=2,report_type='sprinkler_lateral_diagnostic',evidence_status='diagnostic',
            timing_counts=timing_counts(rows),
            generated_at=datetime.now(timezone.utc).isoformat(),calculation=saved,
            declaration=dict(dataset_kind=dataset_kind,selection_note=selection_note),comparisons=rows,
            metrics={metric:summarize_events([[p['error'] for p in row['pairs'] if p['metric']==metric and p['included_in_metrics']]
                       for row in rows if row['comparison_input']['independence']=='held_out']) for metric in METRICS},
            selected_event_count=len(rows),selected_reading_count=len(used_observations),
            included_reading_count=sum(p['included_in_metrics'] for row in rows for p in row['pairs']),
            independence_status='not_established',acceptance_status='not_assessed',sources=[EPA_NETWORK],
            assumptions=[
                'Reading locations, stable operation and source/equipment correspondence are explicit operator declarations.',
                'Pressure is gauge pressure at the declared nozzle, riser base or inlet. Conversion uses supplied water density and standard gravity 9.80665 m/s².',
                'Error is predicted minus recorded. Only checked measured/reported readings from declared held-out events enter metrics.',
                'Each event with eligible pairs has equal aggregate weight within a target/quantity category; events without eligible pairs remain counted separately.',
            ],limitations=[
                LIMITATION,
                'Diagnostic residuals are not independent field validation, calibration, approval or proof of acceptable crop-water delivery.',
                'Checked is an operator quality review, not independent verification. Declared held-out use cannot detect undisclosed calibration, selection bias or correlated events.',
                'Unknown mappings, excluded readings and unmapped model targets remain explicit; absent readings are never zero error.',
                'Point pressure/flow measurements are distinct from catch-can depth and field uniformity; no catch-depth conversion to nozzle discharge is made.',
                'Readings are compared to the saved steady scenario; measurement uncertainty, variable source settings and transient hydraulics are not modeled.',
                'Reports are snapshots of current quality reviews. This read-only operation changes no observations, calculations, water records or equipment actions.',
            ])


def render_sprinkler_comparison(report):
    lines=['Sprinkler lateral comparison — DIAGNOSTIC',
           f"Dataset: {report['declaration']['dataset_kind']}; events: {report['selected_event_count']}; readings: {report['selected_reading_count']}; included: {report['included_reading_count']}",
           'Error is predicted minus recorded. Independence is not established; acceptance is not assessed.']
    lines.append(render_timing(report))
    for key,metric in report['metrics'].items():
        lines.append(f"{key}: {metric['event_count']} events, {metric['paired_reading_count']} pairs; "
                     +(f"bias {metric['bias']:.6g}; MAE {metric['mae']:.6g}; RMSE {metric['rmse']:.6g}" if metric['event_count'] else 'metrics unknown'))
    for key in ('assumptions','limitations'):
        lines += ['',key.title()+':',*('- '+item for item in report[key])]
    return '\n'.join(lines)
