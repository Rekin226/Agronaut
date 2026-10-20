"""Explicit timed work, availability and reservoir inputs for a saved allocation."""

import hashlib
import json

import streamlit as st

_ISSUES = {
    "work_interval_too_short": "Work does not fit its occupied time",
    "occupied_time_exceeds_budget": "Occupied time exceeds the period budget",
    "time_budget_exceeds_availability": "Time budget exceeds listed availability",
    "concurrent_capacity_exceeded": "Simultaneous work exceeds available units",
    "water_budget_exceeds_supplied_balance": "Water budget exceeds supplied balance above reserve",
    "storage_below_minimum": "Reservoir falls below its minimum",
    "storage_above_capacity": "Reservoir exceeds its capacity",
    "dependency_not_satisfied": "Work starts before its prerequisite or minimum waiting interval is complete",
    "water_delivery_capacity_exceeded": "Modeled withdrawals exceed the water outlet delivery limit",
}


def _changed():
    st.session_state.pop("timing_result", None)
    st.session_state.pop("timing_schedule_result", None)


def _store_row(bucket, prefix, fields, selected):
    data, concurrent = {}, {}
    for key, _, kind, _ in fields:
        value = st.session_state.get(prefix+key)
        if kind == "concurrent":
            concurrent[key.removeprefix("units_")] = value
        else:
            data[key] = value
    if bucket in ("work", "runs"):
        data["concurrent"] = concurrent
    if not isinstance(data["id"], str) or not data["id"].strip():
        st.session_state.timing_error = "Enter an interval name."
        return
    data["id"] = data["id"].strip()
    rows = st.session_state.timing_rows[bucket]
    if data["id"] in rows and data["id"] != selected:
        st.session_state.timing_error = "That interval name exists. Select it to edit."
        return
    rows[data["id"]] = data
    _changed()


def _remove(bucket, key):
    st.session_state.timing_rows[bucket].pop(st.session_state.get(key), None)
    st.session_state[key] = None
    _changed()


def _editor(bucket, title, fields):
    rows = st.session_state.timing_rows.setdefault(bucket, {})
    selector = "timing_choice_"+bucket
    selected = st.selectbox(title+" to add or edit", [None, *rows], key=selector, format_func=lambda key: key or "New interval")
    old = rows.get(selected, {})
    prefix = f"timing_{bucket}_{selected or 'new'}_"
    with st.form(prefix+"form"):
        for key, label, kind, options in fields:
            value = old.get("concurrent", {}).get(key.removeprefix("units_")) if kind == "concurrent" else old.get(key)
            if kind == "choice":
                st.selectbox(label, options, index=options.index(value) if value in options else None, key=prefix+key)
            elif kind in ("integer", "concurrent", "nonnegative"):
                st.number_input(label, min_value=0 if kind in ("concurrent", "nonnegative") else 1, max_value=1_000_000, value=value, step=1, key=prefix+key)
            else:
                st.text_input(label, value=value or "", disabled=key == "id" and selected is not None, key=prefix+key)
        st.form_submit_button("Keep "+title.lower()+" in draft", on_click=_store_row, args=(bucket, prefix, fields, selected))
    if selected:
        st.button("Remove "+title.lower(), on_click=_remove, args=(bucket, selector))
    if rows:
        st.dataframe(list(rows.values()))


def _dependency_editor(bucket, work_bucket, title):
    ids = list(st.session_state.timing_rows.get(work_bucket, {}))
    st.caption("Declare work order only where required. An empty list means no dependencies. "
               "The minimum waiting interval uses elapsed seconds after the prerequisite finishes; zero allows an immediate start. "
               "Waiting consumes no resources. Include active waiting as work if it occupies equipment or people.")
    _editor(bucket, title, [("id", "Dependency name", "text", None),
        ("predecessor", "Prerequisite work or run", "choice", ids),
        ("successor", "Work or run that follows", "choice", ids),
        ("min_gap_seconds", "Minimum waiting interval (elapsed seconds)", "nonnegative", None),
        ("source", "Source for work order and waiting interval", "text", None)])


def _store_storage(resource_id, prefix):
    st.session_state.timing_storage[resource_id] = dict(resource_id=resource_id,
        **{key: st.session_state.get(prefix+key) for key in ("capacity_l", "initial_l", "minimum_l", "source")})
    _changed()


def _timing_inputs(saved):
    rows = st.session_state.timing_rows
    data = dict(source=st.session_state.get("timing_source"), work=list(rows.get("work", {}).values()), storage=[], availability=[],
                dependencies=list(rows.get("dependencies", {}).values()))
    for resource in saved["inputs"]["resources"]:
        key = resource["id"]
        if resource["kind"] == "water":
            data["storage"].append(dict(st.session_state.timing_storage.get(key, {}), resource_id=key,
                inflows=list(rows.get("inflows:"+key, {}).values()), external_withdrawals=list(rows.get("external_withdrawals:"+key, {}).values())))
        elif resource["kind"] in ("pump_time", "labor_time"):
            data["availability"].append(dict(resource_id=key, windows=list(rows.get("availability:"+key, {}).values())))
    if st.session_state.get("timing_delivery_enabled"):
        data["water_delivery"] = [dict(resource_id=r["id"], windows=list(rows.get("delivery:"+r["id"], {}).values()))
                                  for r in saved["inputs"]["resources"] if r["kind"] == "water"]
    elif "water_delivery" in saved.get("timing_inputs", saved.get("schedule_inputs", {})):
        data["water_delivery"] = []
    return data


def _submit(call, saved, save):
    data = _timing_inputs(saved)
    request = dict(plan_id=saved["id"], data=data)
    if save:
        request["request_id"] = "web-timed-"+hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    try:
        st.session_state.timing_result = call("save_timed" if save else "evaluate_timing", request)
        if save:
            st.session_state.timing_notice = "Timed alternative saved. Failed checks remain visible; no work approved or resources reserved."
    except (ValueError, TypeError) as exc:
        _changed()
        st.session_state.timing_error = str(exc)


def render_timing_result(result):
    if result["declared_checks_pass"]:
        st.success("The supplied intervals pass the declared timing and storage checks.")
    else:
        st.warning("The supplied intervals fail one or more timing or storage checks.")
        st.dataframe([dict(check=_ISSUES[row["code"]], resource=row.get("resource_id"), start=row.get("start"), end=row.get("end"),
                           work=row.get("work_ids", [row["work_id"]] if row.get("work_id") else [])) for row in result["issues"]])
    if not result["all_requested_blocks_allocated"]:
        st.info("These checks cover allocated blocks only. Some requested work is still unallocated.")
    if result.get("dependencies"):
        st.write("Work order and minimum waiting intervals")
        st.dataframe(result["dependencies"])
    if result.get("water_delivery_checked"):
        st.write("Water outlet delivery checks (L/hour)")
        for pool in result["water_delivery"]:
            st.write(pool["resource_id"])
            if pool["intervals"]:
                st.dataframe([dict(row, max_flow_lph=row["max_flow_lph"]["value"], withdrawal_lph=row["withdrawal_lph"]["value"])
                              for row in pool["intervals"]])
    else:
        st.caption("Water outlet delivery capacity was not checked.")
    for pool in result["time_resources"]:
        st.write(pool["resource_id"], dict(available_hours=pool["available_hours"]["value"], budget_hours=pool["budget_hours"]["value"],
                                         occupied_hours=pool["occupied_hours"]["value"]))
        if pool["intervals"]:
            st.dataframe(pool["intervals"])
    for pool in result["storage"]:
        import pandas as pd

        st.write(pool["resource_id"], {key: pool[key]["value"] for key in ("initial_l", "capacity_l", "minimum_l", "lowest_l", "highest_l", "end_l")})
        chart = pd.DataFrame([dict(time_utc=row["at"], level_l=float(row["level_l"]["value"]),
            minimum_l=float(pool["minimum_l"]["value"]), capacity_l=float(pool["capacity_l"]["value"])) for row in pool["trajectory"]])
        chart["time_utc"] = pd.to_datetime(chart["time_utc"], utc=True)
        st.line_chart(chart, x="time_utc", y=["level_l", "minimum_l", "capacity_l"])
        st.caption("Reservoir balance and limits in liters; chart times are UTC. Exact checks use the unrounded values in the download.")
        st.dataframe([dict(at=row["at"], level_l=row["level_l"]["value"], exact_l=row["level_l"]["exact"]) for row in pool["trajectory"]])
    for assumption in result["assumptions"]:
        st.caption(assumption)


def render_resource_timing(call, saved):
    if st.session_state.get("timing_scope") != saved["id"]:
        for key in list(st.session_state):
            if key.startswith("timing_"):
                del st.session_state[key]
        st.session_state.timing_scope = saved["id"]
        previous = saved.get("timing_inputs", saved.get("schedule_inputs", {}))
        st.session_state.timing_source = previous.get("source", "")
        st.session_state.timing_rows = {"work": {row["id"]: row for row in previous.get("work", [])}}
        st.session_state.timing_storage = {}
        for pool in previous.get("storage", []):
            key = pool["resource_id"]
            st.session_state.timing_storage[key] = {k: v for k, v in pool.items() if k not in ("inflows", "external_withdrawals")}
            for field in ("inflows", "external_withdrawals"):
                st.session_state.timing_rows[field+":"+key] = {row["id"]: row for row in pool[field]}
        for pool in previous.get("availability", []):
            st.session_state.timing_rows["availability:"+pool["resource_id"]] = {row["id"]: row for row in pool["windows"]}
        st.session_state.timing_delivery_enabled = bool(previous.get("water_delivery"))
        for pool in previous.get("water_delivery", []):
            st.session_state.timing_rows["delivery:"+pool["resource_id"]] = {row["id"]: row for row in pool["windows"]}
        schedule = saved.get("schedule_inputs", {})
        inherited = previous.get("dependencies", [])
        st.session_state.timing_rows["dependencies"] = {row["id"]: row for row in inherited}
        st.session_state.timing_rows["schedule_dependencies"] = {row["id"]: row for row in schedule.get("dependencies", inherited)}
        st.session_state.timing_rows["runs"] = {row["id"]: {k: v for k, v in row.items() if k != "windows"} for row in schedule.get("runs", [])}
        for row in schedule.get("runs", []):
            st.session_state.timing_rows["run_windows:"+row["id"]] = {str(i+1): dict(window, id=str(i+1)) for i, window in enumerate(row["windows"])}
        for key in ("source", "step_seconds", "search_limit"):
            st.session_state["timing_schedule_"+key] = schedule.get(key, "" if key == "source" else None)
    with st.expander("Check proposed work times and storage"):
        if notice := st.session_state.pop("timing_notice", None):
            st.success(notice)
        if error := st.session_state.pop("timing_error", None):
            st.error(error)
        if "timing_result" in saved:
            st.subheader("Saved timing check")
            render_timing_result(saved["timing_result"])
        period = saved["inputs"]["period"]
        st.caption(f"Use timestamps with seconds and UTC offset matching {period['timezone']}, for example YYYY-MM-DDTHH:MM:SS+HH:MM. "
                   f"Intervals must stay within {period['start']} through {period['end']} in that timezone. "
                   "Listed equipment and workers stay occupied for the full interval. Water flows uniformly within each interval.")
        st.caption("Enter all known flows and availability. Empty flow lists mean explicitly none; time outside availability windows means unavailable. "
                   "Copying an allocation into a new draft does not copy its timing checks; check the newly saved allocation again.")
        intervals = [("id", "Interval name", "text", None), ("start", "Interval starts (with UTC offset)", "text", None),
                     ("end", "Interval ends (with UTC offset)", "text", None)]
        time_pools = [r for r in saved["inputs"]["resources"] if r["kind"] in ("pump_time", "labor_time")]
        work = [("id", "Work interval name", "text", None), ("job_id", "Allocated work entry", "choice",
                 [a["job_id"] for a in saved["result"]["allocations"] if a["allocated_blocks"]]),
                ("blocks", "Blocks in this interval", "integer", None), *intervals[1:]]
        work += [("units_"+r["id"], r["id"]+" units occupied (explicit zero if unused)", "concurrent", None) for r in time_pools]
        work.append(("source", "Work interval source", "text", None))
        _editor("work", "Work interval", work)
        _dependency_editor("dependencies", "work", "Work dependency")
        st.checkbox("Check water outlet delivery limits", key="timing_delivery_enabled", on_change=_changed)
        if st.session_state.timing_delivery_enabled:
            st.caption("Supply delivery windows for each water pool in L/hour. Outside listed windows capacity is zero. "
                       "Job and outside withdrawals share this limit; reservoir inflows do not reduce outlet demand. "
                       "Uniform interval rates do not establish actual peak flow or pressure. These windows also apply to start-time proposals below.")
        for resource in saved["inputs"]["resources"]:
            key = resource["id"]
            st.subheader(key)
            if resource["kind"] == "water":
                prefix = "timing_storage_"+key+"_"
                previous = st.session_state.timing_storage.get(key, {})
                with st.form(prefix+"form"):
                    for field, label in (("capacity_l", "Reservoir capacity (L)"), ("initial_l", "Volume at period start (L)"),
                                         ("minimum_l", "Minimum reserve (L)"), ("source", "Storage state source")):
                        st.text_input(label, value=previous.get(field, ""), key=prefix+field)
                    st.form_submit_button("Keep "+key+" storage state", on_click=_store_storage, args=(key, prefix))
                for field, label in (("inflows", "Inflow"), ("external_withdrawals", "Outside withdrawal")):
                    _editor(field+":"+key, label+" for "+key, [*intervals,
                        ("volume_l", "Total volume over interval (L)", "text", None), ("source", "Flow source", "text", None)])
                if st.session_state.timing_delivery_enabled:
                    _editor("delivery:"+key, "Delivery window for "+key, [*intervals,
                        ("max_flow_lph", "Maximum combined outlet flow (L/hour; zero means closed)", "text", None),
                        ("source", "Delivery limit source and operating conditions", "text", None)])
            elif resource["kind"] in ("pump_time", "labor_time"):
                _editor("availability:"+key, "Availability for "+key, [*intervals,
                    ("units", "Available pumps or workers", "integer", None), ("source", "Availability source, net of other commitments", "text", None)])
        with st.form("timing_submit"):
            st.text_input("Timing scenario source and assumptions", key="timing_source")
            st.form_submit_button("Evaluate timing checks", on_click=_submit, args=(call, saved, False))
            st.form_submit_button("Save timed scenario", on_click=_submit, args=(call, saved, True))
        if result := st.session_state.get("timing_result"):
            st.subheader("Submitted timing result")
            render_timing_result(result["timing_result"])
            st.download_button("Download timing result", json.dumps(result, indent=2), "resource-timing.json", "application/json")
    from .resource_schedule_ui import render_resource_schedule

    render_resource_schedule(call, saved)
