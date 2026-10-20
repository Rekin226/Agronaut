"""Surface evaluation stays distinct from cutoff design and completed-water logging."""

import json
import subprocess
import sys
from pathlib import Path

from agronaut_agent.cli import main
from agronaut_agent.domains import capabilities, run_capability

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT/"examples/surface_event.json"


def test_surface_cli_report_and_registered_json_match(capsys):
    assert main(["evaluate-surface", "--input", str(EXAMPLE), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    registered = run_capability("soil", "surface_evaluation", json.loads(EXAMPLE.read_text()))
    assert out == registered["result"]
    assert registered["feasible"] is None
    assert main(["evaluate-surface", "--input", str(EXAMPLE)]) == 0
    report = capsys.readouterr().out
    assert "54" in report and "EXPERIMENTAL" in report and "No cutoff-time recommendation" in report


def test_surface_schema_describes_nested_arrays_and_explicit_unknown_deficit():
    schema = capabilities("soil")["soil"]["surface_evaluation"]["input_schema"]
    data = json.loads(EXAMPLE.read_text())
    assert set(schema["required"]) == set(data)
    for name, value in data.items():
        if isinstance(value, dict):
            assert set(schema["properties"][name]["required"]) == set(value)
        elif isinstance(value, list):
            assert set(schema["properties"][name]["items"]["required"]) == set(value[0])
    assert schema["properties"]["assessment"]["properties"]["root_zone_deficit_mm"]["type"] == ["number", "null"]
    schema["properties"]["stations"]["items"]["required"].clear()
    assert capabilities("soil")["soil"]["surface_evaluation"]["input_schema"]["properties"]["stations"]["items"]["required"]
    assert capabilities("soil")["soil"]["surface_cutoff"]["executable"] is False


def test_invalid_request_and_accounting_mismatch_are_distinct_cli_results(tmp_path, capsys):
    data = json.loads(EXAMPLE.read_text())
    data["inflow"][0]["mean_lps"] = 20
    path = tmp_path/"event.json"
    path.write_text(json.dumps(data))
    assert main(["evaluate-surface", "--input", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["performance"] is None
    data["inflow"] = []
    path.write_text(json.dumps(data))
    assert main(["evaluate-surface", "--input", str(path), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "VALIDATION_FAILED"


def test_surface_cli_runs_with_only_the_standard_library():
    process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "evaluate-surface",
                              "--input", str(EXAMPLE), "--json"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["water_balance_accepted"]
