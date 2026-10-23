"""Schema and human-readable report for soil-profile storage assessments."""


def soil_profile_input_schema():
    def obj(fields):
        return dict(type="object", additionalProperties=False, required=list(fields), properties=fields)
    string = dict(type="string")
    decimal = dict(type="string", pattern=r"^(0|[1-9][0-9]{0,14})(\.[0-9]{1,6})?$")
    return obj(dict(schema_version=dict(type="integer", const=1), site=obj(dict(name=string, timezone=string)),
        profile=obj(dict(name=string, observed_at=dict(type="string", format="date-time"),
            kind=dict(type="string", enum=["observed", "scenario"]), root_depth_m=decimal, representation_note=string, source=string)),
        layers=dict(type="array", minItems=1, maxItems=100, items=obj(dict(id=string, top_m=decimal, bottom_m=decimal,
            field_capacity=decimal, wilting_point=decimal, water_content=dict(anyOf=[decimal, dict(type="null")]),
            soil_source=string, water_source=string, provenance=dict(type="string", enum=["measured", "reported", "estimated"]),
            quality=dict(type="string", enum=["unverified", "checked", "rejected"]))))))


def render_soil_profile_report(result):
    lines = ["Agronaut soil-profile storage assessment — EXPERIMENTAL",
             f"{result['inputs']['profile']['name']} · {result['inputs']['profile']['kind']} · {result['observed_at_local']}",
             f"Represented root depth: {result['root_depth_m']['value']} m"]
    for key, value in result["root_zone"].items():
        lines.append(key.removesuffix("_mm").replace("_", " ")+": "+("unknown / withheld" if value is None else value["value"]+" mm"))
    for row in result["layers"]:
        lines.append(f"Layer {row['id']}: rooted thickness {row['rooted_thickness_m']['value']} m; {row['reading_status']}")
        for key in ("total_available_water_mm", "water_storage_mm", "signed_deficit_mm"):
            value = row["values"].get(key)
            lines.append("  "+key.removesuffix("_mm").replace("_", " ")+": "+("unknown / withheld" if value is None else value["value"]+" mm"))
    for label in ("notes", "missing_inputs", "assumptions", "limitations", "sources"):
        lines.extend(["", label.replace("_", " ").title()+":", *("- "+row for row in result[label])])
    return "\n".join(lines)
