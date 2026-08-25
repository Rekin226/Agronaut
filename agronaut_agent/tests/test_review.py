"""The community-insight review CLI — the local, off-chat approval gate."""

from agronaut_agent.store import _Db, CommunityStore
from agronaut_agent.review import apply_command, format_candidate


def _store_with_one():
    cs = CommunityStore(_Db(":memory:"))
    cs.nominate("telegram:1", "private ctx", "aerate at dawn to avoid DO crashes", "do")
    return cs


def test_apply_command_approves_pending():
    cs = _store_with_one()
    cid = cs.pending()[0]["id"]
    msg = apply_command(cs, f"approve {cid}")
    assert "approv" in msg.lower()
    assert cs.pending() == [] and len(cs.search_approved("aerate")) == 1


def test_apply_command_rejects_pending():
    cs = _store_with_one()
    cid = cs.pending()[0]["id"]
    msg = apply_command(cs, f"reject {cid}")
    assert msg == f"Rejected #{cid}."          # grammatical, not "Rejectd"
    assert cs.pending() == [] and cs.search_approved("aerate") == []


def test_apply_command_unknown_id_and_quit_and_help():
    cs = _store_with_one()
    assert "no pending candidate" in apply_command(cs, "approve 999").lower()
    assert apply_command(cs, "quit") == "__quit__"
    assert "commands" in apply_command(cs, "wat").lower()


def test_format_candidate_shows_insight_and_review_context():
    cs = _store_with_one()
    text = format_candidate(cs.pending()[0])
    assert "aerate at dawn" in text            # the SHARE-AS insight
    assert "private ctx" in text               # original context, for the owner's eyes
