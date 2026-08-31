"""Thumbs up/down — the one quality signal that comes from the person who was actually helped.

Code-based evaluators say the retriever found the right file. An LLM judge says the answer used
it. Neither can say the operator got what they needed, and the course puts human feedback in its
own row for exactly that reason.

The design constraint is that this is the closest analytics ever comes to an opinion, so it must
stay a bare rating: no comment field, no free text, nothing that could carry what was said.
"""

import json

import pytest
from langchain_core.messages import AIMessage

from agronaut_agent.analytics import Analytics
from agronaut_agent.core import AgronautAgent


class _Chatty:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Shade the tank and cut the photoperiod.")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGRONAUT_ANALYTICS_PATH", str(tmp_path / "a.jsonl"))
    a = AgronautAgent(chat_model=_Chatty(), db_path=str(tmp_path / "db.sqlite"))
    a._analytics = Analytics(path=tmp_path / "a.jsonl")
    return a


def _rows(agent):
    p = agent._analytics.path
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_thumbs_up_and_down_record_opposite_ratings(agent):
    agent.record_feedback("test", "u1", True)
    agent.record_feedback("test", "u1", False)
    ratings = [r["rating"] for r in _rows(agent) if r["event"] == "feedback"]
    assert ratings == [1, -1]


def test_feedback_acknowledges_the_person(agent):
    assert "thank you" in agent.record_feedback("test", "u1", True).lower()
    assert agent.record_feedback("test", "u1", False) != agent.record_feedback("test", "u1", True)


def test_feedback_is_attributed_to_the_user_only_as_a_hash(agent):
    agent.record_feedback("test", "telegram:123456789", True)
    row = [r for r in _rows(agent) if r["event"] == "feedback"][0]
    assert row["uid"] and "123456789" not in json.dumps(row)


def test_feedback_cannot_carry_a_written_comment(agent):
    """The allowlist is the guarantee, not the calling convention. Even a caller that invents a
    comment field must not be able to persist it."""
    agent._analytics.record("feedback", user_id="u1", rating=-1,
                            comment="the numbers for my tank in Bobo were wrong")
    row = [r for r in _rows(agent) if r["event"] == "feedback"][0]
    assert "comment" not in row and "Bobo" not in json.dumps(row)


def test_summary_reports_the_positive_share(agent):
    for positive in (True, True, True, False):
        agent.record_feedback("test", "u1", positive)
    assert agent._analytics.summarize()["feedback"] == {"up": 3, "down": 1}


def test_feedback_is_not_a_turn_and_does_not_pollute_latency(agent):
    """A rating is not a pipeline run. Counting it as a turn would put a ~0 ms row into the
    latency distribution and quietly drag the p50 down."""
    agent.handle_message("test", "u1", "why is my water green")
    agent.record_feedback("test", "u1", True)
    s = agent._analytics.summarize()
    assert s["latency"]["turn"]["n"] == 1
