"""No-model farm-wide resource planning with explicit pools and work requirements."""

import hashlib
import json
from copy import deepcopy
from datetime import date

import streamlit as st

from agro_model.resources import KINDS


def _reset_result():
    st.session_state.pop("resource_result", None)


def _save_pool(prefix):
    row = {key: st.session_state.get(prefix+key) for key in ("id", "kind", "available", "source")}
    row["unit"] = st.session_state.get(prefix+"currency") if row["kind"] == "cash" else KINDS.get(row["kind"])
    if not isinstance(row["id"], str) or not row["id"].strip():
        st.session_state.resource_error = "Enter a resource name."
        return
    row["id"] = row["id"].strip()
    previous = st.session_state.resource_pools.get(row["id"])
    if previous and (previous["kind"], previous["unit"]) != (row["kind"], row["unit"]):
        for job in st.session_state.resource_jobs.values():
            if row["id"] in job["requirements"]:
                job["requirements"][row["id"]] = None
        for key in list(st.session_state):
            if key.startswith("resource_job_") and key.endswith("_amount_"+row["id"]):
                del st.session_state[key]
        st.session_state.resource_notice = "Resource type or unit changed. Re-enter its per-block requirement for each work entry."
    st.session_state.resource_pools[row["id"]] = row
    _reset_result()


def _save_job(prefix, resource_ids):
    row = {key: st.session_state.get(prefix+key) for key in ("id", "unit_id", "cycle_id", "title", "block_label",
            "priority", "requested_blocks", "minimum_blocks", "source")}
    if not isinstance(row["id"], str) or not row["id"].strip():
        st.session_state.resource_error = "Enter a work entry name."
        return
    row["id"] = row["id"].strip()
    row["requirements"] = {key: st.session_state.get(prefix+"amount_"+key) for key in resource_ids}
    previous = st.session_state.resource_jobs.get(row["id"], {})
    if "irrigation_design" in previous:
        row["irrigation_design"] = previous["irrigation_design"]
    st.session_state.resource_jobs[row["id"]] = row
    _reset_result()


def _remove(collection, selector):
    st.session_state[collection].pop(st.session_state.get(selector), None)
    st.session_state[selector] = None
    _reset_result()


def _detach_design(key, prefix):
    st.session_state.resource_jobs[key].pop("irrigation_design", None)
    st.session_state.resource_jobs[key]["source"] = ""
    st.session_state[prefix+"source"] = ""
    st.session_state.resource_notice = "Design link removed from this draft entry. Supply an independent source before saving manual quantities."
    _reset_result()


def _evaluate(call, timezone, save):
    start, end = st.session_state.get("resource_start"), st.session_state.get("resource_end")
    inputs = dict(title=st.session_state.get("resource_title"), source=st.session_state.get("resource_source"),
        period=dict(start=start.isoformat() if start else None, end=end.isoformat() if end else None, timezone=timezone),
        resources=list(st.session_state.resource_pools.values()), jobs=list(st.session_state.resource_jobs.values()))
    request = dict(data=inputs)
    operation = "evaluate"
    if save:
        template = st.session_state.get("resource_new_work_template")
        if template:
            operation = "save_new_work"
            request.update(template_id=template["id"], reason=st.session_state.get("resource_new_work_reason"))
        else:
            operation = "save"
            request["parent_id"] = st.session_state.get("resource_parent")
            previous = st.session_state.get("resource_new_work_saved")
            if (previous and previous["id"] == request["parent_id"] and previous["inputs"] == inputs):
                st.session_state.resource_notice = "This work scenario is already saved. Edit its inputs to create an alternative."
                return
        request["request_id"] = "web-resources-"+hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    try:
        result = call(operation, request)
        st.session_state.resource_result = result
        if save:
            st.session_state.resource_notice = "Resource scenario saved. No resources reserved and no work recorded."
            if operation == "save_new_work":
                st.session_state.resource_parent = result["id"]
                st.session_state.resource_saved = result["id"]
                st.session_state.resource_new_work_saved = dict(id=result["id"], inputs=deepcopy(inputs))
                st.session_state.pop("resource_new_work_template", None)
                st.session_state.pop("resource_new_work_reason", None)
                st.session_state.resource_notice = "Separate work scenario saved. Further draft edits create alternatives for this new work; its resources are not yet committed."
    except (ValueError, TypeError) as exc:
        _reset_result()
        st.session_state.resource_error = str(exc)


def _load(saved, separate_work=False):
    # Clear editor widgets before loading a different scenario's values.
    for key in list(st.session_state):
        if key.startswith(("resource_pool_", "resource_job_", "resource_design_")):
            del st.session_state[key]
    data = deepcopy(saved["inputs"])
    st.session_state.resource_pools = {r["id"]: r for r in data["resources"]}
    st.session_state.resource_jobs = {j["id"]: j for j in data["jobs"]}
    for key in ("title", "source"):
        st.session_state["resource_"+key] = data[key]
    for key in ("start", "end"):
        value = date.fromisoformat(data["period"][key])
        st.session_state["resource_"+key] = value if value.year >= 1000 else None
    st.session_state.resource_parent = None if separate_work else saved["id"]
    st.session_state.pop("resource_new_work_template", None)
    st.session_state.pop("resource_new_work_reason", None)
    st.session_state.pop("resource_new_work_saved", None)
    if separate_work:
        st.session_state.resource_new_work_template = dict(id=saved["id"], title=data["title"])
        st.session_state.resource_start = None
        st.session_state.resource_end = None
        st.session_state.resource_source = ""
        st.session_state.resource_notice = "New work draft copied. Enter its dates, source and reason, and review the copied units, cycles, resource limits and requirements."
    _reset_result()


def _blank():
    # A blank draft is independent work; preserve saved scenarios and the farm ledger.
    for key in list(st.session_state):
        if key.startswith(("resource_", "timing_")) and key not in ("resource_farm", "resource_scope"):
            del st.session_state[key]
    st.session_state.resource_notice = "Blank independent work draft started. Saved scenarios and ledger history are preserved."


def _result(result):
    st.caption(result["method"])
    st.write("All requested blocks allocated", result["all_requested_blocks_allocated"])
    st.dataframe([{key: value for key, value in row.items() if key != "assigned"} for row in result["allocations"]])
    st.dataframe(result["resources"])
    with st.expander("Resource use by work entry"):
        st.dataframe([dict(job=row["job_id"], resource=key, amount=amount) for row in result["allocations"] for key, amount in row["assigned"].items()])
    for limit in result["limits"]:
        st.caption(limit)


def render_farm_resources(brain, user, channel="web"):
    st.header("Farm resources")
    farms = {f["id"]: f for f in brain.list_farms(channel, user)}
    if not farms:
        st.info("Create a farm and production units before planning shared resources.")
        return
    selected_farm = st.session_state.get("resource_farm")
    st.session_state.resource_farm = selected_farm if selected_farm in farms else None
    farm_id = st.selectbox("Farm to plan", list(farms), index=None, key="resource_farm", format_func=lambda key: farms[key]["name"])
    if st.session_state.get("resource_scope") != farm_id:
        for key in list(st.session_state):
            if key.startswith(("resource_", "timing_")) and key != "resource_farm":
                del st.session_state[key]
        st.session_state.resource_scope = farm_id
    if not farm_id:
        return
    farm = farms[farm_id]
    if not farm["timezone"]:
        st.info("This farm needs a known timezone before resource periods can be evaluated.")
        return
    st.session_state.setdefault("resource_pools", {})
    st.session_state.setdefault("resource_jobs", {})
    units = {u["id"]: u for u in brain.list_units(channel, user) if u["farm_id"] == farm_id}

    def call(operation, data):
        return brain.farm_resources(channel, user, farm_id, operation, data)

    if notice := st.session_state.pop("resource_notice", None):
        st.success(notice)
    if error := st.session_state.pop("resource_error", None):
        st.error(error)
    from .resource_ledger_ui import render_resource_ledger
    render_resource_ledger(call, farm)
    from .equipment_calendar_ui import render_equipment_calendar
    render_equipment_calendar(call, farm)
    st.caption("Enter shared availability and complete resource needs for each work block. Lower priority numbers go first. "
               "These are alternative plans for the whole period, not pump schedules or reservations.")
    st.button("Start blank work plan", on_click=_blank)
    if template := st.session_state.get("resource_new_work_template"):
        st.info(f"Drafting separate work from {template['title']} · {template['id'][:8]}. Prior schedules and actual usage are not copied.")
        st.text_input("Why this is separate work", key="resource_new_work_reason")
    pools, jobs = st.session_state.resource_pools, st.session_state.resource_jobs
    with st.expander("Shared resource pools", expanded=not pools):
        st.caption("Name each physical pool once. Enter availability after commitments outside this plan; "
                   "do not count the same water or money in multiple pools. Amounts use decimal text.")
        selected = st.selectbox("Resource to add or edit", [None, *pools], key="resource_pool_choice", format_func=lambda key: key or "New resource")
        old = pools.get(selected, {})
        prefix = f"resource_pool_{selected or 'new'}_"
        kind = st.selectbox("Resource type", list(KINDS), index=list(KINDS).index(old["kind"]) if old.get("kind") in KINDS else None, key=prefix+"kind")
        with st.form(prefix+"form"):
            st.text_input("Resource name", value=old.get("id", ""), disabled=selected is not None, key=prefix+"id")
            if kind == "cash":
                st.text_input("Currency code", value=old.get("unit", ""), key=prefix+"currency")
            else:
                st.caption("Unit: "+str(KINDS.get(kind) or "select a resource type"))
            st.text_input("Available for the period", value=old.get("available", ""), key=prefix+"available")
            st.text_input("Availability source and outside commitments", value=old.get("source", ""), key=prefix+"source")
            st.form_submit_button("Keep resource in draft", on_click=_save_pool, args=(prefix,))
        if selected:
            st.button("Remove resource from draft", on_click=_remove, args=("resource_pools", "resource_pool_choice"))
        if pools:
            st.dataframe(list(pools.values()))
    if pools and units:
        from .resource_design_ui import render_design_job

        render_design_job(call, pools)
        with st.expander("Work and priorities", expanded=not jobs):
            st.caption("Define a block small enough to allocate meaningfully. Use a minimum equal to the request for all-or-nothing work. "
                       "Declare each resource per block, including explicit zero when unused; unknown needs cannot be evaluated.")
            selected = st.selectbox("Work entry to add or edit", [None, *jobs], key="resource_job_choice", format_func=lambda key: key or "New work entry")
            old = jobs.get(selected, {})
            link = old.get("irrigation_design")
            prefix = f"resource_job_{selected or 'new'}_"
            unit_id = st.selectbox("Work production unit", list(units), index=list(units).index(old["unit_id"]) if old.get("unit_id") in units else None,
                                   key=prefix+"unit_id", format_func=lambda key: units[key]["name"], disabled=bool(link))
            cycles = {c["id"]: c for c in brain.list_cycles(channel, user, unit_id)} if unit_id else {}
            if st.session_state.get(prefix+"cycle_id") not in (None, *cycles):
                st.session_state[prefix+"cycle_id"] = None
            with st.form(prefix+"form"):
                choices = [None, *cycles]
                st.selectbox("Crop cycle (optional)", choices, index=choices.index(old.get("cycle_id")) if old.get("cycle_id") in choices else 0,
                    key=prefix+"cycle_id", format_func=lambda key: "No crop cycle" if key is None else f"{cycles[key]['crop']} · {cycles[key]['planted_on']}", disabled=bool(link))
                for key, label in (("id", "Work entry name"), ("title", "Work description"), ("block_label", "What one block means")):
                    st.text_input(label, value=old.get(key, ""), disabled=key == "id" and selected is not None, key=prefix+key)
                for key, label in (("priority", "Priority (unique number, lower first)"), ("requested_blocks", "Blocks requested"), ("minimum_blocks", "Minimum useful blocks")):
                    st.number_input(label, min_value=1, max_value=1_000_000, value=old.get(key), step=1, key=prefix+key,
                                    disabled=bool(link) and key in ("requested_blocks", "minimum_blocks"))
                for resource_id, pool in pools.items():
                    st.text_input(f"{resource_id} per block ({pool['unit']})", value=old.get("requirements", {}).get(resource_id, ""), key=prefix+"amount_"+resource_id,
                                  disabled=bool(link) and resource_id in (link["water_resource_id"], link["pump_resource_id"]))
                st.text_input("Work requirements source", value=old.get("source", ""), key=prefix+"source")
                st.form_submit_button("Keep work in draft", on_click=_save_job, args=(prefix, list(pools)))
            if selected:
                if link:
                    st.caption(f"Linked full irrigation event on {link['day']} from calculation {link['calculation_id']}. Re-import to change its date or design.")
                    st.button("Convert to manual work entry", on_click=_detach_design, args=(selected, prefix))
                st.button("Remove work from draft", on_click=_remove, args=("resource_jobs", "resource_job_choice"))
            if jobs:
                st.dataframe([{key: value for key, value in job.items() if key != "requirements"} for job in jobs.values()])
    with st.form("resource_plan_form"):
        st.text_input("Scenario title", key="resource_title")
        st.date_input("Period starts", value=None, min_value=date(1000, 1, 1), max_value=date.max, key="resource_start")
        st.date_input("Period ends", value=None, min_value=date(1000, 1, 1), max_value=date.max, key="resource_end")
        st.caption("Farm timezone: "+farm["timezone"])
        st.text_input("Scenario source and assumptions", key="resource_source")
        st.form_submit_button("Evaluate draft allocation", on_click=_evaluate, args=(call, farm["timezone"], False))
        st.form_submit_button("Save resource scenario", on_click=_evaluate, args=(call, farm["timezone"], True))
    if draft := st.session_state.get("resource_result"):
        st.subheader("Evaluated scenario snapshot")
        if draft.get("snapshot", {}).get("parent_zone_overlap"):
            st.warning("This plan includes a parent field and one of its zones. Check that their work and quantities are distinct before committing resources.")
        st.caption("This result reflects the submitted inputs below. Submit again after editing the draft.")
        _result(draft["result"])
        st.download_button("Download evaluated scenario", json.dumps(draft, indent=2), "resource-scenario.json", "application/json")
    saved = {p["id"]: p for p in call("list", {})["plans"]}
    if saved:
        st.subheader("Saved resource scenarios")
        selected_saved = st.session_state.get("resource_saved")
        st.session_state.resource_saved = selected_saved if selected_saved in saved else None
        st.session_state.resource_compare = [key for key in st.session_state.get("resource_compare", []) if key in saved]
        chosen = st.selectbox("Saved resource scenario", list(saved), index=None, key="resource_saved", format_func=lambda key:
            f"{saved[key]['title']} · {key[:8]} · " + ("timing not checked" if saved[key]["timing_checks_pass"] is None else
                                                      "timing checks pass" if saved[key]["timing_checks_pass"] else "timing checks fail"))
        if chosen:
            record = call("get", dict(plan_id=chosen))
            if record["snapshot"].get("parent_zone_overlap"):
                st.warning("This scenario contains both whole-field and zone work. Review possible duplicate quantities before committing resources.")
            if origin := record.get("new_work"):
                st.caption(f"Separate work based on {origin['template']['title']} · {origin['template']['id'][:8]}: {origin['reason']}")
                st.caption(origin["note"])
            if record["review"]["needs_review"]:
                st.warning(record["review"]["note"])
                st.write(record["review"]["cycle_changes"])
                if record["review"].get("irrigation_designs"):
                    st.write(record["review"]["irrigation_designs"])
            _result(record["result"])
            if record["snapshot"].get("irrigation_designs"):
                from .resource_design_ui import render_evidence
                with st.expander("Irrigation source calculations"):
                    for evidence in record["snapshot"]["irrigation_designs"]:
                        render_evidence(evidence)
            from .resource_timing_ui import render_resource_timing

            render_resource_timing(call, record)
            from .resource_outcome_ui import render_resource_outcomes

            render_resource_outcomes(call, record)
            st.button("Use as starting scenario", on_click=_load, args=(record,))
            st.caption("Use as starting scenario keeps alternatives in the same work family. Start separate work only for another operation or round of work.")
            st.button("Start separate work from this scenario", on_click=_load, args=(record, True))
            st.download_button("Download saved resource scenario", json.dumps(record, indent=2), f"resources-{chosen}.json", "application/json")
        selected = st.multiselect("Compare resource scenarios", list(saved), max_selections=10, key="resource_compare", format_func=lambda key: f"{saved[key]['title']} · {key[:8]}")
        if len(selected) >= 2:
            try:
                compared = call("compare", dict(plan_ids=selected))
                st.caption(compared["note"])
                st.dataframe([dict(scenario_title=r["inputs"]["title"], plan_id=r["id"],
                    scheduling_status=r.get("schedule_result", {}).get("status", "not proposed"),
                    equipment_calendar_checked="equipment_schedule" in r,
                    water_delivery_checked=r.get("timing_result", {}).get("water_delivery_checked", False),
                    equipment_calendar_changed=r.get("equipment_schedule_review", {}).get("changed"),
                    timing_checks=r.get("timing_result", {}).get("declared_checks_pass"),
                    timing_status="checked" if "timing_result" in r else "not checked") for r in compared["scenarios"]])
                st.dataframe([dict(scenario=r["inputs"]["title"], plan_id=r["id"], **pool) for r in compared["scenarios"] for pool in r["result"]["resources"]])
                st.download_button("Download resource comparison", json.dumps(compared, indent=2), "resource-comparison.json", "application/json")
            except ValueError as exc:
                st.error(str(exc))
