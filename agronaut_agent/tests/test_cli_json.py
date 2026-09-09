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
