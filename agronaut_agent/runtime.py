"""Per-request runtime context so stateless LLM tools can reach the current user's stores.

handle_message() sets the current (MemoryStore, user_id) before running the tool loop; the
memory tools read it. A ContextVar keeps this correct under the bot's worker-thread model
(one in-flight message per thread) without threading user_id through every tool signature.
"""

from __future__ import annotations

import contextvars

_current = contextvars.ContextVar("agronaut_current", default=None)
_followups = contextvars.ContextVar("agronaut_followups", default=None)
_community = contextvars.ContextVar("agronaut_community", default=None)
_calibration = contextvars.ContextVar("agronaut_calibration", default=None)
_attachments = contextvars.ContextVar("agronaut_attachments", default=None)


def set_current(memory_store, user_id: str, followups=None, community=None, calibration=None) -> None:
    _current.set((memory_store, user_id))
    _followups.set(followups)
    _community.set(community)
    _calibration.set(calibration)
    _attachments.set([])   # fresh per-turn sink for files a tool wants to send back


def clear_current() -> None:
    _current.set(None)
    _followups.set(None)
    _community.set(None)
    _calibration.set(None)
    _attachments.set(None)


def add_attachment(path: str) -> None:
    """A tool records a file (e.g. a rendered schematic) to send back with the reply."""
    atts = _attachments.get()
    if atts is not None:
        atts.append(str(path))


def get_attachments() -> list:
    """Files recorded during the current turn, for the channel adapter to deliver."""
    return list(_attachments.get() or [])


def get_current():
    """Return (memory_store, user_id) for the in-flight message, or None outside a turn."""
    return _current.get()


def get_followups():
    """Return the FollowupStore for the in-flight message, or None if unset."""
    return _followups.get()


def get_community():
    """Return the CommunityStore for the in-flight message, or None if unset."""
    return _community.get()


def get_calibration():
    """Return the CalibrationStore for the in-flight message, or None if unset."""
    return _calibration.get()
