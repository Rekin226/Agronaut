"""Shared schema and text report for sprinkler catch-grid evaluations."""

from agro_model.catch_can import evaluate_catch_test


def catch_input_schema():
    def obj(fields):
        return dict(type="object", additionalProperties=False, required=list(fields), properties=fields)
    string, numeric = dict(type="string"), dict(type="number")
    return obj(dict(schema_version=dict(type="integer", const=1),
        site=obj(dict(name=string, timezone=string)),
        event=obj(dict(zone_name=string, started_at=dict(type="string", format="date-time"), duration_seconds=numeric,
            kind=dict(type="string", enum=["observed", "scenario"]), rain_free=dict(type="boolean", const=True),
            same_exposure=dict(type="boolean", const=True), wind_m_s=dict(type=["number", "null"]), conditions_source=string)),
        grid=obj(dict(rows=dict(type="integer", minimum=2, maximum=100), columns=dict(type="integer", minimum=2, maximum=100),
            spacing_x_m=numeric, spacing_y_m=numeric, equal_area=dict(type="boolean", const=True), source=string)),
        catches=dict(type="array", minItems=4, maxItems=2500, items=obj(dict(row=dict(type="integer"), column=dict(type="integer"),
            depth_mm=dict(type=["number", "null"]), source=string)))))


def render_catch_report(result):
    lines = ["Agronaut sprinkler catch-grid evaluation — EXPERIMENTAL",
        f"{result['inputs']['site']['name']} · {result['inputs']['event']['zone_name']} · {result['inputs']['event']['kind']}",
        f"Grid positions: {result['sample_count']}"]
    for key, value in result["statistics"].items():
        lines.append(key.replace("_", " ")+": "+("unknown / undefined" if value is None else f"{value:.6g}"))
    for label, key in (("Notes", "notes"), ("Missing inputs", "missing_inputs"), ("Assumptions", "assumptions"),
                       ("Limits", "limitations"), ("Sources", "sources")):
        lines.extend(["", label+":", *("- "+row for row in result[key])])
    return "\n".join(lines)


def evaluate_catch_request(payload):
    return evaluate_catch_test(payload)
