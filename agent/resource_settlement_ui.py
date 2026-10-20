"""Record or correct complete resource usage while retaining unknown allowances."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

from agronaut_agent.resource_settlement import MANUAL_PROVENANCE


def _save(call, prior, prefix):
    from .resource_ledger_ui import _write

    usage = {}
    for root in prior["amounts"]:
        row_prefix = prefix+root+"_"
        entered = st.session_state.get(row_prefix+"amount", "").strip()
        ids = st.session_state.get(row_prefix+"outcome_ids", [])
        usage[root] = dict(amount=entered or None, source=st.session_state.get(row_prefix+"source"), outcome_ids=ids,
                           provenance="linked_outcomes" if ids else st.session_state.get(row_prefix+"provenance") if entered else None)
    start, end = st.session_state.get(prefix+"start"), st.session_state.get(prefix+"end")
    data = dict(actual_start=start.isoformat() if start else None, actual_end=end.isoformat() if end else None,
                attribution=st.session_state.get(prefix+"attribution"), source=st.session_state.get(prefix+"source"), usage=usage)
    reason = st.session_state.get(prefix+"reason")
    if data == prior.get("settlement") and not reason:
        st.session_state.resource_notice = "This settlement is already saved. Supply a reason to reassess its evidence."
        return
    _write(call, "settle_commitment", dict(commitment_id=prior["id"], data=data, reason=reason), "resource_ledger_commit_choice")


def render_resource_settlement(call, prior):
    try:
        inputs = call("settlement_inputs", dict(commitment_id=prior["id"]))
    except ValueError as exc:
        st.error(str(exc))
        return
    st.subheader("Settle recorded usage")
    st.caption(inputs["note"])
    if prior["status"] == "released":
        st.caption("This allowance was released. A late actual-use report will charge the budget again; check for another commitment covering the same work.")
    periods = [r["data"]["period"] for r in prior["budget_snapshots"].values()]
    earliest = max(date(1000, 1, 1), *(date.fromisoformat(p["start"]) for p in periods))
    last = min(*(date.fromisoformat(p["end"]) for p in periods), datetime.now(ZoneInfo(periods[0]["timezone"])).date())
    if earliest > last:
        st.info("These budgets have not started yet. Actual usage can be recorded once its dates fall inside their periods.")
        return
    prefix = f"resource_settlement_{prior['id']}_"
    old = prior.get("settlement", {})
    candidates = {r["id"]: r for r in inputs["outcomes"]}
    for row in prior.get("outcome_evidence", []):
        candidates.setdefault(row["outcome_id"], row["snapshot"])
    pool_roots = inputs["pool_budget_roots"]
    with st.form(prefix+"form"):
        for key in ("start", "end"):
            value = date.fromisoformat(old["actual_"+key]) if old else None
            if value and not earliest <= value <= last:
                value = None
            st.date_input("Actual usage period "+key, value=value, min_value=earliest, max_value=last, key=prefix+key)
        st.caption("Dates use the original budget timezone: "+periods[0]["timezone"])
        for root, budget in prior["budget_snapshots"].items():
            details = budget["data"]
            st.write(f"{details['resource_key']} · {details['unit']} · currently charged {prior['amounts'][root]}")
            row_prefix = prefix+root+"_"
            previous = old.get("usage", {}).get(root, {})
            st.text_input("Complete used amount · "+details["resource_key"], value=previous.get("amount") or "", key=row_prefix+"amount",
                help="Blank means unknown and retains an allowance. Enter 0 only for a known zero. Decimal strings, up to six places.")
            provenance = previous.get("provenance")
            st.selectbox("Manual total provenance · "+details["resource_key"], MANUAL_PROVENANCE,
                         index=MANUAL_PROVENANCE.index(provenance) if provenance in MANUAL_PROVENANCE else None, key=row_prefix+"provenance")
            eligible = {key: r for key, r in candidates.items() if any(pool_roots.get(m["resource_id"]) == root and m["observed_total"] is not None for m in r["comparisons"])}
            st.multiselect("Optional complete irrigation outcome set · "+details["resource_key"], list(eligible),
                default=previous.get("outcome_ids", []), key=row_prefix+"outcome_ids",
                format_func=lambda key, eligible=eligible: f"{eligible[key]['job_id']} · {eligible[key]['actual_day']} · {key[:8]}")
            if eligible:
                st.caption("With linked outcomes, enter their exact combined total and select every allocated event for this budget. Provenance becomes linked_outcomes. For other work, use a sourced manual total.")
                st.dataframe([dict(outcome_id=r["id"], job_id=r["job_id"], day=r["actual_day"], metric=m["metric"],
                    exact_total=m["observed_total"]["exact"], unit=m["unit"], provenance=m["provenance"], quality=m["quality"],
                    needs_review=r.get("review", {}).get("needs_review", True))
                    for r in eligible.values() for m in r["comparisons"] if pool_roots.get(m["resource_id"]) == root and m["observed_total"] is not None])
            st.text_input("Usage source or explanation of unknown · "+details["resource_key"], value=previous.get("source", ""), key=row_prefix+"source")
        st.text_area("How these totals cover the finished or stopped committed work", value=old.get("attribution", ""), key=prefix+"attribution")
        st.text_input("Settlement source", value=old.get("source", ""), key=prefix+"source")
        st.text_input("Reason for settlement or correction", key=prefix+"reason")
        st.form_submit_button("Record usage settlement", on_click=_save, args=(call, prior, prefix))
    if old:
        st.dataframe([dict(resource_key=prior["budget_snapshots"][root]["data"]["resource_key"], unit=prior["budget_snapshots"][root]["data"]["unit"],
            original_allowance=prior["baseline_amounts"][root], total_unknown=row["amount"] is None,
            recorded_use=prior["used_amounts"][root], reserved=prior["reserved_amounts"][root], source=row["source"])
            for root, row in old["usage"].items()])
