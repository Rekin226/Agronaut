"""Strict offline contracts and owned immutable lateral scenarios."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.cli import main
from agronaut_agent.domains import get_capability, run_capability
from agronaut_agent.drip_lateral_io import evaluate_drip_lateral_request
from agronaut_agent.tests.test_field_workflow import (
    setup,  # noqa: F401 — shared owned field fixtures
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT/"examples/drip_lateral.json"


def inputs(filename="drip_lateral.json"):
    return json.loads((EXAMPLE.parent/filename).read_text())


@pytest.mark.parametrize("filename", ["drip_lateral.json", "drip_lateral_curve.json"])
def test_registered_schema_cli_and_stdlib_output_agree(capsys, filename):
    example = EXAMPLE.parent/filename
    registered = run_capability("soil", "drip_lateral", inputs(filename))
    assert main(["evaluate-drip-lateral", "--input", str(example), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == registered["result"]
    assert main(["evaluate-drip-lateral", "--input", str(example)]) == 0
    assert "no irrigation schedule" in capsys.readouterr().out.lower()
    process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "evaluate-drip-lateral",
        "--input", str(example), "--json"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(process.stdout) == registered["result"]
    schema = get_capability("soil", "drip_lateral").describe()["input_schema"]
    assert set(schema["required"]) == set(inputs())
    assert set(schema["properties"]["stations"]["items"]["required"]) == set(inputs()["stations"][0])
    import jsonschema
    jsonschema.validate(inputs(filename), schema)
    wrong_version = inputs(filename)
    wrong_version["schema_version"] = 3-wrong_version["schema_version"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wrong_version, schema)


@pytest.mark.parametrize("filename", ["drip_lateral.json", "drip_lateral_curve.json"])
def test_owned_save_retry_export_and_scope_keep_scenarios_separate_from_actual_water(setup, filename):  # noqa: F811
    workflow, farms = setup
    data = inputs(filename)
    plan = workflow.save("owner", "field", "drip_lateral", data, cycle_id="cycle", request_id="lateral")
    assert workflow.save("owner", "field", "drip_lateral", data, cycle_id="cycle", request_id="lateral") == plan
    data["stations"][0]["elevation_m"] = 4
    assert workflow.get("owner", "field", plan["id"])["inputs"]["stations"][0]["elevation_m"] == .2
    assert plan in farms.export("owner")["calculations"]
    assert workflow.list("owner", "field")["water_days"] == []
    assert plan["result"]["result"]["schedule"] is None
    assert plan["result"]["result_schema"] == f"agronaut.drip_lateral.v{data['schema_version']}"
    with pytest.raises(ValueError):
        workflow.get("other", "field", plan["id"])
    with pytest.raises(ValueError):
        workflow.get("owner", "field2", plan["id"])
    with pytest.raises(ValueError, match="cycle"):
        workflow.save("owner", "field", "drip_lateral", inputs(filename), cycle_id="cycle2", request_id="wrong-cycle")
    data = inputs(filename)
    data["site"]["timezone"] = "Asia/Taipei"
    with pytest.raises(ValueError, match="timezone"):
        workflow.save("owner", "field", "drip_lateral", data, request_id="wrong-timezone")
    with pytest.raises(ValueError, match="unsupported"):
        run_capability("aquaponics", "drip_lateral", inputs(filename))


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(schedule=[1]),
    lambda d: d["site"].update(timezone="unknown"),
    lambda d: d["inlet"].pop("source"),
    lambda d: d["emitter"].update(pressure_compensating=True),
    lambda d: d["stations"][0].update(extra="ignored"),
    lambda d: d["stations"][0].pop("elevation_m"),
    lambda d: d["stations"][0].update(distance_m=True),
])
def test_missing_unknown_or_coerced_inputs_cannot_silently_enter_the_model(change):
    data = inputs()
    change(data)
    with pytest.raises((ValueError, TypeError)):
        evaluate_drip_lateral_request(data)
