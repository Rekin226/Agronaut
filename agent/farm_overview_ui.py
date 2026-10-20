"""Read-only cross-unit crop and work overview, without a language model."""

import json

import streamlit as st


def render_farm_overview(brain, user, channel="web"):
    st.subheader("Farm overview")
    report = brain.farm_overview(channel, user)
    st.caption(report["note"])
    if not report["farms"]:
        st.info("No farms recorded yet. Add a production unit to begin.")
        return
    st.caption(f"{report['farm_count']} farms · {report['unit_count']} production units · Snapshot {report['generated_at']}")
    for section in report["farms"]:
        farm = section["farm"]
        st.subheader(farm["name"])
        st.caption(f"Local date: {section['local_date'] or 'unknown'} · Timezone: {farm['timezone'] or 'unknown'}")
        if section["issue"]:
            st.warning(section["issue"])
        if not section["units"]:
            st.info("No production units recorded for this farm.")
        equipment = section['equipment']
        counts = equipment['counts']
        st.write('Equipment reservations')
        st.caption(f"{counts['active']} active bookings · Today: {counts['today'] if counts['today'] is not None else 'unknown'} · "
                   f"{counts['needs_review']} need review. Elapsed reservations do not prove completed work.")
        if equipment['attention']:
            rows = []
            for booking in equipment['attention']:
                for interval in booking['intervals']:
                    rows.append(dict(title=booking['title'], equipment=interval['equipment_name'],
                        equipment_key=interval['equipment_key'], timing=interval['timing'],
                        start=interval['local_start'] or interval['start'], end=interval['local_end'] or interval['end'],
                        today=interval['today'], needs_review=booking['review']['needs_review'],
                        review=booking['review'], source=booking['source'], booking_id=booking['booking_id'],
                        plan_id=booking['plan_id'], work_id=interval['work_id'], job_id=interval['job_id']))
            st.dataframe(rows)
        elif not counts['active']:
            st.info('No active equipment reservations recorded; unrecorded equipment use is unknown.')
        else:
            st.info('No recorded equipment reservations overlap today or require review. Other reservations are included in the download.')
        st.caption('Use Farm resources / Shared equipment calendar to inspect, replace or release bookings.')
        for item in section["units"]:
            unit, counts = item["unit"], item["cycle_counts"]
            with st.expander(f"{unit['name']} · {unit['production_system'] or 'unresolved'} · {counts['open']} open crop cycles", expanded=True):
                if unit.get("parent_unit_id"):
                    st.caption(f"Irrigation zone of {unit['parent_name']} · {unit['irrigation_method'] or 'method unknown'}")
                st.caption(f"Unit ID: {unit['id']}")
                if not item["cycles"]:
                    st.info("No crop cycles recorded. This does not establish whether the unit is currently growing crops.")
                    continue
                st.dataframe([dict(crop=c["cycle"]["crop"], planted_on=c["cycle"]["planted_on"],
                    status=c["status"], ended_on=c["lifecycle"]["ended_on"],
                    planned=c["task_counts"]["planned"] if c["task_counts"] else None,
                    due=c["task_counts"]["due"] if c["task_counts"] else None,
                    overdue=c["task_counts"]["overdue"] if c["task_counts"] else None,
                    needs_review=c["task_counts"]["needs_review"] if c["task_counts"] else None,
                    cycle_id=c["cycle"]["id"]) for c in item["cycles"]])
                attention = [dict(crop=c["cycle"]["crop"], cycle_id=c["cycle"]["id"], **task)
                             for c in item["cycles"] for task in c["attention"]]
                if attention:
                    st.write("Recorded work due or needing review")
                    st.dataframe(attention)
                elif section["issue"] is None:
                    st.info("No recorded tasks currently due or flagged for review; unrecorded work is unknown.")
                unknown = sum(c["unclassified_task_count"] for c in item["cycles"])
                if unknown:
                    st.write(f"{unknown} recorded tasks cannot be classified until the farm timezone is established.")
                st.caption("Use the production-unit selector and Crop records to inspect, plan or resolve work.")
    st.download_button("Download farm overview", json.dumps(report, indent=2, ensure_ascii=False),
                       file_name="farm-overview.json", mime="application/json")
