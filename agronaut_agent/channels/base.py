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


def explain_turn_failure(exc: BaseException) -> str:
    """Turn an exception from a turn into something an operator can act on.

    A dead model provider is by far the most common way a turn fails, and it is not an
    exotic state: `agronaut setup` will happily save `LLM_PROVIDER=ollama` after reporting
    that Ollama is not running, because the operator may be about to start it. The next
    thing they do is ask a question, and the honest answer is "the model is not reachable",
    not a traceback.

    Every branch ends by naming the sizing engine. It needs no model at all, so there is
    always something true to offer, which is the same rule `setup_wizard.next_steps` follows.
    """
    name = type(exc).__name__
    text = f"{name}: {exc}".lower()

    unreachable = ("connect" in text and "error" in text) or "refused" in text \
        or "max retries" in text or "failed to establish" in text or "timed out" in text
    rejected = any(code in text for code in ("401", "403", "unauthorized", "forbidden",
                                             "invalid api key", "authentication"))
    missing_model = "not found" in text and "model" in text

    import os

    provider = os.getenv("LLM_PROVIDER") or "the configured provider"
    if unreachable:
        where = ""
        if provider == "ollama":
            host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            where = f" at {host}"
        return (f"I could not reach {provider}{where}, so I cannot answer that right now.\n"
                + ("Start it with `ollama serve`, then try again.\n"
                   if provider == "ollama" else
                   "Check your connection, then try again.\n")
                + "Run `agronaut setup` to switch provider. The sizing engine needs no "
                  "model and still works: `agronaut size --help`")
    if rejected:
        return (f"{provider} rejected the API key, so I cannot answer that right now.\n"
                "Run `agronaut setup` to re-enter it. The sizing engine needs no model and "
                "still works: `agronaut size --help`")
    if missing_model:
        model = os.getenv("LLM_MODEL") or "the configured model"
        pull = f"`ollama pull {model}`" if provider == "ollama" else "your provider's console"
        return (f"{provider} does not have {model}. Get it with {pull}, then try again.\n"
                "The sizing engine needs no model and still works: `agronaut size --help`")
    return (f"That turn failed ({name}: {exc}).\n"
            "If it keeps happening, `agronaut setup` re-checks your configuration. The "
            "sizing engine needs no model and still works: `agronaut size --help`")
