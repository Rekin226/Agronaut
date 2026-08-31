"""Turn tracing and cost telemetry.

Before this, analytics rows were independent counters: a `message`, a `retrieval` and three
`tool_call` rows landed in the log with nothing tying them together, so "that answer was slow"
could never be resolved into "because the model was called four times". These tests assert the
three properties that make the log a trace instead of a tally:

  1. every event a turn produces carries the SAME trace id,
  2. a nested turn (photo, voice) joins its parent's trace rather than minting a second one,
  3. the turn is measured even when it raises, because a failed turn is exactly the one whose
     latency you must not drop from the distribution.

And the property that must survive all of it: still no content, ever.
"""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent import runtime
from agronaut_agent.analytics import Analytics
from agronaut_agent.core import AgronautAgent


class _FakeChat:
    """One tool call, then a final answer. Reports token usage the way LangChain normalises it."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if [m for m in messages if isinstance(m, ToolMessage)]:
            return AIMessage(content="Sized it.",
                             usage_metadata={"input_tokens": 700, "output_tokens": 40, "total_tokens": 740})
        return AIMessage(
            content="",
            usage_metadata={"input_tokens": 500, "output_tokens": 20, "total_tokens": 520},
            tool_calls=[{
                "name": "size_aquaponics_system",
                "id": "call_1",
                "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                         "temperature_c": 27, "water_budget_lpd": 300},
            }],
        )


@pytest.fixture(autouse=True)
def _clean_turn():
    """No test may leak an open turn into the next one — a ContextVar left set would make the
    next test's events join a trace it never opened."""
    yield
    runtime.end_turn()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGRONAUT_ANALYTICS_PATH", str(tmp_path / "a.jsonl"))
    a = AgronautAgent(chat_model=_FakeChat(), db_path=str(tmp_path / "db.sqlite"))
    a._analytics = Analytics(path=tmp_path / "a.jsonl")
    return a


def _rows(agent):
    p = agent._analytics.path
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# --- the trace ---------------------------------------------------------------

def test_every_event_in_a_turn_shares_one_trace_id(agent):
    agent.handle_message("test", "u1", "size me a 12 m2 tilapia system")
    rows = _rows(agent)
    traces = {r.get("trace") for r in rows}
    assert None not in traces, "an event escaped the trace"
    assert len(traces) == 1, f"one turn produced {len(traces)} traces"
    assert {"message", "llm_call", "tool_call", "turn"} <= {r["event"] for r in rows}


def test_two_turns_get_two_trace_ids(agent):
    agent.handle_message("test", "u1", "hello")
    agent.handle_message("test", "u1", "hello again")
    assert len({r["trace"] for r in _rows(agent)}) == 2


def test_trace_id_is_not_derived_from_the_user(agent):
    """It groups events within a turn and must not become a second user identifier: the same
    user on two turns gets two different ids."""
    agent.handle_message("test", "u1", "hello")
    agent.handle_message("test", "u1", "hello")
    rows = _rows(agent)
    assert len({r["trace"] for r in rows}) == 2
    uids = {r["uid"] for r in rows if r.get("uid")}
    assert len(uids) == 1                      # same user...
    assert not (uids & {r["trace"] for r in rows})   # ...but never the same token


def test_nested_turn_joins_rather_than_minting_a_second_trace():
    """A photo turn runs a text turn inside itself. One photo must read as one turn."""
    assert runtime.start_turn() is True
    outer = runtime.trace_id()
    assert runtime.start_turn() is False, "inner turn wrongly claimed ownership"
    assert runtime.trace_id() == outer
    runtime.end_turn()


# --- the numbers -------------------------------------------------------------

def test_turn_records_end_to_end_latency_and_model_share(agent):
    agent.handle_message("test", "u1", "size me a 12 m2 tilapia system")
    turn = [r for r in _rows(agent) if r["event"] == "turn"][0]
    assert turn["latency_ms"] >= 0
    assert turn["llm_calls"] == 2 and turn["tool_calls"] == 1
    assert turn["llm_ms"] <= turn["latency_ms"], "model time cannot exceed the turn"


def test_token_usage_is_summed_across_the_turn(agent):
    agent.handle_message("test", "u1", "size me a 12 m2 tilapia system")
    turn = [r for r in _rows(agent) if r["event"] == "turn"][0]
    assert turn["tokens_in"] == 1200 and turn["tokens_out"] == 60


def test_missing_usage_is_omitted_not_zeroed(tmp_path, monkeypatch):
    """A provider that reports no usage must leave the field ABSENT. Recording 0 would let a
    provider with no usage metadata silently drag every cost aggregate toward zero."""
    class _NoUsage(_FakeChat):
        def invoke(self, messages):
            return AIMessage(content="hi")

    monkeypatch.setenv("AGRONAUT_ANALYTICS_PATH", str(tmp_path / "a.jsonl"))
    a = AgronautAgent(chat_model=_NoUsage(), db_path=str(tmp_path / "db.sqlite"))
    a._analytics = Analytics(path=tmp_path / "a.jsonl")
    a.handle_message("test", "u1", "hello")
    turn = [r for r in _rows(a) if r["event"] == "turn"][0]
    assert "tokens_in" not in turn and "tokens_out" not in turn
    assert turn["llm_calls"] == 1


def test_a_failed_turn_is_still_measured(tmp_path, monkeypatch):
    class _Explodes(_FakeChat):
        def invoke(self, messages):
            raise RuntimeError("provider down")

    monkeypatch.setenv("AGRONAUT_ANALYTICS_PATH", str(tmp_path / "a.jsonl"))
    a = AgronautAgent(chat_model=_Explodes(), db_path=str(tmp_path / "db.sqlite"))
    a._analytics = Analytics(path=tmp_path / "a.jsonl")
    with pytest.raises(RuntimeError):
        a.handle_message("test", "u1", "hello")
    turns = [r for r in _rows(a) if r["event"] == "turn"]
    assert len(turns) == 1 and turns[0]["latency_ms"] >= 0


def test_llm_call_events_carry_their_stage(agent):
    agent.handle_message("test", "u1", "size me a 12 m2 tilapia system")
    stages = [r["stage"] for r in _rows(agent) if r["event"] == "llm_call"]
    assert stages == ["agent", "agent"]


# --- the guarantee that must survive -----------------------------------------

def test_tracing_records_no_message_content(agent):
    agent.handle_message("test", "u1", "my tilapia in tank 3 are gasping at dawn")
    blob = json.dumps(_rows(agent))
    for word in ("tilapia", "gasping", "tank"):
        assert word not in blob


# --- reading traces back -----------------------------------------------------

def test_traces_groups_a_turn_into_one_path(agent):
    agent.handle_message("test", "u1", "size me a 12 m2 tilapia system")
    traces = agent._analytics.traces()
    assert len(traces) == 1
    t = traces[0]
    assert t["latency_ms"] is not None and t["tokens_in"] == 1200
    assert [e["event"] for e in t["events"]][0] == "message"
    assert [e["event"] for e in t["events"]][-1] == "turn"


def test_summarize_reports_latency_percentiles_and_tokens(agent):
    for _ in range(3):
        agent.handle_message("test", "u1", "size me a 12 m2 tilapia system")
    s = agent._analytics.summarize()
    assert s["latency"]["turn"]["n"] == 3
    assert s["latency"]["llm"]["p50"] is not None
    assert s["tokens"]["in"] == 3600 and s["tokens"]["out"] == 180
