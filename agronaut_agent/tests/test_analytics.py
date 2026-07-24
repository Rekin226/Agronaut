"""Privacy-preserving usage analytics: counts and funnels, never message content. User ids
are stored only as a truncated hash (distinct-user counts without knowing who), and the API
structurally cannot record free text. Disabled with AGRONAUT_ANALYTICS=off.
"""

import json

from agronaut_agent.analytics import Analytics


def test_records_events_without_raw_user_id_or_content(tmp_path):
    a = Analytics(path=tmp_path / "a.jsonl")
    a.record("message", user_id="telegram:12345")
    a.record("tool_call", user_id="telegram:12345", tool="size_aquaponics_system")

    rows = [json.loads(ln) for ln in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    for r in rows:
        # the raw id never appears; only a short hash
        assert "12345" not in json.dumps(r)
        assert "telegram:12345" not in json.dumps(r)
        assert len(r["uid"]) <= 16 and r["uid"] != "telegram:12345"
    assert rows[1]["tool"] == "size_aquaponics_system"   # event metadata is fine, content isn't


def test_same_user_hashes_stably_for_distinct_counts(tmp_path):
    a = Analytics(path=tmp_path / "a.jsonl")
    a.record("message", user_id="u1")
    a.record("message", user_id="u1")
    a.record("message", user_id="u2")
    rows = [json.loads(ln) for ln in (tmp_path / "a.jsonl").read_text().splitlines()]
    uids = {r["uid"] for r in rows}
    assert len(uids) == 2                                 # u1 collapses, u2 distinct


def test_summarize_reports_counts_and_distinct_users(tmp_path):
    a = Analytics(path=tmp_path / "a.jsonl")
    a.record("message", user_id="u1")
    a.record("tool_call", user_id="u1", tool="size_aquaponics_system")
    a.record("message", user_id="u2")
    a.record("image", user_id="u2")

    s = a.summarize()
    assert s["events"]["message"] == 2
    assert s["events"]["tool_call"] == 1
    assert s["events"]["image"] == 1
    assert s["distinct_users"] == 2
    # funnel: users who reached a sizing tool
    assert s["users_who_sized"] == 1


def test_disabled_by_env_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGRONAUT_ANALYTICS", "off")
    a = Analytics(path=tmp_path / "a.jsonl")
    a.record("message", user_id="u1")
    assert not (tmp_path / "a.jsonl").exists()
    assert a.summarize()["distinct_users"] == 0


def test_record_ignores_unknown_freetext_kwargs_defensively(tmp_path):
    # content must never leak in even if a caller passes it — only allowlisted fields persist
    a = Analytics(path=tmp_path / "a.jsonl")
    a.record("message", user_id="u1", text="my secret farm location", note="pii here")
    blob = (tmp_path / "a.jsonl").read_text()
    assert "secret farm" not in blob and "pii here" not in blob
