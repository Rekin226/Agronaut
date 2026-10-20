"""Schema and report for the standard-library drip-emitter evaluation."""

from agro_model.emitter_test import evaluate_emitter_test


def emitter_input_schema():
    def obj(properties):
        return dict(type="object", additionalProperties=False, required=list(properties), properties=properties)
    string = dict(type="string")
    return obj(dict(schema_version=dict(type="integer", const=1), site=obj(dict(name=string, timezone=string)),
        event=obj(dict(zone_name=string, started_at=dict(type="string", format="date-time"), ended_at=dict(type="string", format="date-time"),
            kind=dict(type="string", enum=["observed", "scenario"]), stable_operation=dict(type="boolean", const=True), conditions_source=string)),
        sampling=obj(dict(expected_count=dict(type="integer", minimum=4, maximum=2500, multipleOf=4),
            same_emitter_type=dict(type="boolean", const=True), equal_representation=dict(type="boolean", const=True), source=string)),
        samples=dict(type="array", minItems=4, maxItems=2500, items=obj(dict(id=string, lateral=string, position=string,
            volume_ml=dict(type=["number", "null"], minimum=0, maximum=1000000),
            duration_seconds=dict(type="number", minimum=1, maximum=86400),
            pressure_kpa=dict(type=["number", "null"], minimum=0, maximum=10000), source=string)))))


def evaluate_emitter_request(payload):
    return evaluate_emitter_test(payload)


def render_emitter_report(result):
    lines = ["Agronaut drip-emitter evaluation — EXPERIMENTAL",
        f"{result['inputs']['site']['name']} · {result['inputs']['event']['zone_name']} · {result['inputs']['event']['kind']}",
        f"Expected emitters: {result['sample_count']}"]
    for key, value in result["statistics"].items():
        lines.append(key.replace("_", " ")+": "+("unknown / undefined" if value is None else f"{value:.6g}"))
    lines.append("Sample flows (L/h), with operating pressure (kPa):")
    for row in result["samples"]:
        flow = "unknown" if row["flow_l_h"] is None else f"{row['flow_l_h']:.6g}"
        pressure = "unknown" if row["pressure_kpa"] is None else str(row["pressure_kpa"])
        lines.append(f"{row['id']} · {row['lateral']} / {row['position']}: {flow}; pressure {pressure}")
    for label, key in (("Notes", "notes"), ("Missing inputs", "missing_inputs"), ("Assumptions", "assumptions"),
                       ("Limits", "limitations"), ("Sources", "sources")):
        lines.extend(["", label+":", *("- "+row for row in result[key])])
    return "\n".join(lines)
