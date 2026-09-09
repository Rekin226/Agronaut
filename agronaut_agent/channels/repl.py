"""ReplChannel — a terminal channel, and the proof that ChannelAdapter isn't Telegram-shaped.

A second, dependency-free implementation of the same contract the Telegram adapter uses:
translate a platform's input into agent.handle_message and print the reply. Needs a
configured tool-calling provider (e.g. LLM_PROVIDER=nvidia).
"""

from __future__ import annotations

import logging
import os

from ..core import AgronautAgent
from .base import ChannelAdapter, explain_turn_failure


class _DropTracebacks(logging.Filter):
    """Keep the warning, drop the stack trace, for an interactive session only.

    `agent/llm.py` logs a provider failure with `exc_info=True`, which is right for
    `agronaut bot` where the trace goes to a log file nobody is staring at. In a REPL it
    prints forty lines of httpx internals directly at a person who is then handed a plain
    explanation anyway, and the trace is what they read first. The one-line warning is
    genuinely useful, so it stays. Set AGRONAUT_DEBUG=1 to get the traces back.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        return True


def _quiet_tracebacks() -> None:
    """Install the filter where the records actually get emitted.

    A filter on a logger only sees records logged directly on it, never ones propagated from
    a child, so filtering the root logger does nothing to `agent.llm`'s warnings. Handler
    filters do see propagated records, but this process has no handlers at all, so logging
    falls back to `logging.lastResort` and prints the trace before anything of ours runs.
    Attaching our own handler is what actually takes effect.
    """
    if os.getenv("AGRONAUT_DEBUG"):
        return
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            handler.addFilter(_DropTracebacks())
        return
    handler = logging.StreamHandler()
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handler.addFilter(_DropTracebacks())
    root.addHandler(handler)


class ReplChannel(ChannelAdapter):
    channel_name = "cli"

    def __init__(self, agent: AgronautAgent, user: str = "local"):
        super().__init__(agent)
        self.user = user

    def run(self) -> None:
        _quiet_tracebacks()
        print("Agronaut REPL — type 'quit' to exit, '/reset' to clear.")
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in {"quit", "exit"}:
                break
            if text == "/reset":
                self.agent.reset(self.channel_name, self.user)
                print("(conversation reset)")
                continue
            if text:
                # The Telegram adapter has guarded a turn for a long time ("never leave the
                # user hanging on an unexpected error"); the REPL did not, so an unreachable
                # provider killed the session with an httpx traceback. That matters more now
                # that `agronaut setup` ends by recommending this channel first: the most
                # recommended path was the least robust one.
                try:
                    reply = self.agent.handle_message(self.channel_name, self.user, text)
                except (EOFError, KeyboardInterrupt):
                    raise
                except Exception as exc:  # noqa: BLE001 — explained, and the session lives
                    reply = explain_turn_failure(exc)
                print("agronaut>", reply)
