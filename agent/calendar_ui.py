"""Explicit crop-work planning and resolution; no automatic agronomic dates or actions."""

from datetime import date

import streamlit as st

from agronaut_agent.crop_calendar import ACTIVITIES


def _plan(call, cycle_id, prior_id, prefix, triggers):
    from .crop_ui import _request_id

    data = {key: st.session_state.get(prefix+key) for key in ("entry", "title", "activity", "location", "notes", "source", "priority")}
    basis = st.session_state.get(prefix+"basis")
    due = dict(basis=basis)
    if basis == "planting":
        due.update({key: st.session_state.get(prefix+key) for key in ("start_offset_days", "end_offset_days")})
    else:
        for key in ("start", "end"):
            value = st.session_state.get(prefix+key)
            due[key] = value.isoformat() if value else None
    data["due"] = due
    selected = st.session_state.get(prefix+'trigger')
    if selected and selected != 'none':
        if selected not in triggers:
            st.session_state.crop_error = 'The selected task source is no longer available. Choose a current source or explicitly remove the link.'
            return
        candidate = triggers[selected]
        data['trigger'] = {key: candidate[key] for key in ('kind', 'id', 'review_revision_id') if key in candidate}
        data['trigger']['reason'] = st.session_state.get(prefix+'trigger_reason')
    else:
        data['trigger'] = None
    request = dict(cycle_id=cycle_id, data=data, supersedes_id=prior_id)
    try:
        call("plan_task", dict(request, request_id=_request_id("plan_task", request)))
        st.session_state.crop_notice = "Work plan saved; no completed activity has been recorded."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _resolve(call, record_id, prefix, candidates):
    from .crop_ui import _request_id

    status = st.session_state.get(prefix+"status")
    request = dict(record_id=record_id, status=status, reason=st.session_state.get(prefix+"reason"),
                   source=st.session_state.get(prefix+"source"))
    if status == "completed":
        day = st.session_state.get(prefix+"completed_on")
        request["completed_on"] = day.isoformat() if day else None
        selected = st.session_state.get(prefix+"evidence")
        if selected and selected not in candidates:
            st.session_state.crop_error = "The selected activity record is no longer available. Review the completion evidence."
            return
        request["completion_record"] = {key: candidates[selected][key] for key in ("kind", "id")} if selected else None
    try:
        call("resolve_task", dict(request, request_id=_request_id("resolve_task", request)))
        st.session_state.crop_notice = "Task status saved; quantity records are unchanged."
    except (ValueError, TypeError, KeyError) as exc:
        st.session_state.crop_error = str(exc)


def render_calendar_summary(calendar):
    st.caption(calendar["note"])
    if not calendar["tasks"]:
        st.info("No work tasks planned for this crop cycle.")
        return
    st.write("Task status", calendar["counts"])
    if calendar["counts"]["needs_review"]:
        st.warning("Review flagged tasks for changed planting assumptions, cycle closure, trigger records or completion evidence.")
    st.dataframe([dict(entry=t["entry"], title=t["title"], activity=t["activity"], priority=t["priority"], status=t["status"],
        timing=t["timing"], due_start=(t["effective_window"] or {}).get("start"), due_end=(t["effective_window"] or {}).get("end"),
        originally_planned_start=t["original_window"]["start"], originally_planned_end=t["original_window"]["end"],
        needs_review=t["needs_review"], review_reasons="; ".join(t["review_reasons"]), completed_on=t["completed_on"], completion_timing=t["completion_timing"],
        evidence=t["completion_evidence_state"], trigger=t.get('trigger'), trigger_state=t.get('trigger_review', {}).get('state', 'not_linked'),
        location=t["location"], source=t["source"]) for t in calendar["tasks"]])


def render_crop_calendar(call, journal, cycle_id, today):
    rows = [r for r in journal["records"] if r["kind"] == "calendar"]
    current = {r["id"]: r for r in rows if not r["superseded"]}
    pending = {key: row for key, row in current.items() if row["data"]["status"] == "planned"}
    with st.expander("Crop work calendar"):
        render_calendar_summary(journal["calendar"])
        st.caption("Supply your work window and source. Calendar entries can cover preparation and cleanup. "
                   "They do not schedule notifications, control equipment, or choose treatment dates.")
        selector = f"crop_task_plan_{cycle_id}"
        if st.session_state.get(selector) not in (None, *pending):
            st.session_state[selector] = None
        prior_id = st.selectbox("Task plan to create or revise", [None, *pending], key=selector,
            format_func=lambda key: "New task" if key is None else f"{pending[key]['data']['entry']} · {pending[key]['data']['title']}")
        old = pending[prior_id]["data"] if prior_id else {}
        prefix = f"crop_task_{cycle_id}_{prior_id or 'new'}_"
        triggers = {r['kind']+':'+r['id']: r for r in journal['calendar']['trigger_candidates']}
        old_trigger = old.get('trigger')
        retained = old_trigger['kind']+':'+old_trigger['id'] if old_trigger else 'none'
        options = ['none', *triggers]
        # Retain a changed/voided selection so a stale form cannot silently unlink it.
        if retained not in options:
            options.append(retained)
        if prefix+'trigger' in st.session_state and st.session_state[prefix+'trigger'] not in options:
            options.append(st.session_state[prefix+'trigger'])
        def trigger_label(key):
            if key == 'none':
                return 'No linked source record'
            if key not in triggers:
                return 'Unavailable source; replace or remove explicitly · '+key
            r = triggers[key]
            return f"{r['when']} · {r['label']} · {r.get('summary', '')} · {r['provenance']} · {r['quality'] or 'quality not rated'} · {key} · review {r.get('review_revision_id') or 'none'}"
        due = old.get("due", {})
        basis = st.selectbox("Work window basis", ["date", "planting"],
            index=["date", "planting"].index(due["basis"]) if due else None, key=prefix+"basis",
            format_func=lambda value: "Calendar dates" if value == "date" else "Days relative to planting")
        with st.form(prefix+"form"):
            st.text_input("Task entry ID", value=old.get("entry", ""), disabled=prior_id is not None, key=prefix+"entry")
            st.text_input("Task title", value=old.get("title", ""), key=prefix+"title")
            st.selectbox("Task activity", list(ACTIVITIES), index=list(ACTIVITIES).index(old["activity"]) if old else None, key=prefix+"activity")
            if basis == "planting":
                st.caption("Day 0 is this cycle's planting date. Negative offsets are before planting. "
                           "Planting corrections flag the plan for review; saving a revision accepts the current crop/date assumptions.")
                for key, label in (("start_offset_days", "Window starts, days from planting"), ("end_offset_days", "Window ends, days from planting")):
                    st.number_input(label, min_value=-365, max_value=3650, value=due.get(key), step=1, key=prefix+key)
            else:
                for key, label in (("start", "Work window starts"), ("end", "Work window ends")):
                    saved_date = date.fromisoformat(due[key]) if due.get(key) else None
                    if saved_date is not None and saved_date.year < 1000:
                        st.caption("The saved date is outside the date picker's supported range. Enter a replacement date to revise this plan.")
                        saved_date = None
                    st.date_input(label, value=saved_date,
                                  min_value=date(1000, 1, 1), max_value=date.max, key=prefix+key)
            st.text_input("Work location or extent", value=old.get("location", ""), key=prefix+"location")
            st.text_area("Task instructions or notes", value=old.get("notes", ""), key=prefix+"notes")
            priorities = ["low", "normal", "high"]
            st.selectbox("Task priority", priorities, index=priorities.index(old["priority"]) if old else None, key=prefix+"priority")
            st.text_input("Work plan source", key=prefix+"source")
            st.selectbox('Record that prompted this task (optional)', options,
                         index=options.index(st.session_state.get(prefix+'trigger', retained)),
                         key=prefix+'trigger', format_func=trigger_label)
            st.text_area('Why this source prompted the task', value=old_trigger['reason'] if old_trigger else '', key=prefix+'trigger_reason')
            st.caption('The link records your explanation, not a confirmed cause or treatment recommendation. Saving a revision adopts the selected current source and quality review.')
            st.form_submit_button("Save work plan", on_click=_plan, args=(call, cycle_id, prior_id, prefix, triggers))
        if not current:
            return
        selector = f"crop_task_resolution_{cycle_id}"
        if st.session_state.get(selector) not in current:
            st.session_state[selector] = None
        record_id = st.selectbox("Task to complete, cancel or reopen", list(current), index=None, key=selector,
            format_func=lambda key: f"{current[key]['data']['entry']} · {current[key]['data']['title']} · {current[key]['data']['status']}")
        if record_id:
            row = current[record_id]
            st.write("Task instructions", row["data"]["notes"])
            if resolution := row["data"].get("resolution"):
                st.write("Previous status update", dict(note=resolution["reason"], source=resolution["source"],
                                                        completed_on=resolution["completed_on"]))
            prefix = f"crop_task_resolution_{record_id}_"
            status = st.selectbox("New task status", ["completed", "cancelled", "planned"], index=None, key=prefix+"status",
                format_func=lambda value: "Reopen as planned" if value == "planned" else value.capitalize())
            candidates = {r["id"]: r for r in journal["calendar"]["completion_candidates"] if r["activity"] == row["data"]["activity"]}
            if st.session_state.get(prefix+"evidence") not in (None, *candidates):
                st.session_state[prefix+"evidence"] = None
                st.warning("The selected activity record changed. Review the completion evidence before saving.")
            with st.form(prefix+"form"):
                if status == "completed":
                    st.date_input("Actual task completion date", value=None, min_value=date(1000, 1, 1), max_value=today, key=prefix+"completed_on")
                    st.selectbox("Link an existing activity record (optional)", [None, *candidates], key=prefix+"evidence",
                        format_func=lambda key: "Operator report only" if key is None else
                        f"{candidates[key]['day']} · {candidates[key]['label']} · {key[:8]}")
                    st.caption("A link must match the activity and actual date. Marking a task complete does not record applied water, fertilizer or harvest quantities.")
                st.text_input("Task resolution note or correction reason", key=prefix+"reason")
                st.text_input("Task resolution source", key=prefix+"source")
                st.form_submit_button("Save task status", on_click=_resolve, args=(call, record_id, prefix, candidates))
        st.caption("Task revision history remains in the crop journal download.")
