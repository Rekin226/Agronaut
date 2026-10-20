"""The documented rehearsal must cross real CLI and database boundaries offline."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rehearse_field_cycle import main

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("timezone", ["Africa/Ouagadougou", "Asia/Taipei"])
def test_full_rehearsal_is_offline_isolated_and_auditable(tmp_path, timezone):
    default_db = tmp_path / "do-not-touch.sqlite3"
    default_db.write_bytes(b"an existing database must never be opened")
    output = tmp_path / "synthetic run with spaces"
    env = dict(os.environ, PYTHONPATH=str(ROOT), AGRONAUT_DB=str(default_db),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result = subprocess.run([sys.executable, "-S", "-m", "scripts.rehearse_field_cycle",
        "--output-dir", str(output), "--timezone", timezone], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "rehearsal.json").read_text())
    assert report["status"] == "passed"
    assert report['schema_version'] == 3
    assert report['runtime'] == dict(mode='checkout')
    assert report["timezone"] == timezone
    expected_language = 'fr' if timezone == 'Africa/Ouagadougou' else 'zh-Hant'
    assert report['task_language'] == expected_language
    assert report["field_validation_performed"] is False
    assert all(report["summary"]["checks"].values())
    assert report["summary"]["equipment_feasible"] is False
    assert report["summary"]["coverage"]["recorded_days"] == 1
    assert report["summary"]["coverage"]["missing_days"] == 100
    assert report["summary"]["harvest_totals"][0]["quantity"] == "12.5"
    assert default_db.read_bytes() == b"an existing database must never be opened"
    steps = {step["label"]: step for step in report["steps"]}
    assert steps["scenario-replay-blocked"]["returncode"] == 2
    assert all(step["returncode"] == 0 for name, step in steps.items() if name != "scenario-replay-blocked")
    for step in report["steps"]:
        assert step["argv"][1:4] == ["-S", "-m", "agronaut_agent.cli"]
        assert step["argv"][step["argv"].index("--db") + 1] == str(output / "synthetic.sqlite3")
        assert json.loads((output / step["output"]).read_text())
    summary = report["summary"]
    assert summary["base_depletion_mm"] == pytest.approx(64.15534115524338)
    assert summary["replay_depletion_mm"] == pytest.approx(57.461524187735)
    assert summary["corrected_depletion_mm"] == pytest.approx(56.461524187735)
    journal = json.loads((output / steps["season-review"]["output"]).read_text())["journal"]
    assert len(journal["harvest_reviews"]["current"]) == 0  # No invented area/coverage or yield.
    current = journal['calendar']['tasks'][0]
    assert current['status'] == 'completed'
    assert current['completion_evidence_state'] == 'current'
    assert current['trigger_review'] == dict(state='review_changed', needs_review=True)
    assert current['needs_review']  # Adopting actual water must not accept a different source review.
    def payload(label):
        return json.loads((output / steps[label]['output']).read_text())
    plan = payload('plan-irrigation-record-save')['record']
    first = payload('complete-task-save')['record']
    latest = payload('refresh-task-evidence-save')['record']
    original_water, corrected_water = payload('water-record'), payload('correct-water')
    assert plan['data']['trigger']['id'] == payload('point-reading')['id']
    assert latest['data']['trigger_snapshot'] == plan['data']['trigger_snapshot']
    assert latest['data']['trigger_snapshot']['effective_quality'] == 'unverified'
    assert payload('review-point-reading')['quality'] == 'checked'
    assert first['data']['resolution']['completion_record']['snapshot'] == original_water
    assert latest['data']['resolution']['completion_record']['snapshot'] == corrected_water
    assert latest['id'] == current['record_id']
    assert {r['id'] for r in journal['records'] if r['kind'] == 'calendar'} == {plan['id'], first['id'], latest['id']}
    for label in ('plan-irrigation-record', 'complete-task', 'refresh-task-evidence'):
        assert payload(label+'-save') == payload(label+'-retry')
        assert report['guided_tasks'][label]['language'] == expected_language
    for label in ('plan-irrigation-record-trigger', 'complete-task-record_id', 'complete-task-evidence',
                  'refresh-task-evidence-record_id', 'refresh-task-evidence-evidence'):
        args = steps[label]['argv']
        submitted = json.loads(Path(args[args.index('--input')+1]).read_text())
        assert submitted['answer'].isdigit()  # Exercise the displayed menu, not an ID shortcut.
        assert payload(label+'-resume')['status'] == 'collecting'
    assert all(not {'plan_task', 'resolve_task'}.intersection(step['argv']) for step in report['steps'])


def test_existing_directory_is_not_modified(tmp_path, capsys):
    marker = tmp_path / "synthetic.sqlite3"
    marker.write_bytes(b"keep this real data")
    assert main(["--output-dir", str(tmp_path)]) == 2
    assert marker.read_bytes() == b"keep this real data"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["synthetic.sqlite3"]
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_failed_cli_step_leaves_a_failure_report(tmp_path, monkeypatch, capsys):
    from scripts import rehearse_field_cycle

    def fail(*args, **kwargs):
        return subprocess.CompletedProcess(args, 2, '{"status":"VALIDATION_FAILED","error":"fixture failure"}', "")

    monkeypatch.setattr(rehearse_field_cycle.subprocess, "run", fail)
    output = tmp_path / "failed-run"
    assert main(["--output-dir", str(output)]) == 2
    report = json.loads((output / "rehearsal.json").read_text())
    assert report["status"] == "failed"
    assert "fixture failure" in report["error"]
    assert report["steps"][0]["returncode"] == 2
    assert (output / report["steps"][0]["output"]).is_file()
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_invalid_installed_runtime_leaves_report_without_starting_farm(tmp_path, capsys):
    output = tmp_path/'invalid-installation'
    assert main(['--output-dir', str(output), '--installed-python', str(tmp_path/'missing-python')]) == 2
    report = json.loads((output/'rehearsal.json').read_text())
    assert report['runtime'] == dict(mode='installed')
    assert report['status'] == 'failed'
    assert report['steps'] == []
    assert not (output/'synthetic.sqlite3').exists()
    assert json.loads(capsys.readouterr().out)['status'] == 'failed'
