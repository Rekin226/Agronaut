"""Owned read-only comparisons of surface-advance scenarios and recorded events."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.resources import _object
from agro_model.surface_diagnostics import (
    AdvanceReading,
    compare_advance_anchors,
    summarize_advance_errors,
)
from agro_model.validation import choice, text

from .cycle_lifecycle import ensure_cycle_day
from .field_workflow import _read, _scope
from .prediction_timing import (
    LIMITATION,
    prediction_event_timing,
    render_timing,
    timing_counts,
)


def compare_surface_advance(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note):
    choice("dataset_kind", dataset_kind, ("synthetic", "field"))
    text("selection_note", selection_note)
    if not isinstance(comparisons, list) or not 1 <= len(comparisons) <= 100:
        raise ValueError("comparisons must select 1–100 surface event calculations")
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        saved = _read(conn, "field_calculations", unit_id, calculation_id)
        if saved["task"] != "surface_advance":
            raise ValueError("comparison requires a saved surface_advance scenario")
        result = saved["result"]["result"]
        prediction = result["prediction"]
        if prediction is None:
            raise ValueError("comparison requires a solved advance prediction")
        tz, cycle = _scope(conn, owner, unit_id, saved["cycle_id"])
        if saved["inputs"]["site"]["timezone"] != tz:
            raise ValueError("comparison timezone must match the current farm timezone")
        geometry = saved["inputs"]["geometry"]
        q = saved["inputs"]["hydraulics"]["inflow_lps"]
        identifiers, starts, rows, record_count = set(), set(), [], 0
        for supplied in comparisons:
            entry = _object(supplied, ("event_calculation_id", "independence", "representation_note"), "surface comparison")
            identifier = entry["event_calculation_id"]
            text("event_calculation_id", identifier)
            text("representation_note", entry["representation_note"])
            choice("independence", entry["independence"], ("held_out", "used_for_calibration", "unknown"))
            if identifier in identifiers:
                raise ValueError("select each surface event calculation only once")
            identifiers.add(identifier)
            reference = _read(conn, "field_calculations", unit_id, identifier)
            if reference["task"] != "surface_evaluation" or reference["cycle_id"] != saved["cycle_id"]:
                raise ValueError("reference must be a surface_evaluation in the same unit and crop cycle")
            inputs = reference["inputs"]
            if inputs["site"]["timezone"] != tz:
                raise ValueError("reference event timezone must match the farm timezone")
            if any(inputs["geometry"][key] != geometry[key] for key in ("method", "length_m", "width_m", "slope_m_m")):
                raise ValueError("reference event geometry must match the predicted strip exactly")
            event = inputs["event"]
            if dataset_kind == "field" and event["kind"] != "observed":
                raise ValueError("field comparisons require observed events; scenario references are synthetic")
            start = datetime.fromisoformat(event["started_at"])
            end = datetime.fromisoformat(reference["result"]["result"]["event_ended_at"])
            if event["kind"] == "observed" and end > datetime.now(timezone.utc):
                raise ValueError("observed comparisons require completed event monitoring")
            start_key = start.astimezone(timezone.utc)
            if start_key in starts:
                raise ValueError("multiple evaluations of the same event start cannot count as separate events")
            starts.add(start_key)
            if saved["cycle_id"] is not None:
                for stamp in (start, end):
                    day = stamp.astimezone(ZoneInfo(tz)).date().isoformat()
                    if day < cycle["planted_on"]:
                        raise ValueError("reference event precedes the current crop cycle planting date")
                    ensure_cycle_day(conn, unit_id, saved["cycle_id"], day)
            record_count += sum(len(inputs[key]) for key in ("stations", "inflow", "tailwater"))
            if record_count > 20000:
                raise ValueError("selected events exceed 20000 flow/station records; compare smaller batches")
            arrival = inputs["stations"][-1]["advance_min"]
            if arrival <= 0 or event["cutoff_min"] < arrival:
                raise ValueError("reference must reach full length during positive inflow, before or at cutoff")
            # Do not require continued inflow through the PREDICTED arrival. That
            # would preferentially discard slow predictions after a fast actual advance.
            advance_flows = [flow for flow in inputs["inflow"] if flow["start_min"] < arrival]
            if any(flow["mean_lps"] != q for flow in advance_flows):
                raise ValueError("reference interval-mean inflow must equal modeled constant inflow through observed advance")
            readings = tuple(AdvanceReading(row["distance_m"], row["advance_min"], row["source"])
                             for row in inputs["stations"])
            anchors = compare_advance_anchors(geometry["length_m"], prediction["half_length_advance_min"],
                                               prediction["full_length_advance_min"], readings)
            included = entry["independence"] == "held_out"
            rows.append(dict(event_calculation=reference, event_start_utc=start_key.isoformat(), comparison_input=entry, anchors=anchors,
                             prediction_timing=prediction_event_timing(saved.get('created_at'), start, end),
                             included_in_metrics=included,
                             exclusion_reason=None if included else entry["independence"],
                             reference_water_balance_accepted=reference["result"]["result"]["water_balance_accepted"],
                             unused_station_count=len(readings)-sum(row["status"] == "compared" for row in anchors)))
        rows.sort(key=lambda row: (row["event_start_utc"], row["event_calculation"]["id"]))
        metrics = {anchor: summarize_advance_errors(
            pair["error_min"] for row in rows if row["included_in_metrics"]
            for pair in row["anchors"] if pair["anchor"] == anchor and pair["error_min"] is not None)
            for anchor in ("half_length", "full_length")}
        return dict(schema_version=2, report_type="surface_advance_diagnostic", evidence_status="diagnostic",
                    timing_counts=timing_counts(rows),
                    generated_at=datetime.now(timezone.utc).isoformat(), calculation=saved,
                    declaration=dict(dataset_kind=dataset_kind, selection_note=selection_note), comparisons=rows,
                    metrics_by_anchor=metrics, selected_event_count=len(rows),
                    included_event_count=sum(row["included_in_metrics"] for row in rows),
                    independence_status="not_established", acceptance_status="not_assessed",
                    assumptions=[
                        "Error is predicted minus recorded arrival time, in minutes; positive means the model predicts slower advance.",
                        "Each declared held-out event has equal weight within each anchor's metrics. Missing exact half-length stations are not interpolated or counted as zero error.",
                        "Geometry and interval-mean inflows match exactly; representation notes declare that the records describe the same physical strip and conditions.",
                        "Post-event modeled water-balance acceptance does not select arrival observations or determine measurement quality.",
                    ], limitations=[
                        LIMITATION,
                        "Diagnostic comparison, not independent field validation, threshold acceptance or authorization to operate irrigation.",
                        "Field/synthetic labels, representation and held-out status are declarations. Authenticity, measurement quality, uncertainty and undisclosed calibration reuse are not verified.",
                        "Only half/full-length anchors are compared; intermediate measurements and the trivial inlet are not scored. No surface-storage, recession or runoff validation is performed.",
                        "Interval means do not establish within-interval flow constancy. Model assumptions about normal flow, uniform soil and backwater still require review.",
                        "Events and anchors can be correlated; counts are not statistically independent sample sizes. No confidence interval or regional applicability is inferred.",
                        "The same saved prediction is compared with selected compatible events. Exclusions and missing stations remain visible; selection bias cannot be ruled out.",
                        "This read-only report changes no predictions, coefficients, observation records, water logs or capability evidence labels.",
                    ])


def render_surface_advance_comparison(report):
    lines = ["Surface advance comparison — DIAGNOSTIC",
             f"Dataset: {report['declaration']['dataset_kind']}; selected events: {report['selected_event_count']}; "
             f"declared held out: {report['included_event_count']}",
             "Positive error means predicted arrival is later. Independence and acceptance are not established."]
    lines.append(render_timing(report))
    for anchor, values in report["metrics_by_anchor"].items():
        if values["count"]:
            lines.append(f"{anchor}: {values['count']} events; bias {values['bias_min']:.3f} min; "
                         f"MAE {values['mae_min']:.3f} min; RMSE {values['rmse_min']:.3f} min")
        else:
            lines.append(f"{anchor}: no eligible paired observations; metrics unknown")
    for title, key in (("Assumptions", "assumptions"), ("Limits", "limitations")):
        lines.extend(["", title+":", *(f"- {item}" for item in report[key])])
    return "\n".join(lines)
