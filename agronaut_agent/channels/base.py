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


def room_identity(chat_id, chat_type: str | None, user_id) -> str:
    """Stable per-PERSON identity for memory/profile keying.

    In a 1:1 chat (chat_id == user_id, or type 'private') this is just the chat id — so
    existing single-user memory keys are preserved byte-for-byte. In a shared room (group/
    supergroup/channel) it becomes '<chat>:<user>', so members never collapse into one
    profile. Delivery still targets the room — see delivery_chat_id."""
    shared = (chat_type or "").lower() in {"group", "supergroup", "channel"} \
        and str(chat_id) != str(user_id)
    return f"{chat_id}:{user_id}" if shared else str(chat_id)


def delivery_chat_id(channel_user: str):
    """The address to deliver a proactive message (follow-up) to, derived from an identity.
    For a composite room key '<chat>:<user>' this is the room; for a plain id it's itself."""
    return int(str(channel_user).split(":", 1)[0])


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
