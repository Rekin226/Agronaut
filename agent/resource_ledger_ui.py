"""Named budget and explicit commitment controls for the farm resource page."""

import hashlib
import json
from datetime import date

import streamlit as st

from agro_model.resources import KINDS


def _write(call, operation, request, selector=None):
    request_id = "web-ledger-"+hashlib.sha256(json.dumps([operation, request], sort_keys=True).encode()).hexdigest()
    try:
        saved = call(operation, dict(request, request_id=request_id))
        if selector:
            st.session_state[selector] = saved["id"]
        st.session_state.resource_notice = {"save_budget": "Shared budget saved.", "commit_plan": "Allocated quantities committed to shared budgets.",
                                           "release_commitment": "Budget commitment released; no actual use recorded.",
                                           "settle_commitment": "Usage settlement saved; unknown allowances and any budget deficits remain visible."}[operation]
    except (ValueError, TypeError) as exc:
        st.session_state.resource_error = str(exc)


def _budget(call, farm, prior, prefix):
    data = {k: st.session_state.get(prefix+k) for k in ("resource_key", "kind", "unit", "capacity", "source")}
    data["unit"] = data["unit"] if data["kind"] == "cash" else KINDS.get(data["kind"])
    start, end = st.session_state.get(prefix+"start"), st.session_state.get(prefix+"end")
    data["period"] = dict(start=start.isoformat() if start else None, end=end.isoformat() if end else None,
                          timezone=prior["data"]["period"]["timezone"] if prior else farm["timezone"])
    if prior:
        data["period"] = prior["data"]["period"]
    reason = st.session_state.get(prefix+"reason") or None
    if prior and data == prior["data"] and reason is None:
        st.session_state.resource_notice = "This budget is already saved."
        return
    _write(call, "save_budget", dict(data=data, supersedes_id=prior["id"] if prior else None, reason=reason), "resource_ledger_budget_choice")


def _commit(call, plan_id, pools, prior, prefix):
    request = dict(plan_id=plan_id, bindings={key: st.session_state.get(prefix+"pool_"+key) for key in pools},
        source=st.session_state.get(prefix+"source"), accept_partial=st.session_state.get(prefix+"partial"),
        supersedes_id=prior["id"] if prior else None, reason=st.session_state.get(prefix+"reason") or None)
    if (prior and prior["status"] == "active" and request["reason"] is None
            and all(request[k] == prior["operation"].get(k) for k in ("plan_id", "bindings", "source", "accept_partial"))):
        st.session_state.resource_notice = "This commitment is already saved."
        return
    _write(call, "commit_plan", request, "resource_ledger_commit_choice")


def _release(call, prior_id, prefix):
    _write(call, "release_commitment", dict(commitment_id=prior_id, source=st.session_state.get(prefix+"source"),
        reason=st.session_state.get(prefix+"reason")), "resource_ledger_commit_choice")


def render_resource_ledger(call, farm):
    ledger = call("ledger", {})
    budgets = {row["id"]: row for row in ledger["budgets"]}
    with st.expander("Shared budgets and commitments"):
        st.caption(ledger["note"])
        if budgets:
            st.dataframe([dict(budget_id=r["id"], resource_key=r["data"]["resource_key"], **r["data"]["period"],
                kind=r["data"]["kind"], unit=r["data"]["unit"], capacity=r["data"]["capacity"], committed=r["committed"],
                reserved=r["reserved"], recorded_use=r["recorded_use"], remaining=r["remaining"], overdrawn=r["overdrawn"],
                unresolved_usage_count=r["unresolved_usage_count"],
                settlement_evidence_changed=r["settlement_evidence_changed"],
                timezone_changed=r["timezone_changed"]) for r in budgets.values()])
            for row in budgets.values():
                if row["settlement_evidence_changed"]:
                    st.warning(f"{row['data']['resource_key']} has changed settlement evidence. Correct that settlement before committing more resources from this budget.")
                if row["overdrawn"] != "0":
                    st.warning(f"{row['data']['resource_key']} is overdrawn by {row['overdrawn']} {row['data']['unit']}. Recorded use is preserved; further reservations must fit the remaining balance.")
        selector = "resource_ledger_budget_choice"
        if st.session_state.get(selector) not in (None, *budgets):
            st.session_state[selector] = None
        key = st.selectbox("Budget to create or correct", [None, *budgets], key=selector,
            format_func=lambda key: "New shared budget" if key is None else f"{budgets[key]['data']['resource_key']} · {key[:8]}")
        prior = budgets.get(key)
        old = prior["data"] if prior else {}
        prefix = f"resource_ledger_budget_{key or 'new'}_"
        with st.form(prefix+"form"):
            st.text_input("Stable resource key", value=old.get("resource_key", ""), disabled=prior is not None, key=prefix+"resource_key",
                help="Reuse one key for the same supply across non-overlapping periods. Do not give the same water or money two names.")
            st.selectbox("Shared budget kind", list(KINDS), index=list(KINDS).index(old["kind"]) if old else None,
                         disabled=prior is not None, key=prefix+"kind")
            st.text_input("Budget currency (cash only)", value=old.get("unit", "") if old.get("kind") == "cash" else "",
                          disabled=prior is not None, key=prefix+"unit")
            for part in ("start", "end"):
                value = date.fromisoformat(old["period"][part]) if old else None
                if value is not None and value.year < 1000:
                    value = None
                st.date_input("Budget period "+part, value=value, min_value=date(1000, 1, 1), max_value=date(9999, 12, 30),
                              disabled=prior is not None, key=prefix+part)
            if prior:
                st.caption(f"This budget retains its original period: {old['period']['start']} to {old['period']['end']} · {old['period']['timezone']}.")
            st.text_input("Total capacity assigned to this ledger", value=old.get("capacity", ""), key=prefix+"capacity",
                          help="Decimal amount in L, hours, or the currency above. Exclude commitments outside Agronaut; existing Agronaut commitments are already counted.")
            st.text_input("Budget capacity source", value=old.get("source", ""), key=prefix+"source")
            st.text_input("Reason for budget correction", key=prefix+"reason")
            st.form_submit_button("Save shared budget", on_click=_budget, args=(call, farm, prior, prefix))
        latest = {r["id"]: r for r in ledger["commitments"] if not r["superseded"]}
        selector = "resource_ledger_commit_choice"
        if st.session_state.get(selector) not in (None, *latest):
            st.session_state[selector] = None
        selected = st.selectbox("Commitment to review", [None, *latest], key=selector,
            format_func=lambda key: "New commitment" if key is None else f"{latest[key]['status']} · {key[:8]}")
        prior = latest.get(selected)
        plans = {r["id"]: r for r in call("list", {})["plans"]}
        plan_key = f"resource_ledger_plan_{selected or 'new'}"
        if plan_key not in st.session_state:
            st.session_state[plan_key] = prior["plan_id"] if prior else None
        if st.session_state.get(plan_key) not in (None, *plans):
            st.session_state[plan_key] = None
        plan_id = st.selectbox("Scenario to commit", [None, *plans], key=plan_key,
            format_func=lambda key: "Select a saved scenario" if key is None else f"{plans[key]['title']} · {key[:8]}")
        if plan_id and (not prior or prior["status"] in ("active", "released")):
            plan = call("get", dict(plan_id=plan_id))
            st.caption("Only allocated blocks and their assigned quantities will be committed. No unallocated work is reserved.")
            st.dataframe([{k: r[k] for k in ("id", "kind", "unit", "assigned")} for r in plan["result"]["resources"]])
            with st.expander("Work included in this commitment"):
                st.dataframe([dict(unit_id=r["unit_id"], cycle_id=r["cycle_id"], work=r["title"],
                    allocated_blocks=r["allocated_blocks"], unallocated_blocks=r["unallocated_blocks"],
                    pool=key, assigned=amount) for r in plan["result"]["allocations"] for key, amount in r["assigned"].items()])
            prefix = f"resource_ledger_commit_{selected or 'new'}_{plan_id}_"
            with st.form(prefix+"form"):
                pools = [r["id"] for r in plan["result"]["resources"]]
                for pool in plan["result"]["resources"]:
                    options = {key: row for key, row in budgets.items() if
                               (row["data"]["kind"], row["data"]["unit"]) == (pool["kind"], pool["unit"])}
                    widget = prefix+"pool_"+pool["id"]
                    if widget not in st.session_state:
                        st.session_state[widget] = prior["bindings"].get(pool["id"]) if prior else None
                    if st.session_state.get(widget) not in (None, *options):
                        st.session_state[widget] = None
                        st.caption("The previous budget revision is no longer current. Select its updated revision explicitly.")
                    st.selectbox("Shared budget for "+pool["id"], [None, *options], key=widget,
                        format_func=lambda key, options=options: "Select a budget" if key is None else
                        f"{options[key]['data']['resource_key']} · {options[key]['data']['period']['start']} to {options[key]['data']['period']['end']} · {key[:8]}")
                st.checkbox("I accept committing only the allocated blocks if this scenario is partial",
                            value=prior["operation"].get("accept_partial", False) if prior else False, key=prefix+"partial")
                st.text_input("Commitment source", value=prior["source"] if prior else "", key=prefix+"source")
                st.text_input("Reason for replacement or release", key=prefix+"reason")
                st.form_submit_button("Commit allocated resources", on_click=_commit, args=(call, plan_id, pools, prior, prefix))
                if prior and prior["status"] == "active":
                    st.form_submit_button("Release commitment", on_click=_release, args=(call, prior["id"], prefix))
        if prior:
            from .resource_settlement_ui import render_resource_settlement
            render_resource_settlement(call, prior)
        if ledger["commitments"]:
            st.dataframe([dict(commitment_id=r["id"], plan_id=r["plan_id"], status=r["status"], superseded=r["superseded"],
                **r["plan_snapshot"]["inputs"]["period"], title=r["plan_snapshot"]["inputs"]["title"],
                needs_review=r["review"]["needs_review"], reason=r["reason"]) for r in ledger["commitments"]])
            for row in latest.values():
                if row["review"]["needs_review"]:
                    st.warning(f"Record {row['id'][:8]} needs review; its charges are preserved. Inspect changed or incomplete evidence before correcting it.")
                    st.write(row["review"])
        if ledger["budget_history"] or ledger["commitments"]:
            st.download_button("Download budget and commitment history", json.dumps(ledger, indent=2),
                               file_name="resource-ledger.json", mime="application/json", key="resource_ledger_download")
