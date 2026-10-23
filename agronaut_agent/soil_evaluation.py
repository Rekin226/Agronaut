"""Read-only, owned comparisons against explicitly represented soil observations."""

from dataclasses import asdict
from datetime import datetime, timezone

from agro_model.coefficients import FAO_SOIL
from agro_model.resources import _object
from agro_model.soil_evaluation import compare_soil_state, summarize_errors
from agro_model.soil_state import SoilStateUpdate
from agro_model.validation import choice, text
from agro_model.water import Soil, SoilState

from .field_io import _date, json_ready
from .field_workflow import _read, _scope
from .observation_quality import reviewed_observation
from .prediction_timing import (
    SOIL_TIMING_LIMITATION,
    observation_timing_counts,
    prediction_observation_timing,
    render_observation_timing,
)


def compare_soil(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note):
    choice("dataset_kind", dataset_kind, ("synthetic", "field"))
    text("selection_note", selection_note)
    if not isinstance(comparisons, list) or not 1 <= len(comparisons) <= 1000:
        raise ValueError("comparisons must contain 1–1000 explicitly selected observations")
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        saved = _read(conn, "field_calculations", unit_id, calculation_id)
        if saved["task"] not in ("root_zone_budget", "coupled_soil_water"):
            raise ValueError("soil prediction comparison requires a root_zone_budget or coupled_soil_water calculation")
        tz, _ = _scope(conn, owner, unit_id, saved["cycle_id"])
        if saved["inputs"]["site"]["timezone"] != tz:
            raise ValueError("comparison timezone must match the current farm timezone")
        if saved["task"] == "coupled_soil_water":
            from .coupled_soil_evaluation import compare_coupled_soil
            return compare_coupled_soil(conn, saved, comparisons, dataset_kind, selection_note, tz)
        predictions = {row["day"]: row for row in saved["result"]["result"]["days"]}
        identifiers, dates, rows = set(), set(), []
        for supplied in comparisons:
            entry = _object(supplied, ("observation_id", "as_of", "represented_depth_m", "representation_note",
                "depletion_adjustment_mm", "adjustment_source", "expected_review"), "soil comparison")
            day = _date(entry["as_of"])
            identifier = entry["observation_id"]
            text("observation_id", identifier)
            if identifier in identifiers or day in dates:
                raise ValueError("select each observation and end-of-day boundary only once")
            identifiers.add(identifier)
            dates.add(day)
            original = _read(conn, "field_observations", unit_id, identifier)
            if original["cycle_id"] is not None and original["cycle_id"] != saved["cycle_id"]:
                raise ValueError("comparison observation belongs to another crop cycle")
            observation = reviewed_observation(conn, original)
            review = observation["quality_review"]
            if entry["expected_review"] != (review["id"] if review else None):
                raise ValueError("measurement review changed; review_soil and supply the current expected_review")
            reading = SoilStateUpdate(day, datetime.fromisoformat(original["observed_at"]), tz,
                original["quantity"], original["value"], original["unit"], entry["represented_depth_m"],
                entry["representation_note"], entry["depletion_adjustment_mm"], entry["adjustment_source"],
                original["source"], original["provenance"], observation["effective_quality"])
            if reading.effective_at > datetime.now(timezone.utc):
                raise ValueError("soil prediction comparison requires a completed local day")
            prediction = predictions.get(day.isoformat())
            if prediction is None or prediction.get("balance") is None:
                raise ValueError("no modeled soil prediction exists for the selected day")
            balance = prediction["balance"]
            soil = Soil(**balance["inputs"]["soil"])
            values = compare_soil_state(soil, SoilState(day, balance["values"]["depletion_mm"], "Saved pre-reconciliation prediction"), reading)
            prior_updates = [row["as_of"] for row in saved["inputs"].get("soil_state_updates", []) if row["as_of"] < day.isoformat()]
            reused = [row["as_of"] for row in saved.get("soil_state_evidence", [])
                      if row["observation"]["id"] == identifier and row["as_of"] < day.isoformat()]
            rows.append(dict(as_of=day.isoformat(), observation=observation, comparison_input=entry,
                observation_timing=prediction_observation_timing(saved.get('created_at'), original['observed_at'], reading.effective_at),
                represented_reading=asdict(reading), soil=asdict(soil), values=values,
                weather_kind=prediction["weather_kind"], same_day_state_update="soil_state_update" in prediction,
                prior_state_update_dates=prior_updates, known_target_reuse_dates=reused,
                included_in_metrics=not reused,
                exclusion_reason="Selected observation already initialized this prediction" if reused else None))
        rows.sort(key=lambda row: row["as_of"])
        included = [row for row in rows if row["included_in_metrics"]]
        groups = {}
        for row in included:
            obs = row["observation"]
            key = (obs["provenance"], obs["effective_quality"], row["weather_kind"])
            groups.setdefault(key, []).append(row["values"]["error_mm"])
        return json_ready(dict(schema_version=2, report_type="soil_prediction_diagnostic", evidence_status="diagnostic",
            observation_timing_counts=observation_timing_counts(rows),
            generated_at=datetime.now(timezone.utc).isoformat(), calculation=saved,
            declaration=dict(dataset_kind=dataset_kind, selection_note=selection_note),
            comparisons=rows, metrics=summarize_errors(row["values"]["error_mm"] for row in included),
            groups=[dict(provenance=key[0], quality=key[1], weather_kind=key[2], metrics=summarize_errors(errors))
                    for key, errors in sorted(groups.items())], selected_count=len(rows), excluded_count=len(rows)-len(included),
            independence_status="not_established", sources=[FAO_SOIL],
            assumptions=["Compare the saved end-of-day water-balance prediction before any same-day soil-state reconciliation.",
                         "Error is prediction minus adjusted reading; positive bias means the model predicts drier soil.",
                         "Each included day has equal weight. MAE is mean absolute error; RMSE is root mean square error. Metrics have units mm."],
            limitations=[SOIL_TIMING_LIMITATION,
                         "This is a diagnostic report, not independent field validation or proof of acceptable irrigation performance.",
                         "Dataset kind and selection rationale are operator declarations; source authenticity and sampling independence are not established.",
                         "Readings must represent the entire modeled root depth and field area. Timing adjustments and model soil properties affect converted depletion.",
                         "Known prior reuse of a selected observation excludes it from aggregate metrics. Undeclared reuse, parameter calibration and selection bias cannot be detected automatically.",
                         "Prior state updates condition later predictions. Consecutive observations may be correlated; counts are not independent sample sizes.",
                         "Metrics cover selected dates only, separated by provenance, quality and weather kind. Missing dates are not zero error; no acceptance threshold or uncertainty interval is inferred.",
                         "This read-only comparison does not change model state, observation quality, water logs or saved calculations."]))


def render_soil_comparison(report):
    metrics = report["metrics"]
    lines = ["Agronaut soil prediction comparison — DIAGNOSTIC",
             f"Declared dataset: {report['declaration']['dataset_kind']}; independence: not established.",
             f"Selected {report['selected_count']} days; included {metrics['count']}; excluded {report['excluded_count']}."]
    lines.append(render_observation_timing(report))
    if metrics["count"]:
        lines.append(f"Bias {metrics['bias_mm']:.3f} mm; MAE {metrics['mae_mm']:.3f} mm; RMSE {metrics['rmse_mm']:.3f} mm; "
                     f"maximum absolute error {metrics['maximum_absolute_error_mm']:.3f} mm.")
    else:
        lines.append("No eligible comparisons remain for aggregate metrics.")
    for row in report["comparisons"]:
        values = row["values"]
        obs = row["observation"]
        lines.append(f"{row['as_of']} · {obs['id']} · {obs['provenance']}, {obs['effective_quality']} · {row['weather_kind']}: "
                     f"predicted {values['predicted_depletion_mm']:.3f} mm; reference {values['reference_depletion_mm']:.3f} mm; "
                     f"error {values['error_mm']:.3f} mm.")
        if row["same_day_state_update"]:
            lines.append("  Uses the prediction before the same-day state update.")
        if row["exclusion_reason"]:
            lines.append("  Excluded: "+row["exclusion_reason"])
    for label in ("assumptions", "limitations"):
        lines.extend(["", label.title()+":", *("- "+item for item in report[label])])
    return "\n".join(lines)
