"""Explicit actual surface-water intake, corrections and owned coupled replay."""

import csv
import hashlib
import json
from datetime import datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

import streamlit as st

from .coupled_water_ui import _reset, _table

COLUMNS = ("id", "rainfall_mm", "rainfall_runoff_mm", "irrigation_at_surface_mm", "source")


def _record(call, saved_id, cycle_id, prefix, supersedes_id):
    try:
        if st.session_state.get("field_plan") != saved_id:
            raise ValueError("The selected calculation changed; review the surface-water draft.")
        if st.session_state.get("field_cycle") != cycle_id:
            raise ValueError("The selected crop cycle changed; review the surface-water draft.")
        s = st.session_state
        day = s.get(prefix+"day")
        data = dict(day=day.isoformat() if day else None,
            patches=list(_table(s[prefix+"rows"], COLUMNS, ("id",), "surface-water rows", 100).values()),
            source=s[prefix+"source"], representation_note=s[prefix+"coverage"],
            provenance=s[prefix+"provenance"], quality=s[prefix+"quality"])
        request = dict(calculation_id=saved_id, data=data, supersedes_id=supersedes_id,
            reason=s[prefix+"reason"] if supersedes_id else None)
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        result = call("record_surface_day", dict(request, request_id="surface-day-"+digest))
        s.field_notice = "Surface-water record saved: "+result["id"]
    except (ValueError, TypeError, csv.Error) as exc:
        st.session_state.field_error = str(exc)


def render_surface_water(call, saved, save_revision):
    p = "field_surface_water_"
    _reset(p, saved["id"])
    if saved["task"] != "coupled_soil_water":
        return
    with st.expander("Record and replay actual surface water"):
        try:
            review = call("review_surface_water", dict(calculation_id=saved["id"]))
        except ValueError as exc:
            st.error(str(exc))
            return
        st.caption(review["note"])
        area = Fraction(review["footprint"]["area_m2_exact"])
        st.dataframe([{"Patch": r["id"], "Area fraction": float(Fraction(r["area_fraction_exact"])),
                       "Represented area (m²)": float(area*Fraction(r["area_fraction_exact"]))}
                      for r in review["footprint"]["patches"]], hide_index=True)
        st.caption("Supply whole-day totals for every patch: "+", ".join(r["id"] for r in review["footprint"]["patches"])+
                   ". Unknown water is not zero. Saving a record does not run a calculation.")
        current = {r["id"]: r for r in review["records"] if not r["superseded"]}
        if st.session_state.get(p+"correction") not in current:
            st.session_state[p+"correction"] = None
        correction = st.selectbox("Surface-water record to correct", [None, *current], key=p+"correction",
            format_func=lambda i: "New daily record" if i is None else f"{current[i]['day']} · {current[i]['data']['quality']} · {i}")
        draft = p+"draft_"
        _reset(draft, (saved["id"], correction))
        if correction:
            st.dataframe(current[correction]["data"]["patches"], hide_index=True)
            st.caption("Enter the complete corrected daily totals and their evidence below. Set quality to rejected to retain an unusable record in history.")
        with st.form(draft+"form"):
            st.date_input("Surface-water local day", value=None,
                max_value=datetime.now(ZoneInfo(review["timezone"])).date()-timedelta(days=1), key=draft+"day")
            st.text_area("Actual patch water rows", key=draft+"rows",
                help="Comma-separated rows without headers: "+", ".join(COLUMNS)+
                     ". Depths are local patch mm, decimal strings up to six decimal places. Quote sources containing commas.")
            st.text_input("Surface-water record source", key=draft+"source")
            st.text_input("Evidence for patch locations, area and whole-day coverage", key=draft+"coverage")
            st.selectbox("Surface-water provenance", ["measured", "reported", "estimated"], index=None, key=draft+"provenance")
            st.selectbox("Surface-water quality", ["unverified", "checked", "rejected"], index=None, key=draft+"quality")
            if correction:
                st.text_input("Surface-water correction reason", key=draft+"reason")
            st.form_submit_button("Save actual surface-water record", on_click=_record, args=(call, saved["id"], saved["cycle_id"], draft, correction))
        if review["records"]:
            st.dataframe([dict(day=r["day"], id=r["id"], quality=r["data"]["quality"], provenance=r["data"]["provenance"],
                superseded=r["superseded"], matches_footprint=r["matches_footprint"], source=r["data"]["source"]) for r in review["records"]], hide_index=True)
            st.download_button("Download surface-water records and review", json.dumps(review, indent=2),
                file_name=f"surface-water-{saved['id']}.json", mime="application/json", key=p+"download")
        for row in review["applied"]:
            if row["needs_review"]:
                st.warning(f"Applied surface water {row['record']['day']} ({row['record']['id']}) was corrected or rejected. "
                           "Replay the affected calculation before continuing its state.")
        dates = {d["day"] for d in saved["inputs"]["days"]}
        eligible = {i: r for i, r in current.items() if r["matches_footprint"] and r["data"]["quality"] != "rejected" and r["day"] in dates}
        key = p+"records"
        if key in st.session_state:
            st.session_state[key] = [i for i in st.session_state[key] if i in eligible]
        with st.form(p+"replay_form"):
            st.multiselect("Surface-water records to replay", list(eligible), key=key,
                format_func=lambda i: f"{eligible[i]['day']} · {eligible[i]['data']['provenance']} · {i}")
            st.selectbox("Weather evidence for surface replay", ["observed_only", "allow_historical"], key=p+"policy")
            st.caption("Replay replaces selected patch supplies, preserves crop/soil parameters and weather, and saves a child calculation. "
                       "Review runoff and evaporation/crop coefficients for these actual conditions before replaying.")

            def submit():
                if st.session_state.get("field_plan") != saved["id"]:
                    st.session_state.field_error = "The selected calculation changed; review the replay selection."
                    return
                if st.session_state.get("field_cycle") != saved["cycle_id"]:
                    st.session_state.field_error = "The selected crop cycle changed; review the replay selection."
                    return
                save_revision("replay_surface_water", dict(calculation_id=saved["id"], record_ids=st.session_state[key],
                    weather_policy=st.session_state[p+"policy"]))

            st.form_submit_button("Replay actual surface water as a new calculation", on_click=submit)
