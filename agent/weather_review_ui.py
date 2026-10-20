"""Review saved weather timestamps with an explicitly chosen age limit."""

import json

import streamlit as st


def render_weather_review(call, saved):
    if not saved["inputs"].get("weather"):
        return
    with st.expander("Review saved weather age"):
        st.caption("Choose an age limit for forecast timestamps. This is your review policy, "
                   "not a forecast accuracy test. Nothing is fetched or changed.")
        limit = st.number_input("Maximum forecast age (hours)", min_value=0.0, value=None,
                                key="field_weather_review_limit")
        signature = (saved["id"], limit)
        if st.session_state.get("field_weather_review_signature") != signature:
            st.session_state.pop("field_weather_review_report", None)
            st.session_state.field_weather_review_signature = signature
        if st.button("Review weather now", key="field_weather_review_run"):
            st.session_state.pop("field_weather_review_report", None)
            try:
                report = call("review_weather", dict(calculation_id=saved["id"], max_age_hours=limit))
                st.session_state.field_weather_review_report = report
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        report = st.session_state.get("field_weather_review_report")
        if report:
            st.caption(f"Reviewed at {report['as_of']} · saved site timezone {report['site_timezone']}. "
                       "Click Review weather now to update this snapshot.")
            if report["status"] == "needs_review":
                st.warning(f"{report['flagged_day_count']} weather day(s) need review.")
            for issue in report["issues"]:
                st.warning(issue.replace("_", " "))
            st.dataframe([dict(row, flags=", ".join(flag.replace("_", " ") for flag in row["flags"]))
                          for row in report["rows"]], hide_index=True)
            st.caption(report["note"])
            st.download_button("Download weather age review", json.dumps(report, indent=2),
                               file_name="weather-age-review.json", mime="application/json",
                               key="field_weather_review_download")
