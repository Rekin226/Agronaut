"""Strict nutrient inputs and stdlib-only CLI use the same registered calculator."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.nutrition_io import evaluate_nutrition_request

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    return json.loads((ROOT/"examples/nutrient_budget.json").read_text())


def test_cli_and_discovered_capability_share_output_without_optional_dependencies(tmp_path):
    result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "nutrient-budget", "--input",
                             str(ROOT/"examples/nutrient_budget.json"), "--json"], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    data = json.loads(result.stdout)
    assert data == run_capability("soil", "nutrient_budget", inputs())["result"]
    assert data["nutrients"]["n"]["excess_kg"] == 5
    schema = capabilities("soil")["soil"]["nutrient_budget"]["input_schema"]
    assert schema["properties"]["target"]["properties"]["basis"]["const"] == "remaining_application"
    assert schema["properties"]["products"]["items"]["properties"]["analysis"]["properties"]["basis"]["const"] == "mass_percent"
    with pytest.raises(ValueError, match="unsupported capability"):
        run_capability("hydroponics", "nutrient_budget", inputs())
    bad = tmp_path/"bad.json"
    bad.write_text('{"schema_version":1,"schema_version":1}')
    result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "nutrient-budget", "--input", str(bad), "--json"],
                             cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 2 and "duplicate JSON" in result.stdout


@pytest.mark.parametrize("path,value", [("schema_version", True), ("area_m2", None), ("site.timezone", "not/a/zone"),
    ("crop.stage", ""), ("target.n_kg_ha", True), ("target.basis", "soil_ppm"), ("target.scope_note", ""),
    ("products", {}), ("period.start", "20260701")])
def test_invalid_request_boundaries_fail(path, value):
    data = inputs()
    keys = path.split(".")
    obj = data
    for key in keys[:-1]:
        obj = obj[key]
    obj[keys[-1]] = value
    with pytest.raises(ValueError):
        evaluate_nutrition_request(data)


def test_unknown_keys_missing_analyses_and_unsupported_units_do_not_silently_coerce():
    original = inputs()
    for mutate in (lambda d: d.update(soil_ppm=10), lambda d: d["target"].update(p_kg_ha=10),
        lambda d: d["products"][0]["analysis"].update(p_pct=10), lambda d: d["products"][0].update(unit="lb"),
        lambda d: d["products"][0]["analysis"].pop("n_pct")):
        data = deepcopy(original)
        mutate(data)
        with pytest.raises(ValueError):
            evaluate_nutrition_request(data)
    no_products = deepcopy(original)
    no_products.pop("products")
    assert run_capability("soil", "nutrient_budget", no_products)["result"]["nutrients"]["n"]["declared_total_kg"] == 0
