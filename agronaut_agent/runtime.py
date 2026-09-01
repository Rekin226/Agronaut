"""Per-request runtime context so stateless LLM tools can reach the current user's stores.

handle_message() sets the current (MemoryStore, user_id) before running the tool loop; the
memory tools read it. A ContextVar keeps this correct under the bot's worker-thread model
(one in-flight message per thread) without threading user_id through every tool signature.

The same mechanism carries the TURN TRACE. Analytics events were previously independent
counter rows: a `message`, a `retrieval` and three `tool_call` rows landed in the log with
nothing tying them to each other, so "this answer was slow" could never be resolved into
"because retrieval returned nothing and the model then called four tools". A per-turn id
that every event in the turn carries is the whole difference between counting and tracing.

The id is minted per TURN, not per user: a fresh random token each time, never derived from
the user id and never persisted alongside anything identifying. It links rows within one
turn and says nothing about who produced them.
"""

from __future__ import annotations

import contextvars
import uuid

_current = contextvars.ContextVar("agronaut_current", default=None)
_followups = contextvars.ContextVar("agronaut_followups", default=None)
_community = contextvars.ContextVar("agronaut_community", default=None)
_calibration = contextvars.ContextVar("agronaut_calibration", default=None)
_readings = contextvars.ContextVar("agronaut_readings", default=None)
_attachments = contextvars.ContextVar("agronaut_attachments", default=None)
_trace = contextvars.ContextVar("agronaut_trace", default=None)
_metrics = contextvars.ContextVar("agronaut_metrics", default=None)


def set_current(memory_store, user_id: str, followups=None, community=None,
                calibration=None, readings=None) -> None:
    _current.set((memory_store, user_id))
    _followups.set(followups)
    _community.set(community)
    _calibration.set(calibration)
    _readings.set(readings)
    _attachments.set([])   # fresh per-turn sink for files a tool wants to send back


def clear_current() -> None:
    _current.set(None)
    _followups.set(None)
    _community.set(None)
    _calibration.set(None)
    _readings.set(None)
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


def get_readings():
    """Return the ReadingStore for the in-flight message, or None if unset."""
    return _readings.get()


# --- turn tracing -------------------------------------------------------------

def start_turn() -> bool:
    """Open a traced turn. Returns True if THIS call opened it, meaning the caller owns
    ending it; False if a turn was already in flight and this call joins it.

    The join case is not an edge case. A photo turn calls handle_message() internally, and
    a voice turn does too. Without joining, the inner call would mint a second id and one
    photo would appear in the log as two unrelated turns, which is precisely the confusion
    tracing exists to remove.
    """
    if _trace.get():
        return False
    _trace.set(uuid.uuid4().hex[:12])
    _metrics.set({"llm_calls": 0, "llm_ms": 0, "tokens_in": 0, "tokens_out": 0,
                  "tool_calls": 0, "usage_seen": False})
    return True


def end_turn() -> None:
    """Close the traced turn. Only the caller that opened it should call this."""
    _trace.set(None)
    _metrics.set(None)


def trace_id() -> str | None:
    """The current turn's trace id, or None outside a turn."""
    return _trace.get()


def record_llm_call(latency_ms: int, tokens_in=None, tokens_out=None) -> None:
    """Accumulate one model call into the turn's totals.

    Token counts are optional because they are genuinely optional: not every provider
    returns usage metadata, and a missing count must stay missing rather than be recorded
    as a zero that would quietly understate real cost in every aggregate.
    """
    m = _metrics.get()
    if m is None:
        return
    m["llm_calls"] += 1
    m["llm_ms"] += int(latency_ms)
    if tokens_in is not None:
        m["tokens_in"] += int(tokens_in)
        m["usage_seen"] = True
    if tokens_out is not None:
        m["tokens_out"] += int(tokens_out)
        m["usage_seen"] = True


def record_tool_call() -> None:
    """Count one tool invocation into the turn's totals."""
    m = _metrics.get()
    if m is not None:
        m["tool_calls"] += 1


def turn_metrics() -> dict:
    """Totals accumulated during the current turn; {} outside a turn."""
    return dict(_metrics.get() or {})
