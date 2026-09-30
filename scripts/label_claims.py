"""Label a faithfulness report's claims by hand, so the judges can be checked against a person.

    python -m scripts.label_claims 2026-09-30_baseline.json          # ~40 claims
    python -m scripts.label_claims 2026-09-30_baseline.json --n 20   # fewer

For each claim you see the passages the answer was written from and the claim itself, and
you answer the same question the judge was asked: does the CONTEXT state or directly imply
the claim? Whether the claim is true in the world does not matter; whether the context backs
it does. The judges' verdicts are never shown, so the labels stay independent of them.

Claims are sampled half from each side of the first judge's verdicts (when both sides have
enough), because a sample that is 95% "supported" cannot tell a good judge from one that
says "supported" to everything. Answers are saved after every claim to
docs/dpg/faithfulness_eval/human_labels.json; run it again to continue where you stopped.
Then `python -m scripts.faithfulness_eval --agreement <report>` prints agreement and kappa.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.faithfulness_eval import _LABELS, _resolve  # noqa: E402


def sample(report: dict, n: int, done: set[str], seed: int = 7) -> list[tuple[dict, dict]]:
    """Up to n unlabelled (query row, claim) pairs, balanced across the first judge's
    SUPPORTED and UNSUPPORTED verdicts, in a fixed order for a given seed."""
    pools: dict[bool | None, list] = {True: [], False: [], None: []}
    for q in report["per_query"]:
        for c in q.get("claims", []):
            if c["id"] not in done:
                pools[c["verdict"]].append((q, c))
    rng = random.Random(seed)
    for p in pools.values():
        rng.shuffle(p)
    half = n // 2
    picked = pools[False][:half]
    picked += pools[True][:n - len(picked)]
    picked += pools[False][half:half + (n - len(picked))]
    picked += pools[None][:n - len(picked)]
    rng.shuffle(picked)
    return picked


def load_labels(path: Path = _LABELS) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"rubric": "SUPPORTED if the CONTEXT states or directly implies the claim",
            "labeller": None, "labels": {}}


def save_labels(data: dict, path: Path = _LABELS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2))


def main() -> int:  # pragma: no cover - interactive
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--labeller", default="maintainer")
    args = ap.parse_args()

    report = json.loads(_resolve(args.report).read_text())
    data = load_labels()
    data["labeller"] = data.get("labeller") or args.labeller
    todo = sample(report, args.n - len(data["labels"]), set(data["labels"]))
    if not todo:
        print(f"All {len(data['labels'])} labels done. Next: python -m scripts.faithfulness_eval"
              f" --agreement {args.report}")
        return 0
    print(__doc__.split("\n\n")[1])
    for i, (q, c) in enumerate(todo, start=1):
        print("\n" + "=" * 78)
        print(f"[{len(data['labels']) + 1}/{args.n}]  QUESTION: {q['query']}\n")
        print("CONTEXT:")
        for para in q["context"].split("\n\n"):
            print(textwrap.indent(textwrap.fill(para, 76), "  "))
            print()
        print(f"CLAIM:  {c['claim']}\n")
        while True:
            a = input("  [s] supported  [u] unsupported  [k] skip  [q] quit: ").strip().lower()
            if a in {"s", "u", "k", "q"}:
                break
        if a == "q":
            break
        if a == "k":
            continue
        data["labels"][c["id"]] = (a == "s")
        save_labels(data)
    print(f"\n{len(data['labels'])} labels saved to {_LABELS}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
