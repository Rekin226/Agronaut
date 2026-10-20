"""Read-only comparison of explicit crop-cycle evidence, without ranking."""

import json

import streamlit as st

from .harvest_review_ui import harvest_review_rows
from .resource_outcome_ui import comparison_rows
from .soil_report_ui import soil_result_rows


def render_season_comparison(call, cycles):
    if len(cycles) < 2:
        return
    with st.expander("Compare recorded seasons"):
        st.caption("Choose 2–10 plantings in this unit. Each keeps its dates, crop, units and evidence groups. "
                   "Different crops, areas, weather and record coverage limit what these totals can tell you.")
        selector = "crop_compare_cycles"
        st.session_state[selector] = [key for key in st.session_state.get(selector, []) if key in cycles][:10]
        selected = st.multiselect("Seasons to compare", list(cycles), key=selector, max_selections=10,
            format_func=lambda key: f"{cycles[key]['crop']} · planted {cycles[key]['planted_on']} · {key[:8]}")
        if len(selected) < 2:
            st.info("Select at least two crop cycles to view recorded outcomes.")
            return
        try:
            comparison = call("compare_seasons", dict(cycle_ids=selected))
        except ValueError as exc:
            st.error(str(exc))
            return
        periods, harvests, cash, water, fertilizer, irrigation, soils, harvest_reviews = [], [], [], [], [], [], [], []
        for season in comparison["seasons"]:
            cycle = season["cycle"]
            identity = dict(cycle_id=cycle["id"], crop=cycle["crop"], planted_on=cycle["planted_on"])
            coverage = season["water"]["coverage"]
            periods.append(dict(identity, status=season["lifecycle"]["status"], through=season["period"]["through"],
                harvest_history="recorded" if season["harvest_totals"] else "unknown",
                cash_history="recorded" if season["cash"]["totals"] else "unknown",
                planned_tasks=season["calendar"]["counts"]["planned"],
                completed_task_reports=season["calendar"]["counts"]["completed"],
                tasks_needing_review=season["calendar"]["counts"]["needs_review"],
                current_irrigation_reviews=season["irrigation_outcomes"]["counts"]["current"],
                current_soil_reports=len(season["soil_tests"]["current"]),
                current_harvest_area_reviews=len(season["harvest_reviews"]["current"]),
                irrigation_reviews_needing_review=season["irrigation_outcomes"]["counts"]["current_needing_review"],
                accepted_water_days=coverage["recorded_days"] if coverage else None,
                missing_water_days=coverage["missing_days"] if coverage else None))
            harvests.extend(dict(identity, **row) for row in season["harvest_totals"])
            harvest_reviews.extend(dict(identity, **row) for report in season["harvest_reviews"]["current"] for row in harvest_review_rows(report))
            soils.extend(dict(identity, **row) for report in season["soil_tests"]["current"] for row in soil_result_rows(report))
            cash.extend(dict(identity, **{k: v for k, v in row.items() if k != "record_ids"}) for row in season["cash"]["totals"])
            water.extend(dict(identity, **{k: v for k, v in row.items() if k != "record_ids"}) for row in season["water"]["totals"])
            for review in season["irrigation_outcomes"]["reviews"]:
                irrigation.extend(dict(identity, review_id=review["id"], plan_id=review["plan_id"], job_id=review["job_id"],
                    actual_day=review["actual_day"], planned_day=review["planned_day"], timezone=review["timezone"],
                    needs_review=review["review"]["needs_review"], **metric) for metric in comparison_rows(review))
            for group in season["fertilizer"]["groups"]:
                for nutrient, amounts in group["nutrients"].items():
                    fertilizer.append(dict(identity, phase=group["phase"], provenance=group["provenance"], nutrient=nutrient.upper(),
                        known_label_kg=amounts["known_product_kg"],
                        total_label_kg=amounts["declared_total_kg"] if amounts["declared_total_kg"] is not None else "unknown"))
        st.dataframe(periods)
        for title, rows, caption in (
            ("Recorded harvests", harvests, "Harvest mass/count, product, basis, grade and provenance stay separate; no yield per area is inferred."),
            ("Harvest per stated area", harvest_reviews, "Each review keeps its batches, dates, ground footprint, collection coverage and change flags. "
             "Alternative reviews may overlap. Complete is an operator statement for this footprint and period; no full-season, causal or ranking claim."),
            ("Recorded cash", cash, "Each currency and provenance stays separate. Net recorded cash is receipts minus payments, not profit. "
             "Cash dates may extend before planting or after crop closure. Missing expenses and receipts remain unknown."),
            ("Recorded water", water, "Depths cover accepted completed daily records only. Compare coverage before interpreting totals."),
            ("Laboratory soil reports", soils, "Current assigned transcriptions only. Methods, units, depths and dates remain separate; "
             "no conversion, averaging or fertilizer interpretation. Unknowns and detection qualifiers remain explicit. Open the season for revision history."),
            ("Irrigation event comparisons", irrigation, "Current reviews of alternative plans stay separate. Review counts are not event counts. "
             "Source-water and pump-time differences are not savings and do not add to daily net-water totals or coverage. "
             "No review means unknown comparison history. Open each season for full correction history and evidence."),
            ("Recorded fertilizer", fertilizer, "Label nutrient masses, with unknown contributions retained. Preplant and in-crop records stay separate.")):
            if rows:
                st.subheader(title)
                st.caption(caption)
                st.dataframe(rows)
        for limit in comparison["limits"]:
            st.caption(limit)
        st.download_button("Download season comparison", json.dumps(comparison, indent=2),
                           file_name="season-comparison.json", mime="application/json", key="crop_compare_download")
