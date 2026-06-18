"""The CLI is a thin wrapper over the deterministic core: arg parsing -> the same seam
the UI uses, structured + human output, and the trust gate's rejections surfaced as a
non-zero exit. No Streamlit, no LLM, no network."""

import json

import pytest

from agronaut.cli import main

# Valid inputs reused across cases (tilapia × lettuce, generous water budget).
_DESIGN = ["design", "--species", "tilapia", "--crop", "lettuce",
           "--grow-area", "20", "--temp", "26", "--water-budget", "200"]
_OPT = ["optimize", "--grow-area", "20", "--temp", "28", "--water-budget", "200"]


def test_species_and_crops_listing(capsys):
    assert main(["species"]) == 0
    assert "tilapia" in capsys.readouterr().out
    assert main(["crops"]) == 0
    assert "lettuce" in capsys.readouterr().out


def test_species_json_is_a_parseable_array(capsys):
    assert main(["species", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and "tilapia" in data


def test_design_human_output(capsys):
    assert main(_DESIGN) == 0
    out = capsys.readouterr().out
    assert "FEASIBLE" in out
    assert "Feed" in out and "g/day" in out
    # The honesty layer must always be present.
    assert "calibration SEEDS" in out


def test_design_json_carries_the_full_artifact(capsys):
    assert main(_DESIGN + ["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["feasible"] is True
    assert out["fish_count"] > 0
    assert len(out["coefficients_used"]) > 0      # provenance survives serialization
    assert len(out["bill_of_materials"]) > 0
    assert len(out["not_modeled"]) > 0            # honesty layer survives serialization


def test_design_report_is_markdown(capsys):
    assert main(_DESIGN + ["--report", "--site", "Pilot"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Aquaponics System Design — Pilot")
    assert "## Coefficients Used (auditable)" in out
    assert "## NOT Modeled" in out


def test_validation_error_is_a_nonzero_exit(capsys):
    # Unknown species must be rejected by the trust gate, not silently defaulted.
    code = main(["design", "--species", "shark", "--crop", "lettuce",
                 "--grow-area", "20", "--temp", "26", "--water-budget", "200"])
    assert code == 2
    assert "validation failed" in capsys.readouterr().err


def test_infeasible_design_still_succeeds_but_says_so(capsys):
    # A tiny water budget is a valid computed result (infeasible), not an error.
    assert main(["design", "--species", "tilapia", "--crop", "lettuce",
                 "--grow-area", "20", "--temp", "26", "--water-budget", "5"]) == 0
    assert "NOT FEASIBLE" in capsys.readouterr().out


def test_optimize_human_output(capsys):
    assert main(_OPT + ["--objective", "food", "--top", "3"]) == 0
    out = capsys.readouterr().out
    assert "Optimize for food" in out
    assert "Best:" in out


def test_optimize_json_is_parseable(capsys):
    assert main(_OPT + ["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["objective"] == "water_efficiency"
    assert out["best"]["fish_species"]
    assert out["searched"] > 0


def test_optimize_palette_restriction(capsys):
    assert main(_OPT + ["--fish", "tilapia", "--crops", "lettuce", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["best"]["fish_species"] == "tilapia"


def test_optimize_unknown_fish_is_a_nonzero_exit(capsys):
    assert main(_OPT + ["--fish", "salmon"]) == 2
    assert "unknown fish species" in capsys.readouterr().err


def test_optimize_rejects_bad_objective_via_argparse():
    # argparse `choices` enforcement exits 2 before our code runs.
    with pytest.raises(SystemExit) as exc:
        main(_OPT + ["--objective", "money"])
    assert exc.value.code == 2


def test_no_command_prints_help_and_returns_one(capsys):
    assert main([]) == 1
    assert "usage:" in capsys.readouterr().out.lower()
