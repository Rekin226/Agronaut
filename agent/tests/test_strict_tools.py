"""Strict tool schemas on Claude: within the API's limits, never at the cost of a turn."""

import pytest
from langchain_core.tools import tool

from agent import llm as L


@tool
def size(area_m2: float, temp_c: float, note: str | None = None, kind: str = "raft") -> str:
    """Size a system."""
    return "ok"


@tool
def log(ph: float | None = None, ammonia: float | None = None) -> str:
    """Log readings."""
    return "ok"


@tool
def free_form(updates: dict) -> str:
    """Takes an open dict, which strict mode forbids."""
    return "ok"


def test_strict_keeps_the_optional_parameters_optional_and_closes_objects():
    tools, made = L.strict_where_possible([size, log], ["size"])
    assert made == ["size"]
    spec = tools[0]
    assert spec["strict"] is True
    assert spec["input_schema"]["required"] == ["area_m2", "temp_c"]   # not every parameter
    assert spec["input_schema"]["additionalProperties"] is False
    assert tools[1] is log                                              # not in priority: loose


def test_open_objects_and_the_budgets_decide_who_is_strict(monkeypatch):
    _, made = L.strict_where_possible([free_form, size], ["free_form", "size"])
    assert made == ["size"]
    monkeypatch.setattr(L, "STRICT_MAX_OPTIONAL", 2)         # size has 2 optional, log 2
    _, made = L.strict_where_possible([size, log], ["size", "log"])
    assert made == ["size"]
    monkeypatch.setattr(L, "STRICT_MAX_OPTIONAL", 24)
    monkeypatch.setattr(L, "STRICT_MAX_UNION", 2)            # size 1 union, log 2
    _, made = L.strict_where_possible([size, log], ["size", "log"])
    assert made == ["size"]


def test_the_real_priority_list_fits_the_published_limits():
    from agronaut_agent.tools import AGRONAUT_TOOLS, STRICT_TOOL_PRIORITY
    _, made = L.strict_where_possible(list(AGRONAUT_TOOLS), STRICT_TOOL_PRIORITY)
    assert made == STRICT_TOOL_PRIORITY, "a priority tool no longer fits; reorder or trim"


class _Model:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def invoke(self, messages, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return "reply"


def test_a_rejected_grammar_falls_back_to_loose_schemas_for_good():
    strict = _Model(RuntimeError("Error code: 400 - The compiled grammar is too large"))
    loose = _Model()
    guard = L.StrictGuard(strict, loose)
    assert guard.invoke([]) == "reply" and guard.invoke([]) == "reply"
    assert strict.calls == 1 and loose.calls == 2


def test_other_errors_are_not_swallowed():
    guard = L.StrictGuard(_Model(RuntimeError("Error code: 529 overloaded")), _Model())
    with pytest.raises(RuntimeError):
        guard.invoke([])
