"""Offline CLI and discoverable dispatch contract for method-specific designs."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from agronaut_agent.cli import main
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.irrigation_io import (
    evaluate_drip_request,
    evaluate_sprinkler_request,
    render_drip_report,
    render_sprinkler_report,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("method", ["drip", "sprinkler"])
def test_offline_cli_json_and_human_report(capsys, method):
    example = ROOT / f"examples/{method}_design.json"
    args = ["design-irrigation", "--method", method, "--input", str(example)]
    assert main(args+["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["feasible"]
    assert out["evidence_status"] == "experimental"
    assert main(args) == 0
    report = capsys.readouterr().out
    assert "EXPERIMENTAL" in report and "Zone 2" in report and "Sources:" in report


@pytest.mark.parametrize("method", ["drip", "sprinkler"])
def test_adapter_preserves_infeasibility_and_provenance(capsys, tmp_path, method):
    data = json.loads((ROOT / f"examples/{method}_design.json").read_text())
    data["supply"]["max_flow_lph"] = 100
    result = run_capability("soil", f"{method}_design", data)
    assert result["feasible"] is False
    assert "source_flow_lph" in result["binding_constraints"]
    assert result["result"]["schedule"] is None
    assert result["inputs"] == data
    assert data["supply"]["source"] in result["sources"]
    path = tmp_path/"design.json"
    path.write_text(json.dumps(data))
    assert main(["calculate", "--system", "soil", "--task", f"{method}_design", "--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == result
    data["layout"]["terrain"] = "hilly"
    path.write_text(json.dumps(data))
    assert main(["design-irrigation", "--method", method, "--input", str(path), "--json"]) == 2
    assert "terrain" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("method", ["drip", "sprinkler"])
def test_catalog_exposes_complete_input_fields_without_shared_mutable_schema(method):
    data = json.loads((ROOT / f"examples/{method}_design.json").read_text())
    schema = capabilities("soil")["soil"][f"{method}_design"]["input_schema"]
    assert set(schema["required"]) == set(data)
    for name, record in data.items():
        if isinstance(record, dict):
            nested = schema["properties"][name]
            assert set(nested["required"]) == set(record)
            assert nested["additionalProperties"] is False
    schema["properties"]["layout"]["required"].clear()
    assert capabilities("soil")["soil"][f"{method}_design"]["input_schema"]["properties"]["layout"]["required"]


@pytest.mark.parametrize("method", ["drip", "sprinkler"])
def test_irrigation_cli_needs_only_the_standard_library(method):
    example = ROOT / f"examples/{method}_design.json"
    result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli",
                             "design-irrigation", "--method", method, "--input", str(example), "--json"],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["feasible"]


@pytest.mark.parametrize("method,evaluate,render", [
    ("drip", evaluate_drip_request, render_drip_report),
    ("sprinkler", evaluate_sprinkler_request, render_sprinkler_report),
])
def test_failed_reports_explain_minimum_head_and_maximum_water_before_runtimes(method, evaluate, render):
    data = json.loads((ROOT / f"examples/{method}_design.json").read_text())
    data["supply"].update(min_running_head_m=1, max_running_head_m=2, daily_available_l=0)
    result = evaluate(data)
    original = deepcopy(result)
    report = render(result)
    assert result == original
    assert not result["feasible"] and result["schedule"] is None
    assert report.index("Binding constraints:") < report.index("candidate runtime")
    water = next(c for c in result["constraints"] if c["code"] == "daily_source_water_l")
    assert f"Daily source water: evaluated {water['actual']} L; allowed maximum 0 L" in report
    name = "emitter" if method == "drip" else "sprinkler"
    minimum = next(c for c in result["constraints"] if c["code"] == f"{name}_min_head_m")
    assert f"Zone {minimum['zone']} — {name.title()} minimum operating head" in report
    assert f"required minimum {minimum['required_min']} m of water head" in report
    assert "Review the daily water allowance" in report
    assert "{'code':" not in report
    for source in result["sources"]:
        assert source in report


def test_report_does_not_round_away_a_small_failure_or_hide_a_future_constraint():
    data = json.loads((ROOT / "examples/drip_design.json").read_text())
    baseline = evaluate_drip_request(data)
    data["supply"]["daily_available_l"] = baseline["candidate_daily_upper_water_l"] - 1e-8
    result = evaluate_drip_request(data)
    assert result["feasible"] is False
    water = next(c for c in result["constraints"] if c["code"] == "daily_source_water_l")
    report = render_drip_report(result)
    assert str(water["actual"]) in report and str(water["allowed_max"]) in report
    assert str(water["actual"]) != str(water["allowed_max"])
    result["constraints"].append(dict(code="future_check", zone=2, actual=5, required_min=6, allowed_max=None))
    report = render_drip_report(result)
    assert "Unrecognized constraint:" in report and "'code': 'future_check'" in report
    assert "'required_min': 6" in report
