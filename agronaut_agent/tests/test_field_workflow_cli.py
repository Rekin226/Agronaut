import json
import subprocess
import sys
from pathlib import Path

from agronaut_agent.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_stdlib_cli_setup_save_log_and_replay(tmp_path):
    db = tmp_path/"workflow.sqlite3"

    def command(*args):
        process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=ROOT,
                                  capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout)

    command("farm", "--db", str(db), "create", "--input", str(ROOT/"examples/soil_unit.json"))
    payload = json.loads((ROOT/"examples/field_water.json").read_text())
    payload["site"]["timezone"] = "Africa/Accra"
    payload["weather"][0].update(kind="forecast", issued_at="2026-07-05T12:00:00Z")
    request = tmp_path/"request.json"

    def operation(name, data):
        request.write_text(json.dumps(data))
        return command("field", "--db", str(db), name, "--input", str(request))

    before = operation("save", dict(task="root_zone_budget", inputs=payload, request_id="initial"))
    review = operation("review_weather", dict(calculation_id=before["id"], max_age_hours=24,
                                             as_of="2026-07-06T12:00:00Z"))
    assert review["rows"][0]["age_hours"] == 24
    assert review["rows"][0]["within_age_limit"] is True
    assert operation("get", dict(calculation_id=before["id"])) == before
    observed = operation("revise_weather", dict(calculation_id=before["id"], request_id="observed",
                         weather=[dict(payload["weather"][0], kind="observed", source="Synthetic observation")]))
    assert observed["parent_id"] == before["id"]
    logged = operation("record_day", dict(request_id="daily", data=dict(
        day="2026-07-06", rainfall_mm=10, net_irrigation_mm=0, runoff_mm=0,
        source="Synthetic CLI fixture", provenance="reported", quality="unverified")))
    after = operation("replay", dict(request_id="replay", calculation_id=observed["id"],
                                      water_record_ids=[logged["id"]]))
    assert after["parent_id"] == observed["id"]
    assert after["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 10
    assert len(command("field", "--db", str(db), "list")["calculations"]) == 3


def test_cli_requires_owned_unit_and_never_accepts_identity_in_data(tmp_path, capsys):
    db = str(tmp_path/"missing.sqlite3")
    assert main(["field", "--db", db, "list"]) == 2
    assert "select" in json.loads(capsys.readouterr().out)["error"]
    path = tmp_path/"malicious.json"
    path.write_text('{"owner_id":"other"}')
    assert main(["farm", "--db", db, "create", "--input", str(ROOT/"examples/soil_unit.json")]) == 0
    capsys.readouterr()
    assert main(["field", "--db", db, "list", "--input", str(path)]) == 2
    assert "unknown fields" in json.loads(capsys.readouterr().out)["error"]
