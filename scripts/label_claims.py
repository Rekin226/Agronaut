"""Label a faithfulness report's claims by hand, so the judges can be checked against a person.

    python -m scripts.label_claims 2026-09-30_baseline.json          # ~40 claims
    python -m scripts.label_claims 2026-09-30_baseline.json --n 20   # fewer
    python -m scripts.label_claims 2026-09-30_baseline.json --review # then: second look

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
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.faithfulness_eval import _LABELS, _resolve, is_meta_claim  # noqa: E402


def sample(report: dict, n: int, done: set[str], seed: int = 7) -> list[tuple[dict, dict]]:
    """Up to n unlabelled (query row, claim) pairs, balanced across the first judge's
    SUPPORTED and UNSUPPORTED verdicts, in a fixed order for a given seed. Sentences about
    the sources themselves are not claims and are never offered."""
    pools: dict[bool | None, list] = {True: [], False: [], None: []}
    for q in report["per_query"]:
        for c in q.get("claims", []):
            if c["id"] not in done and not is_meta_claim(c["claim"]):
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


def disputed(report: dict, labels: dict[str, bool]) -> list[tuple[dict, dict, dict]]:
    """(query row, claim, {judgement: verdict}) for every labelled claim some judge ruled on
    differently. The review pass looks at these and only these."""
    from scripts.faithfulness_eval import verdicts_of

    judged = {f"{report.get('meta', {}).get('judge', 'judge')} (run 1)": verdicts_of(report)}
    judged.update({name: verdicts_of(report, name) for name in report.get("judgements", {})})
    out = []
    for q in report["per_query"]:
        for c in q.get("claims", []):
            if c["id"] not in labels or is_meta_claim(c["claim"]):
                continue
            ruled = {name: v[c["id"]] for name, v in judged.items() if c["id"] in v}
            if any(v is not None and v != labels[c["id"]] for v in ruled.values()):
                out.append((q, c, ruled))
    return out


def load_labels(path: Path = _LABELS) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"rubric": "SUPPORTED if the CONTEXT states or directly implies the claim",
            "labeller": None, "labels": {}}


def save_labels(data: dict, path: Path = _LABELS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2))


def _show(header: str, q: dict, c: dict) -> None:
    print("\n" + "=" * 78)
    print(f"{header}  QUESTION: {q['query']}\n")
    print("CONTEXT:")
    for para in q["context"].split("\n\n"):
        print(textwrap.indent(textwrap.fill(para, 76), "  "))
        print()
    print(f"CLAIM:  {c['claim']}\n")


def _ask(prompt: str, keys: set[str]) -> str:
    while True:
        a = input(prompt).strip().lower()
        if a in keys:
            return a


def short_judge(name: str) -> str:
    """'nvidia/openai/gpt-oss-20b [Reasoning: low, ...] prompt e3a1 (run 1)' ->
    'gpt-oss-20b, prompt e3a1 (run 1)': the model and run, without provider or settings."""
    name = re.sub(r"\s*\[[^\]]*\]", "", name)
    model, _, rest = name.partition(" ")
    return model.rsplit("/", 1)[-1] + (", " + rest if rest.startswith("prompt") else
                                       " " + rest if rest else "")


def _word(v) -> str:
    return {True: "SUPPORTED", False: "UNSUPPORTED"}.get(v, "unjudged")


def review(report: dict, data: dict) -> None:  # pragma: no cover - interactive
    """Second look at the claims a judge ruled on differently, with the verdicts shown.

    Not blind, on purpose: this is adjudication, done after the blind pass. The blind label
    stays in `labels`, the reviewed one goes to `reviewed`, and agreement is reported both
    ways, so a review that just deferred to the judge would show as exactly that.
    """
    items = disputed(report, data["labels"])
    data.setdefault("reviewed", {})
    print(f"{len(items)} labelled claims where a judge disagreed with you. For each, keep your "
          "label or change it.\nJudge whether the CONTEXT states or directly implies the "
          "claim; the judges can be wrong too.")
    for i, (q, c, ruled) in enumerate(items, start=1):
        if c["id"] in data["reviewed"]:
            continue
        _show(f"[review {i}/{len(items)}]", q, c)
        mine = data["labels"][c["id"]]
        print(f"  you said:  {_word(mine)}")
        for name, v in ruled.items():
            print(f"  {short_judge(name):42s} {_word(v)}")
        a = _ask(f"\n  [k] keep {_word(mine)}  [c] change to {_word(not mine)}  "
                 "[s] skip  [q] quit: ", {"k", "c", "s", "q"})
        if a == "q":
            break
        if a == "s":
            continue
        data["reviewed"][c["id"]] = mine if a == "k" else (not mine)
        save_labels(data)
    changed = sum(1 for k, v in data["reviewed"].items() if v != data["labels"].get(k))
    print(f"\n{len(data['reviewed'])} reviewed, {changed} changed. Saved to {_LABELS}")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - interactive
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--labeller", default="maintainer")
    ap.add_argument("--review", action="store_true",
                    help="after labelling: revisit only the claims a judge disagreed on")
    args = ap.parse_args(argv)

    report = json.loads(_resolve(args.report).read_text())
    data = load_labels()
    name = _resolve(args.report).name
    # Labels belong to one report: claim ids repeat across runs with different sentences, so
    # mixing reports would score people's labels against claims they never saw.
    if data.get("labels") and data.get("report") not in (None, name):
        print(f"The labels in {_LABELS.name} were made on {data['report']}, not {name}. "
              "Move that file aside to label another report.")
        return 2
    data["report"] = data.get("report") or name
    data["labeller"] = data.get("labeller") or args.labeller
    if args.review:
        review(report, data)
        return 0
    todo = sample(report, args.n - len(data["labels"]), set(data["labels"]))
    if not todo:
        print(f"All {len(data['labels'])} labels done. Next: python -m scripts.faithfulness_eval"
              f" --agreement {args.report}")
        return 0
    print(__doc__.split("\n\n")[1])
    for i, (q, c) in enumerate(todo, start=1):
        _show(f"[{len(data['labels']) + 1}/{args.n}]", q, c)
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
