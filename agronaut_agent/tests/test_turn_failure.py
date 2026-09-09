"""A failed turn has to say what to do, and must not take the session with it.

The Telegram adapter has guarded a turn for a long time ("never leave the user hanging on an
unexpected error"). The REPL did not, so an unreachable model provider killed it with an
httpx traceback. That became urgent when `agronaut setup` started recommending the terminal
first: the most recommended channel was the least robust one.
"""

import logging

import pytest

from agronaut_agent.channels.base import explain_turn_failure
from agronaut_agent.channels.repl import ReplChannel, _DropTracebacks


class _Boom:
    """An agent whose turn always fails, the way a dead provider makes it fail."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def handle_message(self, *a, **k):
        self.calls += 1
        raise self.exc

    def reset(self, *a, **k):
        pass


def test_an_unreachable_provider_is_explained_not_dumped(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    msg = explain_turn_failure(ConnectionError("[Errno 61] Connection refused"))
    assert "could not reach ollama" in msg.lower()
    assert "http://localhost:11434" in msg
    assert "ollama serve" in msg


def test_a_rejected_key_is_not_reported_as_a_network_problem(monkeypatch):
    """Different fix entirely: retyping a key versus starting a server."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    msg = explain_turn_failure(RuntimeError("401 Unauthorized: invalid api key"))
    assert "rejected the api key" in msg.lower()
    assert "agronaut setup" in msg


def test_every_explanation_offers_something_that_still_works(monkeypatch):
    """The sizing engine needs no model at all, so there is always a true thing to offer.
    Same rule the setup wizard's closing advice follows."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    for exc in (ConnectionError("refused"),
                RuntimeError("401 unauthorized"),
                RuntimeError("model not found"),
                ValueError("something nobody predicted")):
        assert "agronaut size" in explain_turn_failure(exc)


def test_the_repl_survives_a_failing_turn(monkeypatch, capsys):
    """The regression this file exists for: one bad turn used to end the session."""
    agent = _Boom(ConnectionError("[Errno 61] Connection refused"))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    replies = iter(["how big a tank for 50 tilapia?", "and for 100?", "quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(replies))

    ReplChannel(agent).run()

    out = capsys.readouterr().out
    assert agent.calls == 2, "the session died on the first failure"
    assert out.count("could not reach ollama") == 2
    assert "Traceback" not in out


def test_ctrl_c_still_exits(monkeypatch):
    """The guard must not swallow the interrupt that ends the session."""
    agent = _Boom(KeyboardInterrupt())
    monkeypatch.setattr("builtins.input", lambda *a: "anything")
    with pytest.raises(KeyboardInterrupt):
        ReplChannel(agent).run()


def test_the_traceback_filter_keeps_the_message(caplog):
    """Drop the forty lines of httpx internals, keep the one line that says what happened."""
    record = logging.LogRecord("agent.llm", logging.WARNING, __file__, 1,
                               "primary LLM failed; retrying once before fallback", (), None)
    record.exc_text = "Traceback (most recent call last): ..."
    assert _DropTracebacks().filter(record) is True
    assert record.exc_info is None
    assert record.exc_text is None
    assert "primary LLM failed" in record.getMessage()
