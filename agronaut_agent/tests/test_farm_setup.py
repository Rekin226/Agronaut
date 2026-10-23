import copy
import json
from pathlib import Path

import pytest

from agronaut_agent.cli import main
from agronaut_agent.farm_setup import create_unit
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import _Db


def spec():
    return json.loads((Path(__file__).resolve().parents[2] / "examples/soil_unit.json").read_text())


def test_create_unit_in_new_or_existing_farm_is_owned_and_validated(tmp_path):
    db = _Db(tmp_path / "farms.sqlite3")
    first = create_unit(db, "cli:one", spec())
    request = spec()
    request["farm"] = {"id": first["farm_id"]}
    request["unit"]["name"] = "Onions"
    second = create_unit(db, "cli:one", request)
    assert first["id"] != second["id"]
    assert len(FarmStore(db).export("cli:one")["farms"]) == 1
    with pytest.raises(ValueError, match="unknown farm"):
        create_unit(db, "cli:two", request)
    assert FarmStore(db).list_units("cli:two") == []


def test_invalid_unit_does_not_leave_a_partial_farm(tmp_path):
    db = _Db(tmp_path / "farms.sqlite3")
    request = spec()
    request["unit"]["medium"] = "water"
    with pytest.raises(ValueError):
        create_unit(db, "cli:one", request)
    assert FarmStore(db).export("cli:one")["farms"] == []


def test_caller_cannot_set_owner_or_unit_ids(tmp_path):
    db = _Db(tmp_path / "farms.sqlite3")
    for key, value in (("owner_id", "victim"), ("farm_id", "victim"), ("id", "victim")):
        request = spec()
        request["unit"][key] = value
        with pytest.raises(ValueError):
            create_unit(db, "cli:one", request)
    assert FarmStore(db).list_units("cli:one") == []


def test_offline_cli_creates_selects_and_lists_units(tmp_path, capsys):
    path = tmp_path / "request.json"
    request = spec()
    path.write_text(json.dumps(request))
    prefix = ["farm", "--db", str(tmp_path / "cli.sqlite3")]
    assert main(prefix + ["create", "--input", str(path)]) == 0
    first = json.loads(capsys.readouterr().out)["created"]
    request = copy.deepcopy(request)
    request["farm"] = {"id": first["farm_id"]}
    request["unit"]["name"] = "Second field"
    path.write_text(json.dumps(request))
    assert main(prefix + ["create", "--input", str(path)]) == 0
    second = json.loads(capsys.readouterr().out)["created"]
    assert main(prefix + ["list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["units"]) == 2 and listed["active"]["unit_id"] == second["id"]
    assert main(prefix + ["select", "--unit", first["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["active"]["unit_id"] == first["id"]
    assert main(prefix + ["--user", "other", "select", "--unit", first["id"]]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "VALIDATION_FAILED"
