"""Reviewed hydraulic readings mapped explicitly to a saved sprinkler scenario."""

import json

import streamlit as st

from agronaut_agent.sprinkler_diagnostics import render_sprinkler_comparison

P='field_sprinkler_compare_'


def _draft(saved_id, selected, readings):
    events=[]
    for index,identifiers in enumerate(selected):
        prefix=P+str(index)+'_'
        event={key:st.session_state.get(prefix+key) for key in (
            'event_id','started_at','ended_at','stable_operation','conditions_note','representation_note',
            'independence','water_density_kg_m3','density_source')}
        event['mappings']=[]
        for identifier in identifiers:
            p=prefix+identifier+'_'
            reading=readings[identifier]
            event['mappings'].append(dict(observation_id=identifier,expected_review=reading['quality_review']['id'] if reading['quality_review'] else None,
                target=st.session_state.get(p+'target'),station_id=st.session_state.get(p+'station'),
                mapping_note=st.session_state.get(p+'note'),pressure_reference=st.session_state.get(p+'reference')
                    if reading['quantity']=='pressure' else 'not_applicable'))
        events.append(event)
    return dict(calculation_id=saved_id,dataset_kind=st.session_state.get(P+'kind'),
                selection_note=st.session_state.get(P+'note'),comparisons=events)


def _compare(call,saved_id,cycle_id,selected,displayed_readings):
    st.session_state.pop(P+'report',None)
    try:
        if st.session_state.get('field_plan')!=saved_id or st.session_state.get('field_cycle')!=cycle_id:
            raise ValueError('The calculation or crop cycle changed; review this comparison again')
        request=_draft(saved_id,selected,displayed_readings)
        st.session_state[P+'report']=dict(request=request,result=call('compare_sprinkler_lateral',request))
    except (ValueError,TypeError) as exc:
        st.session_state[P+'error']=str(exc)


def render_sprinkler_diagnostics(call,saved,farm,cycle):
    scope=(saved['id'],saved['cycle_id'],farm['timezone'],json.dumps(cycle,sort_keys=True))
    if st.session_state.get(P+'scope')!=scope:
        for key in list(st.session_state):
            if key.startswith(P):
                del st.session_state[key]
        st.session_state[P+'scope']=scope
    if saved['task']!='sprinkler_lateral':
        return
    with st.expander('Compare sprinkler predictions with pressure and flow readings'):
        if error:=st.session_state.pop(P+'error',None):
            st.error(error)
        if saved['result']['result']['prediction'] is None:
            st.info('Save a solved sprinkler-lateral scenario before comparing readings')
            return
        try:
            review=call('review_sprinkler_measurements',dict(calculation_id=saved['id']))
        except ValueError as exc:
            st.error(str(exc))
            return
        readings={r['id']:r for r in review['observations']}
        if not readings:
            st.info('Record pressure (kPa) or flow (L/h) measurements in Crop records for this unit and crop cycle first')
            return
        st.caption('Map existing readings to the inlet, riser base or nozzle where they were taken. '
                   'Only readings marked checked from events declared held out enter aggregate metrics. '
                   'Other readings remain visible. Catch-can depth is not nozzle flow.')
        st.dataframe(review['targets'],hide_index=True)
        count=st.number_input('Number of separate comparison events',min_value=1,max_value=100,value=1,key=P+'count')
        selected=[]
        for index in range(count):
            key=P+str(index)+'_readings'
            if key in st.session_state:
                st.session_state[key]=[i for i in st.session_state[key] if i in readings]
            selected.append(st.multiselect(f'Readings in event {index+1}',list(readings),key=key,
                format_func=lambda i:f"{readings[i]['observed_at']} · {readings[i]['value']:g} {readings[i]['unit']} · {readings[i]['effective_quality']} · {i}"))
        # Keep draft changes reactive so a report cannot outlive its input selection.
        with st.container():
            st.selectbox('Sprinkler comparison dataset kind',['synthetic','field'],index=None,key=P+'kind')
            st.text_area('Reading selection rationale',key=P+'note')
            for index,identifiers in enumerate(selected):
                prefix=P+str(index)+'_'
                st.caption(f'Event {index+1}')
                st.text_input('Event identifier',key=prefix+'event_id')
                st.text_input('Event start (ISO timestamp with offset)',key=prefix+'started_at')
                st.text_input('Event end (ISO timestamp with offset)',key=prefix+'ended_at')
                st.checkbox('This event maintained steady operation',key=prefix+'stable_operation')
                st.text_input('Matching source settings and operating conditions',key=prefix+'conditions_note')
                st.text_input('Matching geometry, equipment and measurement method',key=prefix+'representation_note')
                st.selectbox('Use in model development',['held_out','used_for_calibration','unknown'],index=None,key=prefix+'independence')
                st.number_input('Water density for pressure conversion (kg/m³; blank if unknown)',value=None,
                                min_value=500.0,max_value=2000.0,key=prefix+'water_density_kg_m3')
                st.text_input('Density source or reason it is unknown',key=prefix+'density_source')
                for identifier in identifiers:
                    r=readings[identifier]
                    p=prefix+identifier+'_'
                    st.caption(f"{identifier}: {r['quantity']} · {r['provenance']} · {r['effective_quality']} · {r['source']}")
                    st.selectbox('Measured location type',['inlet','base','nozzle','unmatched'],index=None,key=p+'target')
                    st.selectbox('Modeled station (none for inlet or unmatched)',[None,*[s['identifier'] for s in saved['inputs']['stations']]],
                                 index=None,key=p+'station')
                    st.text_input('Location mapping evidence or reason unmatched',key=p+'note')
                    if r['quantity']=='pressure':
                        st.selectbox('Pressure reference',['gauge_at_target','unknown'],index=None,key=p+'reference')
            st.button('Compare sprinkler readings without changing records',on_click=_compare,
                args=(call,saved['id'],saved['cycle_id'],selected,{i:readings[i] for ids in selected for i in ids}),key=P+'run')
        request=_draft(saved['id'],selected,readings)
        cached=st.session_state.get(P+'report')
        if cached and cached['request']==request:
            report=cached['result']
            st.text(render_sprinkler_comparison(report))
            st.dataframe([dict(event=row['comparison_input']['event_id'],observation=pair['observation']['id'],
                target=pair['mapping']['target'],station=pair['mapping']['station_id'],quality=pair['observation']['effective_quality'],
                predicted=pair['predicted'],recorded=pair['recorded'],unit=pair['unit'],error=pair['error'],
                status=pair['status'],included=pair['included_in_metrics'],exclusions=', '.join(pair['exclusion_reasons']))
                for row in report['comparisons'] for pair in row['pairs']],hide_index=True)
            st.caption('Modeled targets without a mapped reading')
            st.dataframe([dict(event=row['comparison_input']['event_id'],**target)
                          for row in report['comparisons'] for target in row['unmapped_targets']],hide_index=True)
            st.download_button('Download sprinkler comparison and evidence',json.dumps(report,indent=2),
                               file_name=f"sprinkler-comparison-{saved['id']}.json",mime='application/json',key=P+'download')
        elif cached:
            st.caption('The draft or a measurement review changed. Run the comparison again to refresh this report.')
