"""Advance scenarios are scoped plans, never completed irrigation records."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.cli import main
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.surface_advance_io import evaluate_surface_advance_request
from agronaut_agent.tests.test_field_workflow import (
    setup,  # noqa: F401 — shared owned fixtures
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT/"examples/surface_advance.json"


def inputs():
    return json.loads(EXAMPLE.read_text())


def test_cli_report_and_registered_json_match_without_external_dependencies(capsys):
    assert main(["predict-surface-advance", "--input", str(EXAMPLE), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    registered = run_capability("soil", "surface_advance", inputs())
    assert result == registered["result"]
    assert result["event_kind"] == "scenario" and result["cutoff_recommendation"] is None
    assert main(["predict-surface-advance", "--input", str(EXAMPLE)]) == 0
    assert "65.012 min" in capsys.readouterr().out
    process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "predict-surface-advance",
                              "--input", str(EXAMPLE), "--json"], cwd=ROOT, text=True,
                             capture_output=True, check=True)
    assert json.loads(process.stdout) == result


def test_save_retry_and_export_preserve_a_scenario_without_water_logs(setup):  # noqa: F811
    workflow, store = setup
    payload = inputs()
    saved = workflow.save("owner", "field", "surface_advance", payload, cycle_id="cycle", request_id="advance")
    assert saved == workflow.save("owner", "field", "surface_advance", payload, cycle_id="cycle", request_id="advance")
    payload["geometry"]["length_m"] = 10
    assert workflow.get("owner", "field", saved["id"])["inputs"]["geometry"]["length_m"] == 200
    assert workflow.list("owner", "field")["water_days"] == []
    assert saved["result"]["result"]["event_kind"] == "scenario"
    exported = store.export("owner")
    assert any(row["id"] == saved["id"] for row in exported["calculations"])
    with pytest.raises(ValueError):
        workflow.get("owner", "field2", saved["id"])
    with pytest.raises(ValueError):
        workflow.get("other", "field", saved["id"])


def test_timezone_cycle_and_domain_cannot_be_crossed(setup):  # noqa: F811
    workflow, _ = setup
    payload = inputs()
    payload["site"]["timezone"] = "Asia/Taipei"
    with pytest.raises(ValueError, match="timezone"):
        workflow.save("owner", "field", "surface_advance", payload, request_id="wrong-tz")
    with pytest.raises(ValueError, match="cycle"):
        workflow.save("owner", "field", "surface_advance", inputs(), cycle_id="cycle2", request_id="wrong-cycle")
    with pytest.raises(ValueError, match="unsupported"):
        run_capability("aquaponics", "surface_advance", inputs())


def test_schema_is_complete_and_cutoff_remains_unavailable():
    catalog = capabilities("soil")["soil"]
    schema = catalog["surface_advance"]["input_schema"]
    assert set(schema["required"]) == set(inputs())
    for name, value in inputs().items():
        if isinstance(value, dict):
            assert set(schema["properties"][name]["required"]) == set(value)
    assert not catalog["surface_cutoff"]["executable"]


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema_version=True),
    lambda p: p.update(event_kind="observed"),
    lambda p: p["site"].update(timezone="unknown"),
    lambda p: p["hydraulics"].pop("source"),
    lambda p: p["hydraulics"].update(invented_efficiency=.9),
    lambda p: p["conditions"].update(no_rain_or_runon=None),
])
def test_missing_evidence_or_extra_fields_fail_at_boundary(change):
    data = inputs()
    change(data)
    with pytest.raises((ValueError, TypeError)):
        evaluate_surface_advance_request(data)
