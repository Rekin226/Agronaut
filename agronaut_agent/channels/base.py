"""ChannelAdapter — the contract every channel (Telegram, Discord, WhatsApp) implements.

An adapter only translates a platform's native message events into
`agent.handle_message(channel, native_user_id, text)` and sends the reply back. The brain,
tools, memory, and persistence live in AgronautAgent and never change per channel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core import AgronautAgent


class ChannelAdapter(ABC):
    channel_name: str = "base"

    def __init__(self, agent: AgronautAgent):
        self.agent = agent

    @abstractmethod
    def run(self) -> None:
        """Start listening for messages (blocking)."""
        raise NotImplementedError


def chunk(text: str, size: int = 4000) -> list[str]:
    """Split a long reply to fit platform message-size caps (Telegram's is 4096)."""
    if len(text) <= size:
        return [text]
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > size and buf:
            parts.append(buf)
            buf = ""
        # a single line longer than `size` gets hard-split
        while len(line) > size:
            parts.append(line[:size])
            line = line[size:]
        buf += line
    if buf:
        parts.append(buf)
    return parts
