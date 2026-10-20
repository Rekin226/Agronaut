"""Actual mineral application validation and summaries, independent of target budgets."""

from dataclasses import asdict
from decimal import Decimal

from agro_model.nutrition import NUTRIENTS, account_products
from agro_model.validation import choice, text

from .field_io import _date, _mapping
from .nutrition_io import parse_products


def application_data(data):
    data = dict(_mapping(data, ("entry", "day", "phase", "area_m2", "location", "placement",
                               "source", "provenance", "products", "calculation_id"), "fertilizer application"))
    for key in ("entry", "location", "placement", "source"):
        data[key] = text(key, data.get(key)).strip()
    _date(data.get("day"))
    choice("phase", data.get("phase"), ("preplant", "in_crop"))
    choice("provenance", data.get("provenance"), ("measured", "reported", "estimated"))
    if data.get("calculation_id") is not None:
        text("calculation_id", data["calculation_id"])
    products = parse_products(data.get("products"))
    if not products:
        raise ValueError("an application must contain at least one mineral product")
    data["products"] = [asdict(p) for p in products]
    return data, account_products(data.get("area_m2"), products)


def application_totals(records):
    """Only current nonvoid entries; overlapping treated areas are never summed."""
    groups = {}
    for record in records:
        if record["kind"] != "fertilizer" or record["voided"] or record["superseded"]:
            continue
        data = record["data"]
        key = data["phase"], data["provenance"]
        group = groups.setdefault(key, dict(phase=key[0], provenance=key[1], record_ids=[],
            nutrients={n: dict(known_product_kg=Decimal(0), unknown_record_ids=[]) for n in NUTRIENTS}))
        group["record_ids"].append(record["id"])
        for nutrient in NUTRIENTS:
            amount = record["accounting"]["nutrients"][nutrient]
            total = group["nutrients"][nutrient]
            total["known_product_kg"] += Decimal(str(amount["known_product_kg"]))
            if amount["unknown_product_ids"]:
                total["unknown_record_ids"].append(record["id"])
    result = []
    for _, group in sorted(groups.items()):
        for total in group["nutrients"].values():
            total["known_product_kg"] = format(total["known_product_kg"], "f")
            total["declared_total_kg"] = None if total["unknown_record_ids"] else total["known_product_kg"]
        result.append(dict(group, applications=len(group["record_ids"])))
    return dict(groups=result, note="Only recorded current applications, separated by phase and provenance. "
                "No records means unknown application history, not zero fertilizer use. Preplant entries may predate planting. "
                "Unknown analyses or liquid densities leave complete nutrient totals unknown. "
                "Label nutrients are not plant-available nutrients or measured uptake. "
                "Treated areas may overlap; areas and kg/ha are not added across applications. "
                "Applications do not automatically reduce future nutrient targets.")
