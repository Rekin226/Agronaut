"""Privacy-preserving usage analytics.

Records COUNTS and FUNNELS, never message content. By construction the only fields that can
be written are an event name, a truncated hash of the user id (so distinct users can be
counted without knowing who they are), a coarse date, and a small allowlist of non-PII
metadata (e.g. which tool was called). Free-text kwargs are silently dropped — content
cannot leak even if a caller passes it.

Every row also carries the current TURN TRACE id (see runtime.start_turn), so the rows a
single turn produced can be read back as one path through the pipeline instead of as
unrelated counters. The id is a fresh random token per turn, never derived from the user and
never reused, so it groups events without identifying anyone.

Local-only: appends to a JSONL file on the operator's machine; nothing is sent anywhere.
Disable entirely with AGRONAUT_ANALYTICS=off. Consistent with docs/dpg/PRIVACY.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import paths as _paths

# Only these metadata keys are ever persisted alongside an event — anything else (notably
# anything that could carry message content) is dropped.
#
# The retrieval fields describe the SHAPE of a retrieval, never its subject: how many passages
# came back, how far the closest one sat, how long it took, whether the relevance floor fired.
# No query text and no passage text can be recorded, because neither has a key here and unknown
# keys are dropped rather than stored. That is what keeps this consistent with docs/dpg/PRIVACY.md
# while still answering the production question the golden set cannot: is the retriever, on real
# traffic, still behaving the way it did when its threshold was calibrated?
#
# The timing and cost fields describe the SHAPE and PRICE of a turn, never its subject.
# They exist because the course-standard breakdown (system latency vs component latency,
# tokens in vs out) was previously unmeasurable here: only retrieval was timed, which is the
# fast, cheap stage. The bottleneck is the transformer, and it was invisible.
#
# `rating` is a thumbs up/down and nothing else: an integer 1 or -1, with no free-text
# comment field, so the human-feedback signal cannot become a content leak.
_ALLOWED_FIELDS = {"tool", "goal", "channel", "ok",
                   "outcome", "n_results", "k", "latency_ms", "top_score", "hybrid",
                   "filtered", "stage", "llm_ms", "llm_calls", "tokens_in", "tokens_out",
                   "tool_calls", "rating"}

_SIZING_TOOLS = {"size_aquaponics_system", "size_hydroponic_system_tool"}

_DEFAULT_PATH = _paths.data_dir() / "analytics.jsonl"


def _percentiles(values: list[int]) -> dict:
    """p50/p95/max over a list of latencies, plus the sample size.

    The sample size is returned alongside because a p95 over four turns is not a p95, and a
    reader who cannot see n will treat it as one anyway.
    """
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    xs = sorted(values)
    def _at(q: float) -> int:
        # Nearest-rank: no interpolation between two real measurements, so every number
        # printed is a latency that actually happened.
        return xs[min(int(q * len(xs)), len(xs) - 1)]
    return {"n": len(xs), "p50": _at(0.50), "p95": _at(0.95), "max": xs[-1]}


def _hash_uid(user_id: str) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]


class Analytics:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.getenv("AGRONAUT_ANALYTICS_PATH", _DEFAULT_PATH))

    @property
    def enabled(self) -> bool:
        return os.getenv("AGRONAUT_ANALYTICS", "").lower() not in {"off", "0", "false"}

    def record(self, event: str, user_id: str | None = None, **fields) -> None:
        if not self.enabled:
            return
        row = {
            "event": str(event),
            "uid": _hash_uid(user_id) if user_id is not None else None,
            "date": datetime.now(timezone.utc).date().isoformat(),  # date only — no fine tracking
        }
        # Read rather than passed: a trace that depends on every callsite remembering to
        # forward it is a trace with holes in exactly the paths nobody thought about.
        try:
            from .runtime import trace_id
            tid = trace_id()
            if tid:
                row["trace"] = tid
        except Exception:  # noqa: BLE001 — telemetry must never break a live turn
            pass
        for k, v in fields.items():
            if k in _ALLOWED_FIELDS:
                row[k] = v
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:  # analytics must never break a live turn
            pass

    def rows(self) -> list[dict]:
        """Every recorded event, oldest first. Malformed lines are skipped, never raised:
        a half-written final line (the process died mid-append) must not make the whole
        log unreadable."""
        out: list[dict] = []
        if not self.path.exists():
            return out
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def traces(self, limit: int = 20) -> list[dict]:
        """The most recent turns, each as its ordered list of events.

        This is the course's "detailed logs: trace individual prompts through your pipeline",
        with the one substitution this project requires: the prompt itself is never stored, so
        a trace shows the SHAPE of the path (which tools ran, what retrieval returned, where
        the milliseconds went) rather than the text that travelled it. That is enough to
        answer the questions traces are for, and it keeps the privacy guarantee intact.
        """
        grouped: dict[str, list[dict]] = {}
        for r in self.rows():
            tid = r.get("trace")
            if tid:
                grouped.setdefault(tid, []).append(r)
        out = []
        for tid, events in list(grouped.items())[-limit:]:
            turn = next((e for e in events if e["event"] == "turn"), {})
            out.append({
                "trace": tid,
                "date": events[0].get("date"),
                "channel": next((e.get("channel") for e in events if e.get("channel")), None),
                "latency_ms": turn.get("latency_ms"),
                "llm_ms": turn.get("llm_ms"),
                "tokens_in": turn.get("tokens_in"),
                "tokens_out": turn.get("tokens_out"),
                "events": events,
            })
        return out

    def summarize(self) -> dict:
        events: dict[str, int] = {}
        users: set[str] = set()
        sized_users: set[str] = set()
        turn_ms: list[int] = []
        llm_ms: list[int] = []
        retrieval_ms: list[int] = []
        tokens_in = tokens_out = 0
        ratings: dict[str, int] = {"up": 0, "down": 0}
        for r in self.rows():
            events[r["event"]] = events.get(r["event"], 0) + 1
            if r.get("uid"):
                users.add(r["uid"])
                if r["event"] == "tool_call" and r.get("tool") in _SIZING_TOOLS:
                    sized_users.add(r["uid"])
            if r["event"] == "turn":
                if r.get("latency_ms") is not None:
                    turn_ms.append(r["latency_ms"])
                if r.get("llm_ms") is not None:
                    llm_ms.append(r["llm_ms"])
                tokens_in += r.get("tokens_in") or 0
                tokens_out += r.get("tokens_out") or 0
            elif r["event"] == "retrieval" and r.get("latency_ms") is not None:
                retrieval_ms.append(r["latency_ms"])
            elif r["event"] == "feedback":
                ratings["up" if (r.get("rating") or 0) > 0 else "down"] += 1
        return {
            "events": events,
            "distinct_users": len(users),
            "users_who_sized": len(sized_users),
            "latency": {
                "turn": _percentiles(turn_ms),
                "llm": _percentiles(llm_ms),
                "retrieval": _percentiles(retrieval_ms),
            },
            "tokens": {"in": tokens_in, "out": tokens_out},
            "feedback": ratings,
        }


def main() -> int:  # pragma: no cover - CLI convenience
    s = Analytics().summarize()
    print(f"Distinct users: {s['distinct_users']}")
    print(f"Users who sized a system: {s['users_who_sized']}")
    fb = s["feedback"]
    if fb["up"] or fb["down"]:
        total = fb["up"] + fb["down"]
        print(f"Feedback: {fb['up']} up / {fb['down']} down ({fb['up'] / total:.0%} positive)")
    print("Latency (ms, p50/p95/max over n):")
    for stage, p in s["latency"].items():
        if p["n"]:
            print(f"  {stage:10s} {p['p50']:>6} {p['p95']:>6} {p['max']:>6}   n={p['n']}")
    tok = s["tokens"]
    if tok["in"] or tok["out"]:
        print(f"Tokens: {tok['in']} in / {tok['out']} out")
    print("Events:")
    for ev, n in sorted(s["events"].items(), key=lambda kv: -kv[1]):
        print(f"  {ev:16s} {n}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
