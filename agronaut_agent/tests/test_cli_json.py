"""Machine-readable --json output for sizing commands (issue #145)."""

import contextlib
import io
import json

from agronaut_agent import cli


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def test_size_json_parses_and_keeps_honesty_fields():
    code, out = _run(["size", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "12", "--temp", "27", "--water", "3000", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["not_modeled"]
    assert payload["coefficients_used"]
    assert payload["bill_of_materials"]
    assert "source" in payload["coefficients_used"][0]


def test_size_hydro_json_parses_and_keeps_honesty_fields():
    code, out = _run(["size-hydro", "--crop", "lettuce",
                      "--area", "10", "--temp", "22", "--water", "500", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["not_modeled"]
    assert payload["coefficients_used"]
    assert payload["bill_of_materials"]


def test_optimize_json_parses_and_keeps_caveats():
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "food", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["not_modeled"]
    assert payload["best"] is not None
    assert len(payload["ranked"]) == 5


def test_optimize_json_respects_top():
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "food", "--json", "--top", "2"])
    assert code == 0
    payload = json.loads(out)
    assert len(payload["ranked"]) == 2


def test_list_json_is_parseable():
    code, out = _run(["list", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert "tilapia" in payload["fish_species"]
    assert "lettuce" in payload["crops"]


def test_size_without_json_is_unchanged_prose():
    code, out = _run(["size", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "12", "--temp", "27", "--water", "3000"])
    assert code == 0
    assert out.lstrip().startswith("FEASIBLE")
    assert not out.lstrip().startswith("{")


# A rejection under --json must still be JSON. It used to be the prose report, so a
# script calling json.loads on stdout crashed instead of learning why it was refused.

def _assert_json_rejection(code, out, needle):
    assert code == 2
    payload = json.loads(out)
    assert payload["error"] == "VALIDATION_FAILED"
    assert any(needle in e for e in payload["errors"])
    assert "do not guess" in payload["instruction"]


def test_size_json_rejection_is_json():
    code, out = _run(["size", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "-5", "--temp", "27", "--water", "3000", "--json"])
    _assert_json_rejection(code, out, "grow_area_m2")


def test_size_hydro_json_rejection_is_json():
    code, out = _run(["size-hydro", "--crop", "lettuce",
                      "--area", "10", "--temp", "99", "--water", "500", "--json"])
    _assert_json_rejection(code, out, "temperature_c")


def test_optimize_json_rejection_is_json():
    code, out = _run(["optimize", "--area", "-1", "--temp", "28", "--water", "5000",
                      "--objective", "food", "--json"])
    _assert_json_rejection(code, out, "grow_area_m2")


def test_optimize_json_unknown_objective_is_json():
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "vibes", "--json"])
    _assert_json_rejection(code, out, "vibes")


def test_rejection_without_json_is_unchanged_prose():
    code, out = _run(["size", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "-5", "--temp", "27", "--water", "3000"])
    assert code == 2
    assert out.startswith("VALIDATION_FAILED")
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "vibes"])
    assert code == 2
    assert out.startswith("Unknown objective")
