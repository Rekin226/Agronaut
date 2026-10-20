"""Optional Phoenix tracing: off unless asked, local unless overridden, never in the way.

These traces carry message text, unlike everything else Agronaut records, so the guarantees
that matter are about when they are NOT produced. No Phoenix server and no network here.
"""

import pytest

from agronaut_agent import cli, otel_phoenix, runtime


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for key in ("AGRONAUT_PHOENIX", "AGRONAUT_PHOENIX_ENDPOINT", "AGRONAUT_PHOENIX_ALLOW_REMOTE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(otel_phoenix._state, "status", None)
    monkeypatch.setitem(otel_phoenix._tracer, "t", None)


def test_off_by_default_and_imports_nothing(monkeypatch):
    import builtins
    real = builtins.__import__

    def guard(name, *a, **k):
        assert not name.startswith(("phoenix", "openinference")), f"imported {name} while off"
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guard)
    assert otel_phoenix.enable() == "off"


def test_another_host_is_refused_because_traces_hold_messages(monkeypatch):
    monkeypatch.setenv("AGRONAUT_PHOENIX", "on")
    monkeypatch.setenv("AGRONAUT_PHOENIX_ENDPOINT", "https://phoenix.example.com/v1/traces")
    assert otel_phoenix.enable().startswith("refused")


@pytest.mark.parametrize("url,local", [
    ("http://127.0.0.1:6006/v1/traces", True), ("http://localhost:6006/v1/traces", True),
    ("http://[::1]:6006/v1/traces", True), ("http://192.168.1.20:6006/v1/traces", False),
    ("https://app.phoenix.arize.com/v1/traces", False),
])
def test_only_this_machine_counts_as_local(url, local):
    assert otel_phoenix.is_local(url) is local


def test_missing_libraries_say_how_to_install_and_do_not_raise(monkeypatch):
    import builtins
    real = builtins.__import__

    def missing(name, *a, **k):
        if name.startswith(("phoenix", "openinference")):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setenv("AGRONAUT_PHOENIX", "on")
    monkeypatch.setattr(builtins, "__import__", missing)
    status = otel_phoenix.enable()
    assert status.startswith("unavailable") and "agronaut[phoenix]" in status


def test_enable_runs_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(otel_phoenix, "_enable", lambda: calls.append(1) or "off")
    otel_phoenix.enable()
    otel_phoenix.enable()
    assert calls == [1]


class _Span:
    def __init__(self, name, attributes):
        self.name, self.attributes, self.ended = name, attributes, False

    def end(self):
        self.ended = True


class _Tracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, attributes=None):
        s = _Span(name, attributes)
        self.spans.append(s)
        return s


def test_a_turn_is_one_parent_span_carrying_the_same_trace_id(monkeypatch):
    pytest.importorskip("opentelemetry")
    tracer = _Tracer()
    monkeypatch.setitem(otel_phoenix._tracer, "t", tracer)
    assert runtime.start_turn() is True
    tid = runtime.trace_id()
    assert runtime.start_turn() is False        # a photo turn's inner call joins, no 2nd span
    runtime.end_turn()
    assert [s.name for s in tracer.spans] == ["agronaut.turn"]
    assert tracer.spans[0].attributes["agronaut.trace_id"] == tid
    assert tracer.spans[0].ended


def test_turns_cost_nothing_when_tracing_is_off():
    assert runtime.start_turn() is True
    runtime.end_turn()
    assert otel_phoenix._span.get() is None


def test_the_server_is_bound_to_this_machine_with_its_telemetry_off():
    env = otel_phoenix.server_env({})
    assert env["PHOENIX_HOST"] == "127.0.0.1"
    assert env["PHOENIX_TELEMETRY_ENABLED"] == "false"
    assert env["PHOENIX_ALLOW_EXTERNAL_RESOURCES"] == "false"
    assert otel_phoenix.server_env({"PHOENIX_PORT": "7007"})["PHOENIX_PORT"] == "7007"


def test_agronaut_phoenix_is_a_command():
    assert cli._build_parser().parse_args(["phoenix"]).func is cli._cmd_phoenix
