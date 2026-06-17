"""Per-request runtime context so stateless LLM tools can reach the current user's stores.

handle_message() sets the current (MemoryStore, user_id) before running the tool loop; the
memory tools read it. A ContextVar keeps this correct under the bot's worker-thread model
(one in-flight message per thread) without threading user_id through every tool signature.
"""

from __future__ import annotations

import contextvars

_current = contextvars.ContextVar("agronaut_current", default=None)


def set_current(memory_store, user_id: str) -> None:
    _current.set((memory_store, user_id))


def clear_current() -> None:
    _current.set(None)


def get_current():
    """Return (memory_store, user_id) for the in-flight message, or None outside a turn."""
    return _current.get()
