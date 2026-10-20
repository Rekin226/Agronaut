"""Read-only comparisons of saved coupled profiles with represented observations."""

from datetime import datetime, time, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from agro_model.resources import _object
from agro_model.soil_evaluation import interval_water_content, summarize_water_content_errors
from agro_model.soil_storage import SoilCompartment, SoilPatch
from agro_model.validation import aware_time, choice, number, text

from .coupled_water_io import _decimal, _json_value
from .field_io import _date
from .field_workflow import _read
from .observation_quality import reviewed_observation
from .prediction_timing import (
    SOIL_TIMING_LIMITATION,
    observation_timing_counts,
    prediction_observation_timing,
    render_observation_timing,
)


def compare_coupled_soil(conn, saved, comparisons, dataset_kind, selection_note, tz):
    predictions = {row["day"]: row for row in saved["result"]["result"]["days"]}
    zone = ZoneInfo(tz)
    rows, identifiers = [], set()
    for supplied in comparisons:
        entry = _object(supplied, ("observation_id", "as_of", "patch_id", "top_m", "bottom_m",
            "representation_note", "water_content_adjustment", "adjustment_source", "expected_review", "independence"), "coupled soil comparison")
        for key in ("observation_id", "patch_id", "representation_note", "adjustment_source"):
            text(key, entry[key])
        choice("independence", entry["independence"], ("held_out", "used_for_initialization", "used_for_calibration", "unknown"))
        identifier = entry["observation_id"]
        if identifier in identifiers:
            raise ValueError("select each observation only once")
        identifiers.add(identifier)
        original = _read(conn, "field_observations", saved["unit_id"], identifier)
        if original["cycle_id"] is not None and original["cycle_id"] != saved["cycle_id"]:
            raise ValueError("comparison observation belongs to another crop cycle")
        observation = reviewed_observation(conn, original)
        review = observation["quality_review"]
        if entry["expected_review"] != (review["id"] if review else None):
            raise ValueError("measurement review changed; review_soil and supply the current expected_review")
        if original["quantity"] != "soil_water_content" or original["unit"] != "m3/m3":
            raise ValueError("coupled soil comparison requires soil_water_content in m3/m3")
        choice("soil provenance", original["provenance"], ("measured", "reported", "estimated"))
        choice("soil quality", observation["effective_quality"], ("unverified", "checked"))
        number("soil water content", original["value"], 0, 1)
        number("water_content_adjustment", entry["water_content_adjustment"], -1, 1)
        day = _date(entry["as_of"])
        try:
            start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
            closing = datetime.combine(day+timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("comparison date exceeds supported calendar boundaries") from exc
        stamp = aware_time("observed_at", datetime.fromisoformat(original["observed_at"])).astimezone(timezone.utc)
        if closing <= start or not start <= stamp <= closing:
            raise ValueError("soil reading must fall within the as_of local day or exactly its closing midnight")
        if closing > datetime.now(timezone.utc):
            raise ValueError("soil comparison requires a completed local day")
        prediction = predictions.get(day.isoformat())
        if prediction is None:
            raise ValueError("no modeled soil prediction exists for the selected day")
        raw = next((p for p in prediction["ending_soil"]["patches"] if p["id"] == entry["patch_id"]), None)
        if raw is None:
            raise ValueError("sample patch must exist in the selected prediction")
        patch = SoilPatch(raw["id"], Fraction(raw["area_fraction"]["exact"]), tuple(SoilCompartment(**{
            k: v if k in ("id", "soil_source") else Fraction(v["exact"]) for k, v in cell.items()
        }) for cell in raw["compartments"]))
        interval = interval_water_content(patch, _decimal(entry["top_m"]), _decimal(entry["bottom_m"]))
        reading = Fraction(str(original["value"]))
        adjustment = Fraction(str(entry["water_content_adjustment"]))
        reference = reading+adjustment
        if not 0 <= reference <= 1:
            raise ValueError("adjusted soil water content must lie within 0–1; it is never clipped")
        predicted = interval["water_content"]
        values = dict(predicted_water_content=predicted, reading_water_content=reading,
            water_content_adjustment=adjustment, reference_water_content=reference,
            error=predicted-reference, absolute_error=abs(predicted-reference))
        reused = sorted({e["as_of"] for e in saved.get("coupled_state_evidence", [])
                         if e["observation"]["id"] == identifier and e["as_of"] < day.isoformat()})
        included = entry["independence"] == "held_out" and not reused
        rows.append(dict(as_of=day.isoformat(), observation=observation, comparison_input=entry,
            observation_timing=prediction_observation_timing(saved.get('created_at'), original['observed_at'], closing),
            represented_interval=_json_value(interval), values=_json_value(values),
            weather_kind=prediction["weather_kind"], included_in_metrics=included,
            known_target_reuse_dates=reused,
            exclusion_reason="Observation already conditioned this prediction's starting state." if reused else
                None if included else "Observation is not declared held out from initialization and calibration."))
    rows.sort(key=lambda row: (row["as_of"], row["comparison_input"]["patch_id"], row["observation"]["id"]))
    included = [row for row in rows if row["included_in_metrics"]]
    groups = {}
    for row in included:
        obs, entry, interval = row["observation"], row["comparison_input"], row["represented_interval"]
        key = (entry["patch_id"], interval["top_m"]["exact"], interval["bottom_m"]["exact"],
               obs["provenance"], obs["effective_quality"], row["weather_kind"])
        groups.setdefault(key, []).append(float(Fraction(row["values"]["error"]["exact"])))
    return dict(schema_version=2, report_type="coupled_soil_prediction_diagnostic", evidence_status="diagnostic",
        observation_timing_counts=observation_timing_counts(rows),
        generated_at=datetime.now(timezone.utc).isoformat(), calculation=saved,
        declaration=dict(dataset_kind=dataset_kind, selection_note=selection_note), comparisons=rows,
        metrics=summarize_water_content_errors(float(Fraction(row["values"]["error"]["exact"])) for row in included),
        groups=[dict(patch_id=k[0], top_m_exact=k[1], bottom_m_exact=k[2], provenance=k[3], quality=k[4], weather_kind=k[5],
                     metrics=summarize_water_content_errors(v)) for k, v in sorted(groups.items())],
        selected_count=len(rows), excluded_count=len(rows)-len(included), independence_status="not_established",
        assumptions=["Predicted water content is the depth-weighted mean over the declared interval in one selected patch; each modeled cell is uniform.",
                     "The observation must represent that interval and the patch footprint as declared in the sampling note; point readings are not automatically averages.",
                     "Signed timing adjustment maps the reading to closing local midnight. Positive adjustment and positive prediction error mean wetter soil.",
                     "Metrics have units m3/m3 and equal weight per included observation, not area, depth or day. Groups separate patch, interval and evidence kind."],
        limitations=[SOIL_TIMING_LIMITATION,
                     "This diagnostic is not independent field validation, an irrigation prescription or a model calibration.",
                     "Only observations declared held out and not known to have conditioned the prediction enter metrics. These declarations, dataset kind and sampling coverage are not independently verified.",
                     "Initial and checkpoint sources can contain undeclared observation reuse. Source text cannot establish held-out status, and correlated readings are not independent sample sizes.",
                     "Sampling uncertainty, sensor calibration and timing uncertainty are not estimated. No acceptance threshold is inferred.",
                     "The comparison uses the saved prediction and its soil properties. Observations outside modeled dry/saturation bounds are retained within physical volumetric range 0–1 to expose disagreement.",
                     "This read-only report does not adopt measurements, revise predictions or change any farm record."])


def render_coupled_soil_comparison(report):
    metrics = report["metrics"]
    lines = ["Agronaut coupled soil prediction comparison — DIAGNOSTIC",
             f"Declared dataset: {report['declaration']['dataset_kind']}; independence: not established.",
             f"Selected {report['selected_count']} readings; included {metrics['count']}; excluded {report['excluded_count']}."]
    lines.append(render_observation_timing(report))
    if metrics["count"]:
        lines.append(f"Bias {metrics['bias']:.6f}; MAE {metrics['mae']:.6f}; RMSE {metrics['rmse']:.6f} m³/m³ (positive error = wetter).")
    else:
        lines.append("No declared held-out comparisons remain for aggregate metrics.")
    for row in report["comparisons"]:
        e, v = row["comparison_input"], row["values"]
        lines.append(f"{row['as_of']} · {e['patch_id']} · {e['top_m']}–{e['bottom_m']} m · {row['observation']['id']}: "
                     f"predicted {v['predicted_water_content']['value']}; reference {v['reference_water_content']['value']}; "
                     f"error {v['error']['value']} m³/m³ · {e['independence']}.")
        if row["exclusion_reason"]:
            lines.append("  Excluded: "+row["exclusion_reason"])
    for label in ("assumptions", "limitations"):
        lines.extend(["", label.title()+":", *("- "+item for item in report[label])])
    return "\n".join(lines)
