"""Optional full-content tracing to a local Arize Phoenix, for the operator debugging their own bot.

`agronaut traces` records the SHAPE of a turn and never its words; that is a promise in
docs/dpg/PRIVACY.md and it stays true. Sometimes the shape is not enough: why did the model
call that tool, what exactly did the retriever hand it. Phoenix answers that by recording the
prompts, replies and tool inputs in full, which is why this module is:

  - OFF unless AGRONAUT_PHOENIX=on. Nothing is imported or sent otherwise.
  - LOCAL ONLY. The traces go to a Phoenix on this machine (127.0.0.1:6006 by default). An
    endpoint on another host is refused unless AGRONAUT_PHOENIX_ALLOW_REMOTE=1, because that
    would send growers' messages off the operator's computer.
  - Never a dependency. `pip install "agronaut[phoenix]"` adds the two client libraries; the
    Phoenix server itself runs separately (`agronaut phoenix`).

Agronaut runs its own tool loop rather than a LangChain agent, so without help each model and
tool call would arrive in Phoenix as a separate trace. `begin_turn` opens one parent span per
turn (from runtime.start_turn) so a turn reads as one tree, the same unit `agronaut traces`
uses, carrying the same trace id.
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from urllib.parse import urlparse

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://127.0.0.1:6006/v1/traces"
PROJECT = "agronaut"
INSTALL_HINT = 'pip install "agronaut[phoenix]"'
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_state = {"status": None}            # set once per process by enable()
_tracer = {"t": None}
_span: ContextVar = ContextVar("agronaut_phoenix_span", default=None)


def requested() -> bool:
    return os.getenv("AGRONAUT_PHOENIX", "").strip().lower() in {"1", "on", "true", "yes"}


def endpoint() -> str:
    return os.getenv("AGRONAUT_PHOENIX_ENDPOINT", "").strip() or DEFAULT_ENDPOINT


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


def enable() -> str:
    """Turn tracing on if asked, once per process. Returns a one-line status, never raises:
    tracing is a debugging aid and must not stop a bot from starting."""
    if _state["status"] is not None:
        return _state["status"]
    _state["status"] = _enable()
    if requested():
        log.info("Phoenix tracing: %s", _state["status"])
    return _state["status"]


def _enable() -> str:
    if not requested():
        return "off"
    url = endpoint()
    if not is_local(url) and os.getenv("AGRONAUT_PHOENIX_ALLOW_REMOTE", "") != "1":
        return (f"refused: {url} is not on this machine, and Phoenix traces hold growers' "
                "messages in full. Set AGRONAUT_PHOENIX_ALLOW_REMOTE=1 to send them anyway.")
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register
    except ImportError:
        return f"unavailable: the client libraries are not installed ({INSTALL_HINT})"
    try:
        provider = register(endpoint=url, project_name=PROJECT, batch=True, verbose=False,
                            set_global_tracer_provider=True)
        LangChainInstrumentor().instrument(tracer_provider=provider)
        _tracer["t"] = provider.get_tracer("agronaut")
    except Exception as exc:  # noqa: BLE001 (a debugging aid never blocks the bot)
        return f"failed: {exc}"
    return f"on: sending full traces to {url} (project '{PROJECT}')"


def begin_turn(trace_id: str | None) -> None:
    """Open the turn's parent span. A no-op unless tracing is on."""
    tracer = _tracer["t"]
    if tracer is None:
        return
    try:
        from opentelemetry import context, trace
        span = tracer.start_span("agronaut.turn",
                                 attributes={"agronaut.trace_id": trace_id or "",
                                             "openinference.span.kind": "AGENT"})
        token = context.attach(trace.set_span_in_context(span))
        _span.set((span, token))
    except Exception:  # noqa: BLE001
        _span.set(None)


def end_turn() -> None:
    held = _span.get()
    if not held:
        return
    span, token = held
    _span.set(None)
    try:
        from opentelemetry import context
        context.detach(token)
        span.end()
    except Exception:  # noqa: BLE001
        pass


def server_env(base: dict | None = None) -> dict:
    """The environment `agronaut phoenix` starts the server with.

    Bound to this machine only, because the traces hold full messages; and with Phoenix's
    own web analytics switched off, which its self-hosted builds enable by default (page
    views and clicks, per Phoenix's docs, never trace data). Values already set win.
    """
    env = dict(os.environ if base is None else base)
    for key, value in (("PHOENIX_HOST", "127.0.0.1"), ("PHOENIX_PORT", "6006"),
                       ("PHOENIX_TELEMETRY_ENABLED", "false"),
                       ("PHOENIX_ALLOW_EXTERNAL_RESOURCES", "false")):
        env.setdefault(key, value)
    return env


def serve() -> int:  # pragma: no cover - starts a server
    """`agronaut phoenix`: run the Phoenix server with the settings above."""
    import shutil
    import subprocess

    exe = shutil.which("phoenix")
    if not exe:
        print("The Phoenix server is not installed. It is large (about 600 MB), so it lives in\n"
              "its own environment rather than Agronaut's:\n\n"
              "    uv tool install arize-phoenix        (or: pipx install arize-phoenix)\n\n"
              f"Then: {INSTALL_HINT} for the client side, and run `agronaut phoenix` again.")
        return 2
    env = server_env()
    print(f"Phoenix on http://{env['PHOENIX_HOST']}:{env['PHOENIX_PORT']}  (this machine only, "
          "telemetry off). Stop with Ctrl+C.\n"
          "In another terminal, run the bot with AGRONAUT_PHOENIX=on to send it traces.")
    return subprocess.call([exe, "serve"], env=env)
