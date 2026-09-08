"""Slash commands on a channel with no command menu.

The invariants: a command reaches the deterministic method Telegram reaches, an unknown
command is refused rather than passed to the LLM, ordinary prose is left alone, and the two
channels do not drift apart in what they support.
"""

import json
import pathlib

import pytest

from agronaut_agent.channels import commands


class _FakeAgent:
    """Records what the router asked the agent to do."""

    def __init__(self):
        self.calls = []

    def log_readings_direct(self, channel, identity, args):
        self.calls.append(("log", args))
        return "logged"

    def forecast_direct(self, channel, identity, days=7, greenhouse="poly"):
        self.calls.append(("forecast", days, greenhouse))
        return "forecast"

    def advise_direct(self, channel, identity, days=7, greenhouse="poly"):
        self.calls.append(("advise", days, greenhouse))
        return "proposal"

    def decide_direct(self, channel, identity, approve, numbers):
        self.calls.append(("decide", approve, numbers))
        return "decided"

    def record_feedback(self, channel, identity, positive):
        self.calls.append(("feedback", positive))
        return "noted"

    def profile_text(self, channel, identity):
        self.calls.append(("profile",))
        return "tilapia, 20 m2"

    def export_user_data(self, channel, identity):
        self.calls.append(("export",))
        return {"profile": {"crop": "lettuce"}}

    def reset(self, channel, identity):
        self.calls.append(("reset",))

    def forget_everything(self, channel, identity):
        self.calls.append(("forget",))

    def delete_me(self, channel, identity):
        self.calls.append(("delete",))

    def handle_message(self, *a, **k):  # pragma: no cover - must never be reached here
        raise AssertionError("a slash command was handed to the LLM")


def _run(text, agent=None):
    agent = agent or _FakeAgent()
    return commands.dispatch(agent, "whatsapp", "whatsapp:886", text), agent


# --- what is and is not a command ---------------------------------------------------------

@pytest.mark.parametrize("text", ["/log", "/forecast", "/HELP", "/delete_me", "  /whoami  "])
def test_recognised_as_commands(text):
    assert commands.looks_like_command(text)


@pytest.mark.parametrize("text", [
    "", "   ", "hello", "what about 20 m2?", "/", "/123",
    "my pump is at /var/log something",
])
def test_not_commands(text):
    assert not commands.looks_like_command(text)


def test_prose_is_left_for_the_agent():
    """None is the signal to hand the text to the LLM."""
    reply, agent = _run("I have a 20 m2 greenhouse, tilapia and lettuce")
    assert reply is None and agent.calls == []


def test_an_unknown_command_is_refused_not_guessed():
    """A typo must be told, not quietly answered by a model as if it were prose."""
    reply, agent = _run("/forcast")
    assert reply is not None
    assert "/forcast" in reply.text and "/help" in reply.text
    assert agent.calls == []


# --- the deterministic commands -----------------------------------------------------------

def test_log_parses_readings_and_reaches_the_twin():
    reply, agent = _run("/log ammonia 0.5 nitrate 40 temp 27")
    assert reply.text == "logged"
    kind, args = agent.calls[0]
    assert kind == "log"
    assert args["ammonia_mg_l"] == 0.5
    assert args["nitrate_mg_l"] == 40
    assert args["water_temp_c"] == 27


def test_log_without_readings_explains_instead_of_logging_nothing():
    reply, agent = _run("/log")
    assert "ammonia" in reply.text and agent.calls == []


def test_forecast_and_advise_take_days_and_cover():
    reply, agent = _run("/forecast 10 shade")
    assert agent.calls[0] == ("forecast", 10, "shade")
    reply, agent = _run("/advise heated")
    assert agent.calls[0] == ("advise", 7, "heated")


def test_forecast_days_are_clamped_to_the_weather_window():
    _, agent = _run("/forecast 99")
    assert agent.calls[0][1] == 15
    _, agent = _run("/forecast 1")
    assert agent.calls[0][1] == 2


@pytest.mark.parametrize("text,expected", [
    ("/approve 1 3", [1, 3]),
    ("/approve 1,3", [1, 3]),
    ("/reject 2 and 4", [2, 4]),
    ("/approve 3 1 3", [1, 3]),
])
def test_approve_and_reject_parse_item_numbers(text, expected):
    _, agent = _run(text)
    kind, approve, nums = agent.calls[0]
    assert kind == "decide" and nums == expected
    assert approve is text.startswith("/approve")


def test_a_decision_with_no_numbers_asks_rather_than_guessing():
    reply, agent = _run("/approve")
    assert "Which items" in reply.text and agent.calls == []


def test_ranges_are_not_expanded():
    """"1-4" is one hyphen from approving items nobody read, on a path that records a human
    decision about live fish."""
    _, agent = _run("/approve 1-4")
    assert agent.calls[0][2] == [1, 4]


# --- data rights --------------------------------------------------------------------------

def test_export_writes_a_file_rather_than_a_wall_of_text():
    reply, agent = _run("/export")
    assert ("export",) in agent.calls
    assert reply.document and pathlib.Path(reply.document).exists()
    assert json.loads(pathlib.Path(reply.document).read_text())["profile"]["crop"] == "lettuce"
    assert reply.document_mime == "application/json"


@pytest.mark.parametrize("text,expected", [
    ("/reset", ("reset",)), ("/forget", ("forget",)),
    ("/delete_me", ("delete",)), ("/deleteme", ("delete",)),
])
def test_the_erasure_commands_reach_the_agent(text, expected):
    reply, agent = _run(text)
    assert expected in agent.calls and reply.text


def test_help_lists_the_commands_that_need_no_model():
    reply, _ = _run("/help")
    for c in ("/log", "/forecast", "/advise", "/approve", "/export", "/delete_me"):
        assert c in reply.text


def test_group_style_suffix_is_accepted():
    """Telegram sends "/help@AgronautBOT" in groups; the same spelling must not 404."""
    reply, _ = _run("/help@AgronautBOT")
    assert "/log" in reply.text


# --- the two channels must not drift ------------------------------------------------------

def test_every_telegram_command_exists_on_the_text_router():
    """Telegram keeps its native handlers because they earn the platform menu, so the two
    lists are separate. This is what stops a command being added to one and forgotten on
    the other — which is exactly how WhatsApp ended up with none of them."""
    import inspect
    import re

    from agronaut_agent.channels import telegram_adapter

    # Read the names out of _command_specs' source rather than calling it: the method
    # returns bound handlers, so invoking it needs a live adapter with a bot token.
    src = inspect.getsource(telegram_adapter.TelegramAdapter._command_specs)
    telegram = set(re.findall(r'\(\s*"([a-z_]+)"\s*,\s*self\._on_', src))
    assert len(telegram) > 10, f"parsed too few Telegram commands: {telegram}"
    agent = _FakeAgent()
    missing = []
    for name in telegram:
        reply = commands.dispatch(agent, "whatsapp", "u", f"/{name} 1")
        # None means "handled as prose" (the mode switches); a refusal means unimplemented.
        if reply is not None and reply.text.startswith("I don't know"):
            missing.append(name)
    assert not missing, f"commands Telegram has and the text router does not: {sorted(missing)}"
