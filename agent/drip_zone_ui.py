"""Build a simultaneous zone from owned surveyed-lateral geometry snapshots."""

from copy import deepcopy

import streamlit as st

from agent.drip_lateral_ui import parse_inlet_curve

P = 'field_drip_zone_'
JUNCTION = {
    'common_hydraulic_junction': 'All selected laterals start at the same hydraulic junction and elevation datum',
    'all_active_laterals_included': 'Every simultaneously active lateral on this junction is included',
    'no_unmodeled_connection_losses': 'The supplied surveys and fitting coefficients include all connections; no distributed manifold losses are omitted',
    'steady_source_configuration': 'The source curve applies to steady operation with this whole zone active',
}

MANIFOLD = {
    'closed_tail': 'The manifold is closed immediately after the last takeoff',
    'surveyed_profile': 'The manifold survey includes every crest and sag; elevations are relative to its inlet',
    'all_active_laterals_included': 'Every simultaneously active lateral has a surveyed takeoff here',
    'steady_source_configuration': 'The source curve applies to steady operation with all these laterals active',
    'no_unmodeled_connections': 'There are no omitted outlets, loops, additional sources or connection losses',
}


def render_drip_zone_intake(active, farm, cycle, records, call):
    scope = (active['id'], farm['timezone'], (cycle['id'], cycle['crop'], cycle['planted_on']) if cycle else None)
    if st.session_state.get(P+'scope') != scope:
        for key in list(st.session_state):
            if key.startswith(P):
                del st.session_state[key]
        st.session_state[P+'scope'] = scope
    options = {row['id']: row for row in records['calculations']
               if row['task'] == 'drip_lateral' and row['cycle_id'] == (cycle['id'] if cycle else None)}
    with st.expander('Solve simultaneous drip laterals'):
        mode = st.radio('Distribution topology', ['Common junction', 'Surveyed manifold'], key=P+'topology')
        manifold = mode == 'Surveyed manifold'
        if st.session_state.get(P+'previous_topology') != mode:
            for key in list(st.session_state):
                if key.startswith((P+'supply_', P+'junction_', P+'review_', P+'manifold_', P+'takeoff_')):
                    del st.session_state[key]
            st.session_state[P+'previous_topology'] = mode
        if manifold:
            st.caption('One constant-diameter manifold with a distinct pressure at each takeoff. Provide its complete survey and a source curve at the manifold inlet versus TOTAL flow. Each lateral survey is relative to its own takeoff. Through-fitting K uses the incoming manifold velocity; branch entry K belongs in the lateral survey. Do not count a fitting twice.')
        else:
            st.caption('Reuse surveyed pipe/emitter geometry from saved lateral scenarios. Their separate inlet pressures and operating points are not added. '
                   'Provide a new source curve for TOTAL zone flow and confirm the common-junction topology. This does not model a distributed manifold.')
        minimum = 1 if manifold else 2
        if len(options) < minimum:
            st.info(f'Save at least {minimum} surveyed lateral scenarios in this unit and crop cycle first, or import a complete JSON input.')
            return None
        if P+'selected' in st.session_state:
            st.session_state[P+'selected'] = [key for key in st.session_state[P+'selected'] if key in options]
        selected = st.multiselect('Lateral geometry snapshots', list(options), max_selections=20, key=P+'selected',
                                  format_func=lambda key: f"Saved {options[key]['created_at']} · {key}")
        if st.session_state.get(P+'previous_selection') != selected:
            for key in list(st.session_state):
                if key.startswith((P+'supply_', P+'junction_', P+'review_')):
                    del st.session_state[key]
            st.session_state[P+'previous_selection'] = list(selected)
        snapshots = {key: call('get', dict(calculation_id=key)) for key in selected}
        for key, saved in snapshots.items():
            with st.expander('Review geometry '+key):
                st.json({name: saved['inputs'][name] for name in ('site', 'pipe', 'emitter', 'limits', 'conditions', 'stations')})
        with st.form(P+'form'):
            boundary = 'Manifold inlet' if manifold else 'Common junction'
            curve = st.text_area(boundary+' pressure–flow CSV: flow_lph,pressure_head_m', key=P+'supply_curve', max_chars=20000)
            static = st.number_input('Maximum static pressure at the '+boundary.lower()+' (m head)', min_value=0.0, value=None, key=P+'supply_static')
            maximum = st.number_input('Maximum available TOTAL zone flow (L/h)', min_value=0.0, value=None, key=P+'supply_flow')
            configuration = st.text_input('Source settings, upstream equipment and other loads', key=P+'supply_configuration')
            source = st.text_input('Evidence for the '+boundary.lower()+' pressure–total-flow curve and static limit', key=P+'supply_source')
            reference = st.checkbox('The curve is pressure at this '+boundary.lower()+', after upstream equipment, not raw pump head gain', key=P+'supply_reference')
            junction = {key: st.checkbox(label, key=P+'junction_'+key) for key, label in (MANIFOLD if manifold else JUNCTION).items()}
            junction['source'] = st.text_input('Evidence for this topology and active branch set', key=P+'junction_source')
            pipe, limits = {}, {}
            if manifold:
                for field, label in [('internal_diameter_mm', 'Manifold internal diameter (mm)'),
                                     ('roughness_mm', 'Manifold absolute roughness (mm)'),
                                     ('max_head_m', 'Manifold pipe pressure rating (m head)')]:
                    pipe[field] = st.number_input(label, min_value=0.0, value=None, key=P+'manifold_'+field)
                pipe['source'] = st.text_input('Manifold pipe evidence', key=P+'manifold_pipe_source')
                limits['viscosity_m2_s'] = st.number_input('Manifold water kinematic viscosity (m²/s)', min_value=0.0, value=None, format='%.9f', key=P+'manifold_viscosity')
                limits['max_velocity_m_s'] = st.number_input('Permitted manifold velocity (m/s)', min_value=0.0, value=None, key=P+'manifold_velocity')
                limits['source'] = st.text_input('Evidence for fluid properties and manifold velocity limit', key=P+'manifold_limit_source')
            branches, reviewed, takeoffs = [], [], []
            for key, saved in snapshots.items():
                st.caption('Lateral geometry '+key)
                identifier = st.text_input('Physical lateral identifier', key=P+key+'_identifier')
                capacity = st.number_input('Permitted flow for this lateral (L/h)', min_value=0.0, value=None, key=P+key+'_capacity')
                evidence = st.text_input('Evidence for this lateral identity, capacity and geometry reuse', key=P+key+'_evidence')
                reviewed.append(st.checkbox('I reviewed this survey and its emitter/pipe limits relative to its '+('own takeoff' if manifold else 'common junction'), key=P+'review_'+key))
                branch = {name: deepcopy(saved['inputs'][name]) for name in ('pipe', 'emitter', 'limits', 'conditions', 'stations')}
                branch.update(identifier=identifier, max_flow_lph=capacity,
                              source=f"Geometry snapshot {key}; declared reuse: {evidence}" if evidence.strip() else '')
                branches.append(branch)
                if manifold:
                    prefix = P+'takeoff_'+key+'_'
                    takeoffs.append(dict(lateral=branch,
                        distance_m=st.number_input('Takeoff cumulative manifold distance (m)', min_value=0.0, value=None, key=prefix+'distance'),
                        elevation_m=st.number_input('Takeoff elevation relative to manifold inlet (m)', value=None, key=prefix+'elevation'),
                        upstream_minor_k=st.number_input('Incoming manifold segment through-fitting K', min_value=0.0, value=None, key=prefix+'k'),
                        source=st.text_input('Takeoff survey and fitting evidence', key=prefix+'source')))
            if st.form_submit_button('Calculate and save simultaneous zone', key=P+'save'):
                try:
                    if len(branches) < minimum or not reference or not all(reviewed):
                        raise ValueError(f'Select and review at least {minimum} lateral geometries and confirm the pressure reference')
                    if any(saved['inputs']['site']['timezone'] != farm['timezone'] for saved in snapshots.values()):
                        raise ValueError('A geometry snapshot uses another timezone; review and save its corrected site first')
                    points = parse_inlet_curve(curve)
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
                    return None
                payload = dict(schema_version=1, site=dict(name=active['name'], timezone=farm['timezone']),
                    supply=dict(curve=points, max_static_head_m=static, max_flow_lph=maximum,
                        configuration=configuration, source=source,
                        head_reference='manifold_inlet_pressure' if manifold else 'lateral_inlet_pressure'))
                if manifold:
                    if any(t['distance_m'] is None for t in takeoffs):
                        st.error('Provide every takeoff distance')
                        return None
                    payload.update(pipe=pipe, limits=limits, conditions=junction,
                                   takeoffs=sorted(takeoffs, key=lambda t: t['distance_m']))
                else:
                    payload.update(laterals=branches, junction=junction)
                return dict(task='drip_manifold' if manifold else 'drip_zone', inputs=payload)
    return None
