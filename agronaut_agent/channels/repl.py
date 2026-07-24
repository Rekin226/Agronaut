"""ReplChannel — a terminal channel, and the proof that ChannelAdapter isn't Telegram-shaped.

A second, dependency-free implementation of the same contract the Telegram adapter uses:
translate a platform's input into agent.handle_message and print the reply. Needs a
configured tool-calling provider (e.g. LLM_PROVIDER=nvidia).
"""

from __future__ import annotations

from ..core import AgronautAgent
from .base import ChannelAdapter


class ReplChannel(ChannelAdapter):
    channel_name = "cli"

    def __init__(self, agent: AgronautAgent, user: str = "local"):
        super().__init__(agent)
        self.user = user

    def run(self) -> None:
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
                print("agronaut>", self.agent.handle_message(self.channel_name, self.user, text))
