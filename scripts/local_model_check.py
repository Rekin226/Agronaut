"""Does the default local model actually run Agronaut? One real turn through Ollama (#181).

#189 moved the Ollama default to qwen3.5:4b and asks for a 32K context, but it was checked
only with the llama3 already on the maintainer's machine, which cannot call tools. This runs
the question that was left open, on a machine shaped like a grower's laptop (the
`local-model-check` workflow uses GitHub's 16 GB, CPU-only runner):

  1. Was the prompt read whole?  No "truncating input prompt" line in Ollama's log, and the
     input tokens Ollama reports.
  2. Did the model drive the engine?  The sizing tool was called, succeeded, and got the
     numbers the grower gave.
  3. What did it cost?  Peak memory of the Ollama processes, and the turn's wall time.

NOT HERMETIC. It needs a running Ollama with the model pulled, so it never runs in the normal
CI suite. The judging functions are pure and are unit-tested in
agronaut_agent/tests/test_local_model_check.py.

Run it:
    python -m scripts.local_model_check --model qwen3.5:4b --ollama-log ollama.log
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# One grower sentence with every number the sizing tool needs, so a correct call is checkable.
QUESTION = ("I have a 12 m2 greenhouse and want tilapia with lettuce. The water sits around "
            "27 C and I can use 3000 litres a day. Size the system for me.")
EXPECTED_ARGS = {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                 "temperature_c": 27, "water_budget_lpd": 3000}
SIZING_TOOLS = {"size_aquaponics_system", "size_hydroponic_system_tool"}


# --- pure judging (no model, no network: unit-testable) ------------------------------------

def truncation_events(log_text: str) -> list[str]:
    """Every line where Ollama cut a prompt to fit its window."""
    return [line.strip() for line in (log_text or "").splitlines()
            if "truncating input prompt" in line]


def args_match(args: dict, expected: dict = EXPECTED_ARGS) -> dict[str, bool]:
    """Per field: did the model pass what the grower said? Numbers match within 1%, names
    case-insensitively, so "Tilapia" and 12.0 count."""
    out = {}
    for key, want in expected.items():
        got = args.get(key)
        if isinstance(want, (int, float)):
            try:
                out[key] = abs(float(got) - want) <= 0.01 * abs(want)
            except (TypeError, ValueError):
                out[key] = False
        else:
            out[key] = str(got or "").strip().lower() == str(want).lower()
    return out


def verdict(truncations: list[str], calls: list[dict], reply: str,
            error: str | None = None) -> dict:
    """Pass only if the prompt arrived whole AND the model drove the sizing engine with the
    grower's numbers. A fluent reply without a tool call is a fail: in this project an
    uncomputed design is the failure that matters most."""
    sizing = [c for c in calls if c["name"] in SIZING_TOOLS]
    ok_calls = [c for c in sizing if c["ok"]]
    matched = args_match(ok_calls[0]["args"]) if ok_calls else {}
    reasons = []
    if error:
        reasons.append(f"the turn failed: {error}")
    if truncations:
        reasons.append(f"Ollama truncated the prompt {len(truncations)} time(s)")
    if not sizing:
        reasons.append("the model never called the sizing tool")
    elif not ok_calls:
        reasons.append("the sizing tool was called but returned an error")
    elif not all(matched.values()):
        wrong = [k for k, v in matched.items() if not v]
        reasons.append(f"the sizing call got the wrong {', '.join(wrong)}")
    if not error and not (reply or "").strip():
        reasons.append("the reply was empty")
    return {"passed": not reasons, "reasons": reasons, "args_match": matched,
            "tools_called": [c["name"] for c in calls]}


# --- the live run --------------------------------------------------------------------------

class _PeakMemory:
    """Peak resident memory of every Ollama process (the server and its model runner)."""

    def __init__(self, every: float = 0.5):
        self.every, self.peak_mb, self._stop = every, 0.0, threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> float:
        out = subprocess.run(["ps", "-A", "-o", "rss=,command="], capture_output=True,
                             text=True, check=False).stdout
        kb = sum(int(line.split(None, 1)[0]) for line in out.splitlines()
                 if "ollama" in line and line.split(None, 1)[0].isdigit())
        return kb / 1024

    def _run(self):
        while not self._stop.is_set():
            self.peak_mb = max(self.peak_mb, self._sample())
            self._stop.wait(self.every)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


class _Recorder:
    """Stands in for a tool so the run can see the arguments the model chose."""

    def __init__(self, tool, calls: list):
        self._tool, self._calls, self.name = tool, calls, tool.name

    def invoke(self, args):
        entry = {"name": self.name, "args": dict(args or {}), "ok": False}
        self._calls.append(entry)
        result = self._tool.invoke(args)
        entry["ok"] = not str(result).startswith("TOOL_ERROR")
        return result


def _ollama_ps(host: str) -> list:
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=10) as f:
            return json.load(f).get("models", [])
    except Exception:  # noqa: BLE001 (diagnostic only)
        return []


def run(model: str, ollama_log: Path | None) -> dict:
    from agent.llm import get_chat_model
    from agronaut_agent.analytics import Analytics
    from agronaut_agent.core import AgronautAgent

    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    host = host if host.startswith("http") else "http://" + host
    work = Path(tempfile.mkdtemp())
    # The model is passed in built, which is the one path that builds no fallback: this checks
    # ONE model, and a fallback answering (or 404ing) would hide what the named model did.
    agent = AgronautAgent(chat_model=get_chat_model(provider="ollama", model=model),
                          db_path=str(work / "check.sqlite"))
    agent._analytics = Analytics(path=work / "analytics.jsonl")
    calls: list[dict] = []
    agent._tools_by_name = {n: _Recorder(t, calls) for n, t in agent._tools_by_name.items()}

    t0 = time.perf_counter()
    reply, error = "", None
    with _PeakMemory() as mem:
        try:
            reply = agent.handle_message("check", "local-model-check", QUESTION)
        except Exception as exc:  # noqa: BLE001 (a failed turn is a result; report it)
            error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
    seconds = time.perf_counter() - t0

    llm_rows = [r for r in agent._analytics.rows() if r["event"] == "llm_call"]
    log_text = ollama_log.read_text(errors="replace") if ollama_log and ollama_log.exists() else ""
    from agent.llm import ollama_num_ctx
    return {
        "model": model,
        "num_ctx_requested": ollama_num_ctx(),
        "verdict": verdict(truncation_events(log_text), calls, reply, error),
        "error": error,
        "truncation_lines": truncation_events(log_text),
        "tool_calls": calls,
        "llm_calls": [{k: r.get(k) for k in ("stage", "latency_ms", "tokens_in", "tokens_out",
                                             "model")} for r in llm_rows],
        "turn_seconds": round(seconds, 1),
        "peak_ollama_memory_mb": round(mem.peak_mb),
        "ollama_ps": _ollama_ps(host),
        "reply_preview": (reply or "")[:600],
        "log_checked": bool(log_text),
    }


def summary_markdown(report: dict) -> str:
    v = report["verdict"]
    lines = [f"## Local model check, `{report['model']}`: {'PASS' if v['passed'] else 'FAIL'}",
             ""]
    for reason in v["reasons"]:
        lines.append(f"- {reason}")
    loaded = report["ollama_ps"][0] if report["ollama_ps"] else {}
    lines += [
        "",
        "| | |",
        "|---|---|",
        f"| context requested | {report['num_ctx_requested']} |",
        f"| context Ollama loaded | {loaded.get('context_length', 'n/a')} |",
        f"| truncation warnings | {len(report['truncation_lines'])}"
        + ("" if report["log_checked"] else " (log not available)") + " |",
        f"| input tokens per call | {[c['tokens_in'] for c in report['llm_calls']]} |",
        f"| tools called | {', '.join(v['tools_called']) or 'none'} |",
        f"| sizing arguments correct | {v['args_match'] or 'n/a'} |",
        f"| turn time | {report['turn_seconds']} s |",
        f"| peak Ollama memory | {report['peak_ollama_memory_mb']} MB |",
        "",
        "Reply (first 600 characters):",
        "",
        "> " + report["reply_preview"].replace("\n", "\n> "),
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=None, help="Ollama tag (default: agent/llm.py default)")
    ap.add_argument("--ollama-log", type=Path, default=None,
                    help="the server log, to look for truncation warnings")
    ap.add_argument("--save", type=Path, default=None, help="write the full report as JSON")
    args = ap.parse_args()

    from agent.llm import DEFAULT_MODELS
    report = run(args.model or DEFAULT_MODELS["ollama"], args.ollama_log)
    text = summary_markdown(report)
    print(text)
    if args.save:
        args.save.write_text(json.dumps(report, indent=2))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
