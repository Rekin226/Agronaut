"""Actual cycle-attributed receipts and payments, without model interpretation."""

from datetime import date

import streamlit as st


def _save(call, cycle_id, prior_id, prefix):
    from .crop_ui import _request_id

    data = {key: st.session_state.get(prefix+key) for key in ("entry", "day", "direction", "amount", "currency",
            "category", "description", "allocation_note", "source", "provenance")}
    data["day"] = data["day"].isoformat() if data["day"] else None
    request = dict(cycle_id=cycle_id, data=data, supersedes_id=prior_id)
    try:
        call("record_cash", dict(request, request_id=_request_id("record_cash", request)))
        st.session_state.crop_notice = "Cash entry saved; correction history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _void(call, selector):
    from .crop_ui import _request_id

    data = dict(record_id=st.session_state.get(selector), reason=st.session_state.get(selector+"_reason"))
    try:
        call("void_cash", dict(data, request_id=_request_id("void_cash", data)))
        st.session_state.crop_notice = "Cash entry voided; history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def render_cash_totals(summary):
    st.caption(summary["note"])
    if not summary["totals"]:
        st.info("No current cash entries recorded; spending and receipts are unknown.")
        return
    st.dataframe([{key: value for key, value in row.items() if key != "record_ids"} for row in summary["totals"]])
    st.caption("Recorded amounts by category; currencies and provenance remain separate.")
    st.dataframe([{key: value for key, value in row.items() if key != "record_ids"} for row in summary["by_category"]])


def render_cash_records(call, journal, cycle_id, today):
    from .crop_ui import _journal_date

    rows = [r for r in journal["records"] if r["kind"] == "cash"]
    current = {r["id"]: r for r in rows if not r["superseded"]}
    selector = f"crop_cash_correction_{cycle_id}"
    if st.session_state.get(selector) not in (None, *current):
        st.session_state[selector] = None
    with st.expander("Record or correct a payment or receipt"):
        st.caption("Enter money actually paid or received and the amount assigned to this crop cycle. "
                   "Use the transaction date, including preplant expenses or receipts after the crop ended. "
                   "Keep quotes and unpaid invoices out of these cash records.")
        prior_id = st.selectbox("Cash entry", [None, *current], key=selector,
            format_func=lambda key: "New cash entry" if key is None else
            f"Correct {current[key]['data']['entry']}" + (" (voided)" if current[key]["voided"] else ""))
        old = current[prior_id]["data"] if prior_id else {}
        prefix = f"crop_cash_{cycle_id}_{prior_id or 'new'}_"
        with st.form(prefix+"form"):
            st.text_input("Cash entry ID", value=old.get("entry", ""), disabled=prior_id is not None, key=prefix+"entry")
            _journal_date("Payment or receipt date", old.get("day"), date(1000, 1, 1), today, prefix+"day")
            st.selectbox("Cash direction", ["expense", "income"], index=["expense", "income"].index(old["direction"]) if old else None,
                         format_func=lambda value: "Expense paid" if value == "expense" else "Income received", key=prefix+"direction")
            st.text_input("Amount assigned to this cycle", value=old.get("amount", ""), key=prefix+"amount", max_chars=22,
                          help="Positive decimal amount, for example 120.50. Up to six decimal places; no commas or currency symbols.")
            st.text_input("Currency code", value=old.get("currency", ""), max_chars=3, placeholder="USD, XOF, …", key=prefix+"currency")
            st.text_input("Cash category", value=old.get("category", ""), key=prefix+"category",
                          help="For example seeds, paid labor, energy, or produce sales. Use consistent names in your records.")
            st.text_input("Payment or receipt description", value=old.get("description", ""), key=prefix+"description")
            st.text_input("How this amount was allocated to the cycle", value=old.get("allocation_note", ""), key=prefix+"allocation_note",
                          help="State whether this is the whole transaction or an allocated share. Record only this cycle's share.")
            provenance = ["documented", "reported", "estimated"]
            st.selectbox("Cash amount provenance", provenance, index=provenance.index(old["provenance"]) if old else None,
                         key=prefix+"provenance")
            st.text_input("Cash record source", key=prefix+"source", help="Receipt, bank entry, operator report or estimation source.")
            st.form_submit_button("Save cash entry", on_click=_save, args=(call, cycle_id, prior_id, prefix))
    eligible = {key: row for key, row in current.items() if not row["voided"]}
    if eligible:
        selector = f"crop_cash_void_{cycle_id}"
        if st.session_state.get(selector) not in eligible:
            st.session_state[selector] = next(iter(eligible))
        with st.expander("Void an incorrect cash entry"):
            st.selectbox("Cash entry to void", list(eligible), key=selector, format_func=lambda key: eligible[key]["data"]["entry"])
            st.text_input("Reason for voiding cash entry", key=selector+"_reason")
            st.button("Void cash entry", key=selector+"_submit", on_click=_void, args=(call, selector))
    if rows:
        st.subheader("Cash entry history")
        st.dataframe([dict(r["data"], record_id=r["id"], superseded=r["superseded"], voided=r["voided"], reason=r["reason"]) for r in rows])
        render_cash_totals(journal["cash"])
