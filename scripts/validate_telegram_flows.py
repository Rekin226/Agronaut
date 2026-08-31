"""Live acceptance test: the six-step twin conversation, through the bot's exact brain.

Runs the same AgronautAgent the Telegram adapter wraps, against the REAL configured LLM,
and checks per step that the expected tool actually fired (a recorder wraps every tool —
narration, leaked-as-text calls and fabricated results all count as misses, which is the
point). This harness caught, in one week: fabricated "[earlier result ...]" replies, tool
calls leaked as text in two dialects, promises substituting for actions, and a default
model taking 56 seconds per round-trip. Run it before claiming the twin works end to end:

    python scripts/validate_telegram_flows.py

Needs the LLM config in .env and network. NVIDIA free-tier congestion will fail some runs
— that is a finding about the deployment, not a flake in the harness. Exit 0 iff all six
steps fired their expected tool.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

logging.basicConfig(level=logging.ERROR)

from agronaut_agent import tools as T  # noqa: E402
from agronaut_agent.core import AgronautAgent  # noqa: E402

CALLS: list[str] = []
for _t in T.AGRONAUT_TOOLS:
    def _make(name, orig):
        def rec(*a, **k):
            CALLS.append(name)
            return orig(*a, **k)
        return rec
    _t.func = _make(_t.name, _t.func)

STEPS = [
    ("design_full_system",
     "I want to design a complete aquaponic system: catfish (clarias) and lettuce, 15 m2 "
     "media bed, water temperature 26 C, water budget 400 L per day, located in "
     "Bobo-Dioulasso. My power is sometimes unreliable and I am a beginner. "
     "Design the full system please.",
     ["design_full_system"]),
    ("season simulation",
     "How much will that system produce over a year here in Bobo-Dioulasso?",
     ["simulate_season"]),
    ("money",
     "What will it cost to build in Burkina Faso, and does it make money if I sell at "
     "the market? I can work about 10 hours per week on it.",
     ["business_case", "estimate_system_cost"]),
    ("profile",
     "Actually my real system is already running: 2000 L tank, 60 catfish at about "
     "200 g average weight.",
     ["update_profile"]),
    ("live log",
     "Today I measured: ammonia 0.5, nitrate 40, water temperature 27. It's under "
     "shade net.",
     ["log_my_readings"]),
    ("live forecast",
     "How is my system doing, and what will this coming week do to it?",
     ["my_system_forecast"]),
]


def main() -> int:
    brain = AgronautAgent(db_path=":memory:")
    user = "validate-flows"
    n_pass = 0
    print("===== TELEGRAM FLOW VALIDATION =====")
    for label, msg, expect in STEPS:
        before = len(CALLS)
        t0 = time.time()
        try:
            reply = brain.handle_message("cli", user, msg)
        except Exception as exc:  # noqa: BLE001 — a dead endpoint is a result to report
            print(f"[FAIL] {label:18} ERROR {exc}")
            continue
        fired = CALLS[before:]
        atts = [a.rsplit("/", 1)[-1] for a in brain.take_attachments("cli", user)]
        ok = any(e in fired for e in expect)
        n_pass += ok
        print(f"[{'PASS' if ok else 'MISS'}] {label:18} {time.time() - t0:4.0f}s "
              f"tools={fired} att={atts}", flush=True)
        print(f"        reply: {reply[:150].replace(chr(10), ' ')!r}")
    print(f"\n{n_pass}/{len(STEPS)} steps fired their expected tool")
    return 0 if n_pass == len(STEPS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
