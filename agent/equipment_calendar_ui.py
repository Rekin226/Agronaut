"""Physical equipment registration and explicit bookings without a model provider."""

import hashlib
import json

import streamlit as st

from agronaut_agent.equipment_calendar import KINDS


def _write(call, operation, request, selector):
    request_id = "web-equipment-"+hashlib.sha256(json.dumps([operation, request], sort_keys=True).encode()).hexdigest()
    try:
        result = call(operation, dict(request, request_id=request_id))
        st.session_state[selector] = result["id"]
        st.session_state.resource_notice = {
            "save_equipment": "Equipment record saved. Existing bookings are preserved.",
            "book_equipment": "Equipment intervals booked. Budget commitments remain separate.",
            "reserve_equipment": "Equipment calendar block saved.",
            "release_equipment": "Equipment booking released. Budget charges and actual records are unchanged.",
        }[operation]
    except (ValueError, TypeError) as exc:
        st.session_state.resource_error = str(exc)


def _save_asset(call, prior, prefix):
    data = {key: st.session_state.get(prefix+key) for key in ("equipment_key", "name", "kind", "status", "source")}
    reason = st.session_state.get(prefix+"reason") or None
    if prior and prior["data"] == data and reason is None:
        st.session_state.resource_notice = "This equipment record is already saved."
        return
    _write(call, "save_equipment", dict(data=data, supersedes_id=prior["id"] if prior else None, reason=reason), "resource_equipment_choice")


def _save_booking(call, prior, prefix, plan_id=None, work=None):
    request = dict(supersedes_id=prior["id"] if prior else None, reason=st.session_state.get(prefix+"reason") or None)
    if plan_id:
        operation = "book_equipment"
        request.update(plan_id=plan_id, assignments={w["id"]: sorted(st.session_state.get(prefix+"work_"+w["id"], [])) for w in work},
                       source=st.session_state.get(prefix+"source"), accept_partial=st.session_state.get(prefix+"partial", False))
    else:
        operation = "reserve_equipment"
        request["data"] = {key: st.session_state.get(prefix+key) for key in ("equipment_ids", "start", "end", "title", "source")}
        request["data"]["equipment_ids"] = sorted(request["data"]["equipment_ids"])
    if (prior and prior["status"] == "active" and request["reason"] is None and prior["operation"]["kind"] == operation
            and all(prior["operation"].get(k) == v for k, v in request.items() if k not in ("supersedes_id", "reason"))):
        st.session_state.resource_notice = "This equipment booking is already saved."
        return
    _write(call, operation, request, "resource_equipment_booking_choice")


def _release(call, prior, prefix):
    _write(call, "release_equipment", dict(booking_id=prior["id"], source=st.session_state.get(prefix+"source"),
        reason=st.session_state.get(prefix+"reason")), "resource_equipment_booking_choice")


def _selection(key, options, default):
    if key not in st.session_state:
        st.session_state[key] = default
    retained = [v for v in st.session_state[key] if v in options]
    if retained != st.session_state[key]:
        st.session_state[key] = retained
        st.caption("A previous equipment revision is no longer selectable. Review and select its current revision explicitly.")


def render_equipment_calendar(call, farm):
    view = call("equipment_calendar", {})
    assets = {r["id"]: r for r in view["equipment"]}
    with st.expander("Shared equipment calendar"):
        st.caption(view["note"])
        selector = "resource_equipment_choice"
        if st.session_state.get(selector) not in (None, *assets):
            st.session_state[selector] = None
        selected = st.selectbox("Equipment to add or edit", [None, *assets], key=selector,
            format_func=lambda key: "New physical equipment" if key is None else f"{assets[key]['data']['name']} · {assets[key]['data']['equipment_key']}")
        prior = assets.get(selected)
        old = prior["data"] if prior else {}
        prefix = f"resource_equipment_asset_{selected or 'new'}_"
        with st.form(prefix+"form"):
            st.text_input("Stable equipment key", value=old.get("equipment_key", ""), disabled=prior is not None, key=prefix+"equipment_key")
            st.text_input("Equipment name", value=old.get("name", ""), key=prefix+"name")
            st.selectbox("Equipment kind", KINDS, index=KINDS.index(old["kind"]) if prior else None, disabled=prior is not None, key=prefix+"kind")
            st.selectbox("Equipment status", ["available", "unavailable"], index=0 if old.get("status", "available") == "available" else 1, key=prefix+"status")
            st.text_input("Equipment source and applicability", value=old.get("source", ""), key=prefix+"source")
            st.text_input("Reason for equipment correction", key=prefix+"reason")
            st.form_submit_button("Save physical equipment", on_click=_save_asset, args=(call, prior, prefix))
        if assets:
            st.dataframe([dict(equipment_id=r["id"], **r["data"]) for r in assets.values()])
        latest = {r["id"]: r for r in view["bookings"] if not r["superseded"]}
        selector = "resource_equipment_booking_choice"
        if st.session_state.get(selector) not in (None, *latest):
            st.session_state[selector] = None
        chosen = st.selectbox("Equipment booking to review", [None, *latest], key=selector,
            format_func=lambda key: "New equipment booking" if key is None else f"{latest[key]['title']} · {latest[key]['status']} · {key[:8]}")
        prior = latest.get(chosen)
        mode = st.radio("Equipment booking type", ["Timed scenario", "Calendar block"],
            index=1 if prior and prior["plan_id"] is None else 0, key=f"resource_equipment_mode_{chosen or 'new'}")
        if mode == "Timed scenario":
            plans = {r["id"]: r for r in call("list", {})["plans"] if r["timing_checks_pass"] is True}
            key = f"resource_equipment_plan_{chosen or 'new'}"
            if key not in st.session_state:
                st.session_state[key] = prior["plan_id"] if prior else None
            if st.session_state.get(key) not in (None, *plans):
                st.session_state[key] = None
            plan_id = st.selectbox("Timed scenario to book", [None, *plans], key=key,
                format_func=lambda key: "Select a saved timed scenario" if key is None else f"{plans[key]['title']} · {key[:8]}")
            if plan_id:
                try:
                    inputs = call("equipment_inputs", dict(plan_id=plan_id))
                except (ValueError, TypeError) as exc:
                    st.warning(str(exc))
                else:
                    st.dataframe(inputs["work"])
                    options = {key: a for key, a in assets.items() if a["data"]["status"] == "available"}
                    prefix = f"resource_equipment_timed_{chosen or 'new'}_{plan_id}_"
                    with st.form(prefix+"form"):
                        for w in inputs["work"]:
                            default = [prior["equipment_snapshots"][r["equipment_root_id"]]["id"] for r in prior["intervals"]
                                       if r["work_id"] == w["id"]] if prior and prior["plan_id"] == plan_id else inputs.get("suggested_assignments", {}).get(w["id"], [])
                            key = prefix+"work_"+w["id"]
                            _selection(key, options, default)
                            st.multiselect(f"Equipment for {w['id']} · {w['pump_count']} pump(s) required", list(options), key=key,
                                format_func=lambda key: f"{options[key]['data']['name']} · {options[key]['data']['kind']} · {options[key]['data']['equipment_key']}")
                        st.checkbox("I accept booking only allocated work if this scenario is partial", value=prior["operation"].get("accept_partial", False) if prior else False, key=prefix+"partial")
                        st.text_input("Booking source and equipment suitability", value=prior["source"] if prior else "", key=prefix+"source")
                        st.text_input("Reason for booking replacement", key=prefix+"reason")
                        st.form_submit_button("Book equipment for scenario", on_click=_save_booking, args=(call, prior, prefix, plan_id, inputs["work"]))
        else:
            prefix = f"resource_equipment_block_{chosen or 'new'}_"
            old = prior["operation"].get("data", {}) if prior and prior["plan_id"] is None else {}
            st.caption(f"Block equipment for outside work, setup, travel or maintenance. Use timestamps with the offset for {farm['timezone']}, for example 2026-10-20T08:00:00+00:00 for UTC. A block does not establish equipment suitability.")
            with st.form(prefix+"form"):
                _selection(prefix+"equipment_ids", assets, old.get("equipment_ids", []))
                st.multiselect("Equipment to block", list(assets), key=prefix+"equipment_ids",
                    format_func=lambda key: f"{assets[key]['data']['name']} · {assets[key]['data']['equipment_key']}")
                for key, label in (("title", "Calendar block title"), ("start", "Block start with UTC offset"), ("end", "Block end with UTC offset"), ("source", "Calendar block source")):
                    st.text_input(label, value=old.get(key, ""), key=prefix+key)
                st.text_input("Reason for block replacement", key=prefix+"reason")
                st.form_submit_button("Save equipment calendar block", on_click=_save_booking, args=(call, prior, prefix))
        if prior and prior["status"] == "active":
            prefix = f"resource_equipment_release_{prior['id']}_"
            with st.form(prefix+"form"):
                st.text_input("Equipment release source", key=prefix+"source")
                st.text_input("Reason for equipment release", key=prefix+"reason")
                st.form_submit_button("Release equipment booking", on_click=_release, args=(call, prior, prefix))
        if view["active"]:
            st.dataframe([dict(booking_id=r["id"], title=r["title"], plan_id=r["plan_id"], **interval)
                          for r in view["active"] for interval in r["intervals"]])
            for r in view["active"]:
                if r["review"]["needs_review"]:
                    st.warning(f"Equipment booking {r['id'][:8]} needs review. Its intervals remain held.")
                    st.write(r["review"])
        if view["equipment_history"] or view["bookings"]:
            st.download_button("Download equipment calendar history", json.dumps(view, indent=2), "equipment-calendar.json", "application/json",
                               key="resource_equipment_download")
