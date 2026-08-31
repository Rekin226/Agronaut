"""The agentskills.io skill CLI: a standalone, deterministic wrapper over aqua_model that any
agent (Hermes, OpenClaw, Claude Code) can call. No LLM, no network — validated input in,
cited sizing out; the trust gate is preserved (bad input exits non-zero)."""

import contextlib
import io

from skills.aquaponics_engineer import cli


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def test_size_aquaponics_prints_cited_feasible_design():
    code, out = _run(["size-aquaponics", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "12", "--temp", "27", "--water", "3000"])
    assert code == 0
    assert "FEASIBLE" in out
    assert "source:" in out and "NOT modeled" in out


def test_size_hydroponics_prints_nutrient_target():
    code, out = _run(["size-hydroponics", "--crop", "lettuce",
                      "--area", "10", "--temp", "22", "--water", "500"])
    assert code == 0
    assert "hydroponic" in out.lower() and "EC" in out


def test_optimize_prints_best_ratio():
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "food"])
    assert code == 0
    assert "Best ratio" in out


def test_list_shows_species_and_crops():
    code, out = _run(["list"])
    assert code == 0
    assert "tilapia" in out and "lettuce" in out


def test_trust_gate_rejects_bad_input_nonzero_exit():
    code, out = _run(["size-aquaponics", "--fish", "shark", "--crop", "lettuce",
                      "--area", "12", "--temp", "27", "--water", "3000"])
    assert code != 0
    assert "VALIDATION_FAILED" in out


def test_skill_manifest_has_agentskills_frontmatter():
    import pathlib
    md = pathlib.Path(__file__).resolve().parents[1] / "SKILL.md"
    text = md.read_text()
    assert text.startswith("---")
    assert "name:" in text and "description:" in text
