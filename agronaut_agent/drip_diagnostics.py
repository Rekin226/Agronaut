"""Owned comparisons of immutable drip predictions and emitter-test records."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.drip_diagnostics import compare_emitter, summarize_errors, summarize_events
from agro_model.hydraulics import GRAVITY_M_S2
from agro_model.resources import _object
from agro_model.validation import choice, number, text

from .cycle_lifecycle import ensure_cycle_day
from .field_workflow import _read, _scope
from .prediction_timing import (
    LIMITATION,
    prediction_event_timing,
    render_timing,
    timing_counts,
)

ENTRY_FIELDS = ("test_calculation_id", "lateral", "mappings", "independence", "representation_note",
                "conditions_note", "water_density_kg_m3", "density_source", "pressure_reference")


def compare_drip_lateral(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note):
    return _compare_drip(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note, network=False)


def compare_drip_network(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note):
    return _compare_drip(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note, network=True)


def _pair_metrics(pairs):
    return {key: summarize_errors(pair["errors"][key]["error"] for pair in pairs
            if pair["errors"] is not None and pair["errors"][key]["error"] is not None)
            for key in ("flow_lph", "pressure_kpa")}


def _event_metrics(rows, lateral_id=None):
    return {key: summarize_events([[pair["errors"][key]["error"] for pair in row["pairs"]
            if pair["errors"] is not None and pair["errors"][key]["error"] is not None
            and (lateral_id is None or pair["mapping"]["lateral_id"] == lateral_id)]
            for row in rows if row["included_in_metrics"]]) for key in ("flow_lph", "pressure_kpa")}


def _compare_drip(workflow, owner, unit_id, calculation_id, comparisons, dataset_kind, selection_note, *, network):
    choice("dataset_kind", dataset_kind, ("synthetic", "field"))
    text("selection_note", selection_note)
    if not isinstance(comparisons, list) or not 1 <= len(comparisons) <= 100:
        raise ValueError("comparisons must select 1–100 emitter tests")
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        saved = _read(conn, "field_calculations", unit_id, calculation_id)
        allowed = ("drip_zone", "drip_manifold") if network else ("drip_lateral",)
        if saved["task"] not in allowed or saved["result"]["result"]["prediction"] is None:
            raise ValueError("comparison requires a solved " + "/".join(allowed) + " scenario")
        tz, cycle = _scope(conn, owner, unit_id, saved["cycle_id"])
        if saved["inputs"]["site"]["timezone"] != tz:
            raise ValueError("scenario timezone must match the current farm timezone")
        prediction = saved["result"]["result"]["prediction"]
        if network:
            branches = ({row["identifier"]: row["result"] for row in prediction["laterals"]}
                        if saved["task"] == "drip_zone" else
                        {row["identifier"]: row["lateral_result"] for row in prediction["nodes"]})
            nodes = {(branch, row["identifier"]): row for branch, result in branches.items()
                     for row in result["prediction"]["nodes"]}
        else:
            nodes = {row["identifier"]: row for row in prediction["nodes"]}
        identifiers, starts, rows, sample_count = set(), set(), [], 0
        for supplied in comparisons:
            entry = _object(supplied, tuple(f for f in ENTRY_FIELDS if not network or f != "lateral"), "drip comparison")
            for name in ("test_calculation_id", "lateral", "representation_note", "conditions_note", "density_source"):
                if not network or name != "lateral":
                    text(name, entry[name])
            choice("independence", entry["independence"], ("held_out", "used_for_calibration", "unknown"))
            choice("pressure_reference", entry["pressure_reference"], ("gauge_at_emitter", "unknown"))
            density = entry["water_density_kg_m3"]
            if density is not None:
                number("water_density_kg_m3", density, 500, 2000)
            identifier = entry["test_calculation_id"]
            if identifier in identifiers:
                raise ValueError("select each emitter test only once")
            identifiers.add(identifier)
            reference = _read(conn, "field_calculations", unit_id, identifier)
            if reference["task"] != "drip_emitter_test" or reference["cycle_id"] != saved["cycle_id"]:
                raise ValueError("reference must be a drip_emitter_test in the same unit and crop cycle")
            inputs = reference["inputs"]
            if inputs["site"]["timezone"] != tz:
                raise ValueError("test timezone must match the current farm timezone")
            event = inputs["event"]
            if dataset_kind == "field" and event["kind"] != "observed":
                raise ValueError("field comparisons require observed tests")
            start, end = (datetime.fromisoformat(event[key]) for key in ("started_at", "ended_at"))
            if event["kind"] == "observed" and end > datetime.now(timezone.utc):
                raise ValueError("observed comparison requires completed monitoring")
            instant = start.astimezone(timezone.utc)
            if instant in starts:
                raise ValueError("multiple evaluations with the same event start cannot count as separate tests")
            starts.add(instant)
            if cycle is not None:
                for stamp in (start, end):
                    day = stamp.astimezone(ZoneInfo(tz)).date().isoformat()
                    if day < cycle["planted_on"]:
                        raise ValueError("test precedes the current crop cycle planting date")
                    ensure_cycle_day(conn, unit_id, saved["cycle_id"], day)
            samples = reference["result"]["result"]["samples"]
            sample_count += len(samples)
            if sample_count > 20000:
                raise ValueError("selected tests exceed 20000 samples; compare smaller batches")
            selected = {s["id"]: s for s in samples if network or s["lateral"].strip().casefold() == entry["lateral"].strip().casefold()}
            if not selected:
                raise ValueError("selected lateral has no samples in this test")
            mappings = entry["mappings"]
            if not isinstance(mappings, list) or len(mappings) != len(selected):
                raise ValueError("map every sample in the test" if network else "map every sample in the selected lateral, using null for an unmatched station")
            seen_samples, seen_stations, pairs = set(), set(), []
            recorded_to_modeled, modeled_to_recorded = {}, {}
            for supplied_map in mappings:
                mapping = _object(supplied_map, ("sample_id", "station_id", "mapping_note", "lateral_id") if network
                                  else ("sample_id", "station_id", "mapping_note"), "sample mapping")
                sample_id, station_id = mapping["sample_id"], mapping["station_id"]
                text("sample_id", sample_id)
                text("mapping_note", mapping["mapping_note"])
                if sample_id not in selected or sample_id in seen_samples:
                    raise ValueError("each sample must be mapped exactly once" if network else "each selected-lateral sample must be mapped exactly once")
                seen_samples.add(sample_id)
                sample = selected[sample_id]
                node_key = station_id
                if network:
                    lateral_id = mapping["lateral_id"]
                    if (lateral_id is None) != (station_id is None):
                        raise ValueError("unmatched samples require both lateral_id and station_id null")
                    if lateral_id is not None:
                        text("lateral_id", lateral_id)
                        recorded = sample["lateral"].strip().casefold()
                        if (recorded in recorded_to_modeled and recorded_to_modeled[recorded] != lateral_id
                                or lateral_id in modeled_to_recorded and modeled_to_recorded[lateral_id] != recorded):
                            raise ValueError("recorded and modeled lateral identities must map one-to-one within a test")
                        recorded_to_modeled[recorded] = lateral_id
                        modeled_to_recorded[lateral_id] = recorded
                    node_key = (lateral_id, station_id)
                if station_id is not None:
                    text("station_id", station_id)
                    if node_key not in nodes or node_key in seen_stations:
                        raise ValueError("station must be modeled and mapped at most once per test")
                    seen_stations.add(node_key)
                node = nodes.get(node_key)
                errors = compare_emitter(node["emitter_flow_lph"], node["pressure_head_m"], sample["flow_l_h"],
                                         sample["pressure_kpa"], density, entry["pressure_reference"]) if node is not None else None
                pairs.append(dict(mapping=mapping, sample=sample, predicted_station=node, errors=errors,
                                  status="mapped" if node is not None else "unmatched_station"))
            pairs.sort(key=lambda pair: pair["sample"]["id"])
            included = entry["independence"] == "held_out"
            row = dict(test_calculation=reference, comparison_input=entry, event_start_utc=instant.isoformat(),
                       prediction_timing=prediction_event_timing(saved.get('created_at'), start, end),
                       pairs=pairs, metrics=_pair_metrics(pairs), included_in_metrics=included,
                       exclusion_reason=None if included else entry["independence"])
            if network:
                row.update(unmeasured_stations=[dict(lateral_id=branch, station_id=station)
                                               for branch, station in sorted(set(nodes)-seen_stations)],
                           lateral_metrics={branch: _pair_metrics([pair for pair in pairs if pair["mapping"]["lateral_id"] == branch])
                                            for branch in branches})
            else:
                row.update(other_lateral_sample_ids=[s["id"] for s in samples if s["id"] not in selected],
                           unmeasured_station_ids=sorted(set(nodes)-seen_stations))
            rows.append(row)
        rows.sort(key=lambda row: (row["event_start_utc"], row["test_calculation"]["id"]))
        metrics = _event_metrics(rows)
        return dict(schema_version=2, report_type="drip_network_diagnostic" if network else "drip_lateral_diagnostic", evidence_status="diagnostic",
            timing_counts=timing_counts(rows),
            **(dict(lateral_metrics={branch: _event_metrics(rows, branch) for branch in branches}) if network else {}),
            generated_at=datetime.now(timezone.utc).isoformat(), calculation=saved,
            declaration=dict(dataset_kind=dataset_kind, selection_note=selection_note), comparisons=rows,
            metrics=metrics, selected_test_count=len(rows), included_test_count=sum(r["included_in_metrics"] for r in rows),
            independence_status="not_established", acceptance_status="not_assessed",
            pressure_conversion=dict(formula="pressure_kpa = density_kg_m3 * gravity_m_s2 * head_m / 1000",
                                     gravity_m_s2=GRAVITY_M_S2),
            assumptions=[
                "Error is predicted minus recorded. Flow errors use L/h; pressure errors require declared gauge pressure at the emitter, supplied event water density and conventional standard gravity.",
                "Each declared held-out test with paired readings has equal weight in each aggregate metric. Within a test, known pairs have equal weight; missing values are not zero.",
                ("Mappings declare the same physical emitter locations and lateral identities. Representation and conditions notes must justify geometry, equipment, the complete active branch set, shared source configuration and steady operation." if network else
                 "Mappings declare the same physical emitter locations. Representation and conditions notes must justify geometry, equipment, inlet/source configuration and steady operation."),
            ], limitations=[
                LIMITATION,
                "Diagnostic comparison, not independent field validation, a pass/fail decision, calibration or irrigation advice.",
                "Field labels, held-out status, density, sample placement, measurement quality and matching conditions are operator declarations, not independently verified evidence.",
                ("Every sample requires an explicit lateral/station mapping or an unmatched note. Repeated station IDs on different laterals are distinct; per-lateral metrics and all unmeasured stations remain visible. Overall test metrics weight paired emitters equally, not laterals equally; selective mapping and sampling can still bias the result." if network else
                 "All selected-lateral samples require a mapping or an explicit unmatched note. Other laterals, unmeasured stations and missing readings remain visible; selection bias is not ruled out."),
                "No inlet-pressure or total inlet-flow validation: sampled emitter flows are not extrapolated to a zone total. No pressure-derived prediction is substituted for the saved scenario.",
                "A poor uniformity score, missing sample-wide statistics or a violated scenario limit does not discard known individual readings. Out-of-envelope measured values remain visible.",
                "Samples/tests can be correlated; counts do not establish independent sample sizes, confidence intervals or regional applicability.",
                "This read-only report changes no models, coefficients, records, water logs or capability evidence labels.",
            ])


def render_drip_comparison(report):
    lines = [("Drip network comparison — DIAGNOSTIC" if report["report_type"] == "drip_network_diagnostic" else "Drip lateral comparison — DIAGNOSTIC"),
             f"Dataset: {report['declaration']['dataset_kind']}; selected tests: {report['selected_test_count']}; "
             f"declared held out: {report['included_test_count']}",
             "Error is predicted minus recorded. Independence and acceptance are not established."]
    lines.append(render_timing(report))
    for key, values in report["metrics"].items():
        if values["event_count"]:
            lines.append(f"{key}: {values['event_count']} tests, {values['paired_sample_count']} pairs; "
                         f"bias {values['bias']:.6g}; MAE {values['mae']:.6g}; RMSE {values['rmse']:.6g}")
        else:
            lines.append(f"{key}: no eligible pairs; metrics unknown")
    for lateral, metrics in report.get("lateral_metrics", {}).items():
        for key, values in metrics.items():
            lines.append(f"Lateral {lateral}, {key}: {values['paired_sample_count']} eligible pairs; "
                         + (f"MAE {values['mae']:.6g}" if values['mae'] is not None else "metrics unknown"))
    for title, key in (("Assumptions", "assumptions"), ("Limits", "limitations")):
        lines.extend(["", title+":", *(f"- {item}" for item in report[key])])
    return "\n".join(lines)
