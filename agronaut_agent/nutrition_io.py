"""Shared nutrient-accounting request, discovery schema and human-readable report."""

from dataclasses import asdict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.nutrition import (
    FertilizerProduct,
    NutrientTarget,
    NutritionBudget,
    ProductAnalysis,
    evaluate_nutrition,
)
from agro_model.validation import text

from .field_io import _construct, _mapping


def nutrition_input_schema():
    def obj(properties, required=None):
        return dict(type="object", additionalProperties=False, properties=properties,
                    required=list(properties) if required is None else required)

    string = dict(type="string", minLength=1)
    nullable = dict(type=["number", "null"], minimum=0)
    analysis = obj(dict(n_pct=dict(nullable, maximum=100), p2o5_pct=dict(nullable, maximum=100),
                        k2o_pct=dict(nullable, maximum=100), basis=dict(const="mass_percent"), source=string))
    target = obj(dict(n_kg_ha=dict(nullable, maximum=1e6), p2o5_kg_ha=dict(nullable, maximum=1e6),
                      k2o_kg_ha=dict(nullable, maximum=1e6), basis=dict(const="remaining_application"),
                      source=string, scope_note=string))
    product = obj(dict(id=string, name=string, kind=dict(const="mineral"),
                       quantity=dict(type="number", exclusiveMinimum=0, maximum=1e9), unit=dict(enum=["kg", "L"]),
                       analysis=analysis, density_kg_l=dict(type=["number", "null"], exclusiveMinimum=0, maximum=1000),
                       density_source=dict(type=["string", "null"])),
                  ["id", "name", "kind", "quantity", "unit", "analysis"])
    props = dict(schema_version=dict(type="integer", const=1), site=obj(dict(name=string, timezone=string)),
                 area_m2=dict(type="number", exclusiveMinimum=0, maximum=1e10), crop=obj(dict(name=string, stage=string)),
                 period=obj(dict(start=dict(type="string", format="date"), end=dict(type="string", format="date"))),
                 target=target, products=dict(type="array", maxItems=100, items=product))
    return obj(props, [key for key in props if key != "products"])


def parse_products(products):
    if not isinstance(products, list) or len(products) > 100:
        raise ValueError("products must be an array with at most 100 items")
    parsed = []
    for product in products:
        data = _mapping(product, ("id", "name", "kind", "quantity", "unit", "analysis", "density_kg_l", "density_source"), "product")
        data = dict(data, analysis=_construct(ProductAnalysis, data.get("analysis")))
        parsed.append(_construct(FertilizerProduct, data))
    return tuple(parsed)


def evaluate_nutrition_request(payload):
    payload = _mapping(payload, ("schema_version", "site", "area_m2", "crop", "period", "target", "products"), "nutrient budget")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _mapping(payload.get("site"), ("name", "timezone"), "site")
    text("site name", site.get("name"))
    text("site timezone", site.get("timezone"))
    try:
        ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    crop = _mapping(payload.get("crop"), ("name", "stage"), "crop")
    period = _mapping(payload.get("period"), ("start", "end"), "period")
    target = _construct(NutrientTarget, payload.get("target"))
    parsed = parse_products(payload.get("products", []))
    spec = NutritionBudget(payload.get("area_m2"), crop.get("name"), crop.get("stage"), period.get("start"), period.get("end"), target, tuple(parsed))
    result = evaluate_nutrition(spec)
    result.update(schema_version=1, site=site)
    result["inputs"] = dict(schema_version=1, site=site, area_m2=spec.area_m2, crop=crop, period=period,
                            target=asdict(target), products=[asdict(p) for p in spec.products])
    return result


def render_nutrition_report(result):
    def amount(value):
        return "unknown" if value is None else f"{value:g}"

    inputs = result["inputs"]
    lines = ["Agronaut mineral nutrient accounting — EXPERIMENTAL",
             "Declared product quantities compared with supplied targets; not an application recommendation.",
             f"{inputs['crop']['name']} · {inputs['crop']['stage']} · {result['area_ha']:g} ha",
             f"Period: {inputs['period']['start']} to {inputs['period']['end']}"]
    for key, row in result["nutrients"].items():
        lines.append(f"{key.upper()}: declared {amount(row['declared_kg_ha'])} kg/ha; target {amount(row['target_kg_ha'])} kg/ha; "
                     f"whole-area shortfall {amount(row['shortfall_kg'])} kg; excess {amount(row['excess_kg'])} kg.")
    for title, key in (("Missing inputs", "missing_inputs"), ("Assumptions", "assumptions"), ("Limits", "limitations"), ("Sources", "sources")):
        lines.extend(["", title+":", *(f"- {item}" for item in result[key])])
    return "\n".join(lines)
