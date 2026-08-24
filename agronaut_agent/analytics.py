"""Privacy-preserving usage analytics.

Records COUNTS and FUNNELS, never message content. By construction the only fields that can
be written are an event name, a truncated hash of the user id (so distinct users can be
counted without knowing who they are), a coarse date, and a small allowlist of non-PII
metadata (e.g. which tool was called). Free-text kwargs are silently dropped — content
cannot leak even if a caller passes it.

Local-only: appends to a JSONL file on the operator's machine; nothing is sent anywhere.
Disable entirely with AGRONAUT_ANALYTICS=off. Consistent with docs/dpg/PRIVACY.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Only these metadata keys are ever persisted alongside an event — anything else (notably
# anything that could carry message content) is dropped.
_ALLOWED_FIELDS = {"tool", "goal", "channel", "ok"}

_SIZING_TOOLS = {"size_aquaponics_system", "size_hydroponic_system_tool"}

from . import paths as _paths

_DEFAULT_PATH = _paths.data_dir() / "analytics.jsonl"


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
        for k, v in fields.items():
            if k in _ALLOWED_FIELDS:
                row[k] = v
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:  # analytics must never break a live turn
            pass

    def summarize(self) -> dict:
        events: dict[str, int] = {}
        users: set[str] = set()
        sized_users: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events[r["event"]] = events.get(r["event"], 0) + 1
                if r.get("uid"):
                    users.add(r["uid"])
                    if r["event"] == "tool_call" and r.get("tool") in _SIZING_TOOLS:
                        sized_users.add(r["uid"])
        return {
            "events": events,
            "distinct_users": len(users),
            "users_who_sized": len(sized_users),
        }


def main() -> int:  # pragma: no cover - CLI convenience
    s = Analytics().summarize()
    print(f"Distinct users: {s['distinct_users']}")
    print(f"Users who sized a system: {s['users_who_sized']}")
    print("Events:")
    for ev, n in sorted(s["events"].items(), key=lambda kv: -kv[1]):
        print(f"  {ev:16s} {n}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
