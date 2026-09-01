"""The approval gate: a proposal is inert until a human decides on it, and the decision is
what gets stored — not the model's opinion of it.

These tests guard the properties that make the gate worth having rather than decorative:
only the proposal in front of the operator is decidable, a decision is final, a mis-typed
number is reported instead of swallowed, and erasure reaches this table too.
"""

import pytest

from agronaut_agent.channels.telegram_adapter import parse_item_numbers
from agronaut_agent.store import ProposalStore, _Db
from aqua_model import advisory as A
from aqua_model.production import start_state
from aqua_model.species import get_species

TILAPIA = get_species("tilapia")
USER = "telegram:1"


@pytest.fixture
def store(tmp_path):
    return ProposalStore(_Db(tmp_path / "t.sqlite3"))


def _proposal(tan: float = 2.0, as_of: str = "2026-09-01") -> dict:
    """A proposal with real content: high ammonia on an unmeasured system, which is the
    case that produces both a measure_first gate and an action under it."""
    from dataclasses import replace
    base = start_state(volume_l=3000, fish_count=80, start_weight_g=120.0,
                       water_temp_c=28.0, species=TILAPIA)
    st = replace(base, nitrogen=replace(base.nitrogen, tan_mg_l=tan, no3_mg_l=40.0))
    return A.to_dict(A.recommend(st, TILAPIA, as_of=as_of))


def test_a_recorded_proposal_starts_entirely_undecided(store):
    payload = _proposal()
    store.record(USER, payload)
    latest = store.latest(USER)
    assert latest["items"], "a proposal with recommendations stored no items"
    assert len(latest["items"]) == len(payload["recommendations"])
    assert all(i["status"] == ProposalStore.OPEN for i in latest["items"])
    assert all(i["decided_at"] is None for i in latest["items"])


def test_positions_match_the_rendered_order(store):
    """`/approve 2` must mean the second thing the operator read."""
    payload = _proposal()
    store.record(USER, payload)
    items = store.latest(USER)["items"]
    assert [i["position"] for i in items] == list(range(1, len(items) + 1))
    assert [i["action"] for i in items] == [r["action"] for r in payload["recommendations"]]


def test_approving_records_the_decision(store):
    store.record(USER, _proposal())
    result = store.decide(USER, [1], approve=True)
    assert result["decided"] == [1]
    item = store.latest(USER)["items"][0]
    assert item["status"] == ProposalStore.APPROVED
    assert item["decided_at"]


def test_a_decision_is_final(store):
    """Flipping an approval silently would make the row a statement about now rather than
    about what the person chose then."""
    store.record(USER, _proposal())
    store.decide(USER, [1], approve=True)
    again = store.decide(USER, [1], approve=False)
    assert again["decided"] == []
    assert again["already"] == {1: ProposalStore.APPROVED}
    assert store.latest(USER)["items"][0]["status"] == ProposalStore.APPROVED


def test_only_the_latest_proposal_takes_decisions(store):
    """Otherwise the same two keystrokes mean different things depending on how far the
    chat has scrolled."""
    store.record(USER, _proposal(as_of="2026-09-01"))
    store.record(USER, _proposal(as_of="2026-09-02"))
    result = store.decide(USER, [1], approve=True)
    latest = store.latest(USER)
    assert result["proposal_id"] == latest["id"]
    assert latest["as_of"] == "2026-09-02"
    assert latest["items"][0]["status"] == ProposalStore.APPROVED


def test_an_unknown_number_is_reported_not_swallowed(store):
    store.record(USER, _proposal())
    result = store.decide(USER, [99], approve=True)
    assert result["unknown"] == [99] and result["decided"] == []


def test_deciding_with_no_proposal_on_record_is_not_an_error(store):
    result = store.decide(USER, [1], approve=True)
    assert result["proposal_id"] is None and result["decided"] == []


def test_users_cannot_see_or_decide_each_others_proposals(store):
    store.record("telegram:1", _proposal())
    assert store.latest("telegram:2") is None
    assert store.decide("telegram:2", [1], approve=True)["proposal_id"] is None
    assert store.latest("telegram:1")["items"][0]["status"] == ProposalStore.OPEN


def test_approved_history_carries_the_evidence_class(store):
    """The record has to answer "on what basis did they decide this", or it cannot later
    answer "did following the advice help"."""
    store.record(USER, _proposal())
    store.decide(USER, [1], approve=True)
    hist = store.approved_history(USER)
    assert len(hist) == 1
    assert hist[0]["evidence"] in A.EVIDENCE_CONFIDENCE
    assert hist[0]["confidence"] <= A.EVIDENCE_CONFIDENCE[hist[0]["evidence"]]
    assert hist[0]["context"] and hist[0]["decided_at"]


def test_rejected_items_never_reach_the_approved_history(store):
    store.record(USER, _proposal())
    store.decide(USER, [1], approve=False)
    assert store.approved_history(USER) == []


def test_purge_erases_proposals_and_decisions(store):
    """Right-to-erasure has to reach this table; an approved action is a fact about a person."""
    store.record(USER, _proposal())
    store.decide(USER, [1], approve=True)
    store.purge(USER)
    assert store.latest(USER) is None
    assert store.approved_history(USER) == []


# --- the command parser -----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1 3", [1, 3]),
    ("1,3", [1, 3]),
    ("1 and 3", [1, 3]),
    ("3 1 3", [1, 3]),
    ("", []),
    ("all", []),
    ("0", []),
])
def test_parse_item_numbers(text, expected):
    assert parse_item_numbers(text) == expected


def test_the_parser_refuses_ranges_rather_than_guessing():
    """"1-4" is one hyphen away from approving three items nobody read. Better to take the
    two literal numbers than to invent the two in between."""
    assert parse_item_numbers("1-4") == [1, 4]
