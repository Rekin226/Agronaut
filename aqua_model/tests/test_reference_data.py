"""The shipped reference tables must be findable, and must be declared for packaging.

The bug this guards against is invisible in a checkout, which is what makes it worth a
test. Every one of these files resolved through `aqua_model/../data/`, correct beside the
source and, under `pip install`, pointing at a `site-packages/data/` that nothing creates.
The checkout passed every test while the published package had no price book and reported
its own predictive skill as UNMEASURED.

So these assert the two halves that can drift apart: that the resolver finds the files, and
that everything it promises is actually listed for the wheel to carry.
"""

import re
import tomllib
from pathlib import Path

import pytest

from aqua_model import reference_data, validation_status

_ROOT = Path(__file__).resolve().parent.parent.parent


def test_every_shipped_table_resolves():
    d = reference_data.reference_dir()
    missing = [n for n in reference_data.SHIPPED if not (d / n).is_file()]
    assert not missing, f"declared shipped but not found in {d}: {missing}"


@pytest.mark.skipif(not (_ROOT / "pyproject.toml").is_file(),
                    reason="packaging config only exists in a checkout")
def test_packaging_ships_exactly_what_the_resolver_promises():
    """SHIPPED and pyproject's data-files must agree.

    They are two lists in two languages describing one fact. Adding a table to one and not
    the other fails in the direction nobody tests: the wheel builds, the tests pass, and the
    file is missing only for people who installed it."""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    declared = cfg["tool"]["setuptools"]["data-files"]["share/agronaut/data"]
    packaged = {Path(p).name for p in declared}
    assert packaged == set(reference_data.SHIPPED), (
        "pyproject data-files and reference_data.SHIPPED disagree: "
        f"only in pyproject={packaged - set(reference_data.SHIPPED)}, "
        f"only in SHIPPED={set(reference_data.SHIPPED) - packaged}")


@pytest.mark.skipif(not (_ROOT / "pyproject.toml").is_file(),
                    reason="packaging config only exists in a checkout")
def test_no_runtime_state_is_packaged():
    """data/ in a checkout also holds the memory DB, the analytics log and the page cache.

    A glob here would ship a developer's own conversations to every user, so the list is
    explicit — and this is what keeps it explicit."""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    declared = cfg["tool"]["setuptools"]["data-files"]["share/agronaut/data"]
    assert not [p for p in declared if "*" in p], "no globs under data/: it holds live state"
    forbidden = re.compile(r"\.(sqlite3|jsonl|db)$|analytics|agronaut\.sqlite")
    assert not [p for p in declared if forbidden.search(p)], "runtime state is being packaged"


def test_the_validation_record_is_reachable_wherever_it_is_installed():
    """The honesty claim only holds if the evidence travels with the code.

    Missing, the twin correctly says PREDICTIVE SKILL UNMEASURED — a stronger caveat, so
    nobody is misled. But the record exists and the README cites it, so a package that
    cannot find it is understating what was actually measured."""
    lines = validation_status.validation_lines()
    assert not lines[0].startswith("PREDICTIVE SKILL UNMEASURED"), (
        f"validation record not found at {validation_status.ARTIFACT}")
    assert "Evidence: data/twin_validation.json" in lines[-1]


def test_the_evidence_pointer_is_not_an_install_path():
    """It names the file in the repo, not wherever the wheel happened to put it: a
    site-packages path is no use to a reader trying to check the claim."""
    pointer = validation_status.validation_lines()[-1]
    assert "site-packages" not in pointer and "sys.prefix" not in pointer


def test_an_override_wins(tmp_path, monkeypatch):
    """A container or a distro packager must be able to place these without patching code."""
    monkeypatch.setenv("AGRONAUT_REFERENCE_DIR", str(tmp_path))
    assert reference_data.reference_dir() == tmp_path
    assert reference_data.reference_path("price_book.json") == tmp_path / "price_book.json"
