"""Owned, reviewed measurements remain distinct from hydraulic predictions."""

from copy import deepcopy

import pytest

from agro_model.hydraulics import GRAVITY_M_S2
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_field_workflow import setup  # noqa: F401
from agronaut_agent.tests.test_sprinkler_lateral_workflow import inputs


def plan(workflow,**changes):
    data=inputs()
    for key,value in changes.items():
        data[key].update(value)
    return workflow.save('owner','field','sprinkler_lateral',data,cycle_id='cycle',request_id='scenario')


def reading(workflow,identifier,quantity='flow',value=1000,checked=True,**changes):
    records=CropRecords(workflow.db)
    data=dict(quantity=quantity,value=value,unit='L/h' if quantity=='flow' else 'kPa',
              observed_at='2026-07-06T10:15:00+00:00',provenance='measured',source='Synthetic '+identifier)
    data.update(changes)
    saved=records.record_measurement('owner','field',data,cycle_id='cycle',request_id=identifier)
    review=records.review_measurement('owner','field',saved['id'],'checked','Synthetic check','Fixture',
                                     request_id='check-'+identifier,expected_revision=None) if checked else None
    return dict(observation_id=saved['id'],expected_review=review['id'] if review else None,target='nozzle',station_id='S1',
                mapping_note='Synthetic station identification',pressure_reference='not_applicable' if quantity=='flow' else 'gauge_at_target')


def event(*mappings,**changes):
    data=dict(event_id='visit-1',started_at='2026-07-06T10:00:00+00:00',ended_at='2026-07-06T11:00:00+00:00',
        stable_operation=True,conditions_note='Synthetic matching inlet configuration',representation_note='Synthetic matching equipment and sensor location',
        independence='held_out',water_density_kg_m3=998,density_source='Synthetic fluid assumption',mappings=list(mappings))
    data.update(changes)
    return data


def compare(workflow,saved,*events,**changes):
    data=dict(calculation_id=saved['id'],comparisons=list(events),dataset_kind='synthetic',selection_note='Synthetic retained complete selection')
    data.update(changes)
    return execute_field_workflow(workflow.db,'owner','field','compare_sprinkler_lateral',data)


def test_inlet_base_and_nozzle_are_compared_in_separate_units_and_no_records_change(setup):  # noqa: F811
    workflow,farms=setup
    saved=plan(workflow)
    prediction=saved['result']['result']['prediction']
    node=prediction['nodes'][0]
    mappings=[]
    for i,(target,quantity,predicted) in enumerate([
        ('nozzle','flow',node['nozzle_flow_lph']),('nozzle','pressure',node['nozzle_pressure_head_m']*998*GRAVITY_M_S2/1000),
        ('base','pressure',node['base_pressure_head_m']*998*GRAVITY_M_S2/1000),
        ('inlet','flow',prediction['inlet_flow_lph']),('inlet','pressure',prediction['inlet_pressure_head_m']*998*GRAVITY_M_S2/1000)]):
        mapping=reading(workflow,str(i),quantity,predicted-2)
        mapping.update(target=target,station_id=None if target=='inlet' else 'S1')
        mappings.append(mapping)
    before=farms.export('owner')
    report=compare(workflow,saved,event(*mappings))
    assert report['included_reading_count']==5 and report['independence_status']=='not_established'
    for metric in report['metrics'].values():
        assert metric['paired_reading_count']==1 and metric['bias']==pytest.approx(2)
    assert report['comparisons'][0]['pairs'][0]['observation']['quality']=='unverified'
    assert report['comparisons'][0]['pairs'][0]['observation']['effective_quality']=='checked'
    assert len(report['comparisons'][0]['unmapped_targets'])==9
    assert farms.export('owner')==before
    review=workflow.review_sprinkler_measurements('owner','field',calculation_id=saved['id'])
    assert len(review['observations'])==5 and len(review['targets'])==14
    assert execute_field_workflow(workflow.db,'owner','field','describe_sprinkler_comparison',{})['event_fields']


def test_equal_event_weighting_does_not_pool_different_sample_counts_or_calibration_events(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    nodes=saved['result']['result']['prediction']['nodes']
    a=reading(workflow,'a',value=nodes[0]['nozzle_flow_lph']-3)
    b=reading(workflow,'b',value=nodes[1]['nozzle_flow_lph']+5)
    b['station_id']='S2'
    c=reading(workflow,'c',value=nodes[0]['nozzle_flow_lph']-9,observed_at='2026-07-07T10:15:00+00:00')
    d=reading(workflow,'d',value=0,observed_at='2026-07-08T10:15:00+00:00')
    report=compare(workflow,saved,event(a,b),event(c,event_id='second',started_at='2026-07-07T10:00:00Z',ended_at='2026-07-07T11:00:00Z'),
                   event(d,event_id='calibration',started_at='2026-07-08T10:00:00Z',ended_at='2026-07-08T11:00:00Z',independence='used_for_calibration'))
    metric=report['metrics']['nozzle_flow_lph']
    assert metric['event_count']==2 and metric['paired_reading_count']==3
    assert metric['bias']==pytest.approx(4) and metric['mae']==pytest.approx(6.5) and metric['rmse']==pytest.approx(7)
    assert report['comparisons'][-1]['pairs'][0]['error'] is not None
    assert report['comparisons'][-1]['pairs'][0]['exclusion_reasons']==['used_for_calibration']


def test_review_changes_require_refresh_and_rejected_or_unverified_readings_stay_visible(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    mapping=reading(workflow,'a',checked=False,value=0)
    first=compare(workflow,saved,event(mapping))
    pair=first['comparisons'][0]['pairs'][0]
    assert pair['recorded']==0 and pair['error']>0 and not pair['included_in_metrics']
    assert first['metrics']['nozzle_flow_lph']['bias'] is None
    records=CropRecords(workflow.db)
    rejected=records.review_measurement('owner','field',mapping['observation_id'],'rejected','Wrong sensor','Synthetic',
                                       request_id='reject',expected_revision=None)
    with pytest.raises(ValueError,match='review changed'):
        compare(workflow,saved,event(mapping))
    mapping['expected_review']=rejected['id']
    report=compare(workflow,saved,event(mapping))
    pair=report['comparisons'][0]['pairs'][0]
    assert pair['status']=='rejected_reading' and pair['error'] is None
    assert len(pair['observation']['quality_history'])==1
    checked=records.review_measurement('owner','field',mapping['observation_id'],'checked','Sensor verified','Synthetic',
                                      request_id='restore',expected_revision=rejected['id'])
    mapping['expected_review']=checked['id']
    assert compare(workflow,saved,event(mapping))['included_reading_count']==1


@pytest.mark.parametrize('provenance',['estimated','forecast'])
def test_nonobserved_provenance_cannot_enter_metrics(setup,provenance):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    mapping=reading(workflow,'a',provenance=provenance)
    pair=compare(workflow,saved,event(mapping))['comparisons'][0]['pairs'][0]
    assert pair['status']=='unsupported_provenance' and pair['error'] is None and not pair['included_in_metrics']


def test_density_reference_and_mapping_unknowns_are_not_zero_error(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    mapping=reading(workflow,'p','pressure',0)
    report=compare(workflow,saved,event(mapping,water_density_kg_m3=None))
    assert report['comparisons'][0]['pairs'][0]['status']=='missing_water_density'
    mapping['pressure_reference']='unknown'
    report=compare(workflow,saved,event(mapping))
    assert report['comparisons'][0]['pairs'][0]['status']=='unknown_pressure_reference'
    mapping.update(target='unmatched',station_id=None)
    report=compare(workflow,saved,event(mapping))
    assert report['comparisons'][0]['pairs'][0]['status']=='unmatched_target'
    assert len(report['comparisons'][0]['unmapped_targets'])==14
    assert all(m['bias'] is None for m in report['metrics'].values())


@pytest.mark.parametrize('change',[
    lambda e:e.update(stable_operation=1),lambda e:e.update(water_density_kg_m3=0),
    lambda e:e.update(started_at='2026-07-06T10:00:00'),lambda e:e.update(ended_at='2026-07-06T10:10:00Z'),
    lambda e:e.update(ended_at='2099-07-06T11:00:00Z'),lambda e:e.update(mappings=[]),
    lambda e:e['mappings'][0].update(station_id='missing'),lambda e:e['mappings'][0].update(target='base'),
    lambda e:e['mappings'][0].update(target='inlet'),lambda e:e['mappings'][0].update(pressure_reference='gauge_at_target'),
    lambda e:e['mappings'][0].update(mapping_note=''),lambda e:e['mappings'].append(deepcopy(e['mappings'][0])),
])
def test_invalid_event_or_mapping_cannot_be_compared(setup,change):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    supplied=event(reading(workflow,'a'))
    change(supplied)
    with pytest.raises(ValueError):
        compare(workflow,saved,supplied)


def test_duplicate_targets_overlapping_events_and_reused_observations_are_rejected(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    a,b=reading(workflow,'a'),reading(workflow,'b')
    with pytest.raises(ValueError,match='at most once'):
        compare(workflow,saved,event(a,b))
    with pytest.raises(ValueError,match='overlap'):
        compare(workflow,saved,event(a),event(b,event_id='other',started_at='2026-07-06T18:00:00+08:00',ended_at='2026-07-06T19:00:00+08:00'))
    with pytest.raises(ValueError,match='each observation'):
        compare(workflow,saved,event(a),event(a,event_id='other',started_at='2026-07-07T10:00:00Z',ended_at='2026-07-07T11:00:00Z'))


def test_owner_cycle_timezone_current_planting_and_solved_scenario_are_enforced(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    mapping=reading(workflow,'a')
    for owner,unit in [('other','field'),('owner','field2')]:
        with pytest.raises(ValueError):
            workflow.review_sprinkler_measurements(owner,unit,calculation_id=saved['id'])
    workflow.db.execute("UPDATE field_observations SET cycle_id='cycle2',payload=json_set(payload,'$.cycle_id','cycle2') WHERE id=?",(mapping['observation_id'],))
    with pytest.raises(ValueError,match='same crop cycle'):
        compare(workflow,saved,event(mapping))
    workflow.db.execute("UPDATE field_observations SET cycle_id='cycle',payload=json_set(payload,'$.cycle_id','cycle') WHERE id=?",(mapping['observation_id'],))
    workflow.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError,match='timezone'):
        compare(workflow,saved,event(mapping))
    workflow.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    workflow.db.execute("UPDATE production_cycles SET planted_on='2026-07-07' WHERE id='cycle'")
    with pytest.raises(ValueError,match='planting'):
        compare(workflow,saved,event(mapping))
    data=inputs()
    data['inlet']['running_head_m']=1
    unsolved=workflow.save('owner','field','sprinkler_lateral',data,request_id='unsupported')
    with pytest.raises(ValueError,match='solved'):
        compare(workflow,unsolved,event(mapping))


def test_failed_design_limits_remain_visible_in_diagnostics(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow,inlet=dict(max_flow_lph=1))
    report=compare(workflow,saved,event(reading(workflow,'a')))
    assert report['calculation']['result']['feasible'] is False
    assert report['included_reading_count']==1 and report['acceptance_status']=='not_assessed'


def test_event_end_respects_current_crop_closure(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    mapping=reading(workflow,'a',observed_at='2026-07-05T23:30:00Z')
    CropRecords(workflow.db).close_cycle('owner','field','cycle','2026-07-05','Synthetic end','Fixture',
                                       request_id='close',expected_revision=None)
    with pytest.raises(ValueError,match='cycle end'):
        compare(workflow,saved,event(mapping,started_at='2026-07-05T23:00:00Z',ended_at='2026-07-06T00:30:00Z'))


def test_shared_chat_tool_compares_scoped_readings_without_mutation(tmp_path):
    import json

    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units

    brain,owner=agent_and_units(tmp_path)
    brain.select_unit('cli','one','field')
    saved=brain.field_workflow('cli','one','save',dict(task='sprinkler_lateral',inputs=inputs(),request_id='scenario'))
    obs=brain.crop_records('cli','one','record_measurement',dict(request_id='flow',data=dict(quantity='flow',value=0,unit='L/h',
        observed_at='2026-07-06T10:15:00Z',provenance='measured',source='Synthetic no-flow reading')))
    mapping=dict(observation_id=obs['id'],expected_review=None,target='nozzle',station_id='S1',mapping_note='Synthetic station',pressure_reference='not_applicable')
    before=brain._farms.export(owner)
    data=dict(calculation_id=saved['id'],comparisons=[event(mapping)],dataset_kind='synthetic',selection_note='Synthetic unverified reading')
    brain._base.replies=[AIMessage(content='',tool_calls=[dict(name='field_records',id='compare-sprinkler',
        args=dict(operation='compare_sprinkler_lateral',data=data))]),AIMessage(content='Comparison ready.')]
    brain.handle_message('cli','one','Compare these supplied readings with my saved sprinkler scenario')
    report=json.loads(brain.tool_results('cli','one','field_records')[-1])
    assert report['evidence_status']=='diagnostic' and report['included_reading_count']==0
    assert report['comparisons'][0]['pairs'][0]['recorded']==0
    # A chat turn legitimately appends conversation history inside unit contexts.
    # The diagnostic must leave farm evidence and saved calculations untouched.
    after=brain._farms.export(owner)
    assert {k:v for k,v in after.items() if k!='contexts'}=={k:v for k,v in before.items() if k!='contexts'}


def test_offline_cli_records_reviews_and_compares_readings(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    root=Path(__file__).resolve().parents[2]
    database=str(tmp_path/'offline.sqlite3')
    request=tmp_path/'request.json'

    def operation(group,name,data):
        request.write_text(json.dumps(data))
        result=subprocess.run([sys.executable,'-S','-m','agronaut_agent.cli',group,'--db',database,name,'--input',str(request)],
                              cwd=root,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr+result.stdout
        return json.loads(result.stdout)

    spec=json.loads((root/'examples/soil_unit.json').read_text())
    spec['farm']['timezone']='UTC'
    operation('farm','create',spec)
    saved=operation('field','save',dict(task='sprinkler_lateral',inputs=inputs(),request_id='scenario'))
    obs=operation('crop','record_measurement',dict(request_id='flow',data=dict(quantity='flow',value=1000,unit='L/h',
        observed_at='2026-07-06T10:15:00Z',provenance='measured',source='Synthetic meter reading')))
    quality=operation('crop','review_measurement',dict(observation_id=obs['id'],quality='checked',reason='Synthetic check',
        source='Fixture',request_id='check',expected_revision=None))
    available=operation('field','review_sprinkler_measurements',dict(calculation_id=saved['id']))
    assert available['observations'][0]['quality_review']['id']==quality['id']
    mapping=dict(observation_id=obs['id'],expected_review=quality['id'],target='nozzle',station_id='S1',mapping_note='Synthetic mapping',pressure_reference='not_applicable')
    report=operation('field','compare_sprinkler_lateral',dict(calculation_id=saved['id'],comparisons=[event(mapping)],
        dataset_kind='synthetic',selection_note='Complete synthetic sample'))
    assert report['included_reading_count']==1
    assert operation('field','list',{})['water_days']==[]


def test_catch_depth_is_not_reinterpreted_as_nozzle_discharge(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    mapping=reading(workflow,'depth',quantity='irrigation_depth',value=5,unit='mm')
    assert workflow.review_sprinkler_measurements('owner','field',calculation_id=saved['id'])['observations']==[]
    with pytest.raises(ValueError,match='reading quantity'):
        compare(workflow,saved,event(mapping))


def test_future_event_with_valid_duration_cannot_be_called_completed(setup):  # noqa: F811
    workflow,_=setup
    saved=plan(workflow)
    with pytest.raises(ValueError,match='must have ended'):
        compare(workflow,saved,event(reading(workflow,'flow'),started_at='2099-07-06T10:00:00Z',ended_at='2099-07-06T11:00:00Z'))
