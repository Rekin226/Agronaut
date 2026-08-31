"""Re-measure the tunable retrieval constants against the CURRENT corpus.

Every number in `docs/dpg/retrieval_eval/techniques.json` is a property of the corpus it was
measured on. That has now bitten twice: hybrid search lost at 362 chunks and won at 1354, and
the floor/cap values calibrated at 1354 chunks were invalidated the next day when a 2576-chunk
book joined and nobody re-ran anything. The lesson is not "be more careful" — it is that
re-measuring has to be one command, or it does not happen.

Three constants, each swept independently:

    --floor  AGRONAUT_RELEVANCE_MAX_DISTANCE   how far a passage may sit and still be used
    --cap    AGRONAUT_MAX_PER_SOURCE           passages allowed from any one source
    --beta   AGRONAUT_HYBRID_BETA              weight on semantic vs keyword ranking

SWEPT IN THAT ORDER, NOT AS A GRID. A full grid is 100+ variants and roughly an hour, and the
three constants are close to separable: the floor decides WHETHER the corpus may answer, the cap
decides WHO fills the slots among passages that already cleared it, and beta only reorders. So
each stage fixes the winner of the previous one, and `--verify` re-runs the final combination
end to end. Where that assumption is weakest — a much tighter floor shrinks the pool the cap then
draws from — the cap stage is run under the NEW floor, which is exactly where the interaction
lives.

The floor is judged on different evidence from the rest. Its job is refusal, so it is scored on
the negative controls (off-topic queries it rejects) against the constraint that must never
break: zero real queries silenced. A floor that scores well on recall by admitting everything has
not done its job.

Run it:
    python -m scripts.retrieval_sweep --floor 1.30 1.40 1.50 1.65
    python -m scripts.retrieval_sweep --all --save sweep_2026_09.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import retrieval_eval  # noqa: E402

_OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "dpg" / "retrieval_eval"

# Defaults chosen around the values that ship today, plus the region the separation report
# points at. Overridable per run.
_FLOORS = [1.30, 1.35, 1.40, 1.45, 1.50, 1.65]
_CAPS = [1, 2, 3, 0]           # 0 = no cap
_BETAS = [0.50, 0.70, 0.90, 1.00]

_METRICS = ["hit_rate", "recall@k", "precision@k", "MRR", "MAP@k"]

# Minimum distance a floor must sit ABOVE the worst real query before it is considered shippable.
#
# Not invented here: it is the rule this project already applied and wrote down. The original
# calibration chose 1.65 over a tighter 1.58 explicitly because 1.58 "leaves only 0.032 of
# headroom above the worst real query, versus 0.10 at 1.65", and judged that "a threefold cut in
# safety margin for one extra rejection" was a bad trade. 0.10 is the value that reasoning
# accepted, so it is the constraint, and the picker enforces it instead of leaving it to whoever
# reads the table.
#
# The constraint exists because "silences 0 of 33 real queries" is measured on 33 queries. The
# 34th is the one that matters, and headroom is the only thing standing between it and a refusal.
_MIN_HEADROOM = 0.10

# Smallest difference in a rate metric that is allowed to DECIDE anything.
#
# The golden set has 33 queries, so one query flipping moves hit_rate by 0.030. A MAP gap of
# 0.002 is a fifteenth of that: it is sampling noise wearing a decimal point. Without this, the
# picker chose beta=1.0 (dense-only, hybrid off) over beta=0.90 on a 0.002 MAP win while
# hit_rate fell 0.031 and recall 0.030 the other way — turning a whole retrieval technique off
# on the strength of a rounding difference.
#
# Ties inside this band are broken on hit_rate, then recall: "did the operator get a relevant
# document at all" outranks "how well was it ranked" when the ranking difference is not real.
_NOISE = 0.01


def _score(floor=None, cap=None, beta=None) -> dict:
    """Score one configuration. Env vars are the seam because that is how the shipped code
    reads these constants — a sweep that bypassed them could pass while the real path fails."""
    from agronaut_agent import rag

    env = {}
    if floor is not None:
        env["AGRONAUT_RELEVANCE_MAX_DISTANCE"] = str(floor)
    if cap is not None:
        env["AGRONAUT_MAX_PER_SOURCE"] = str(cap)
    if beta is not None:
        env["AGRONAUT_HYBRID_BETA"] = str(beta)
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        inf = float("inf")
        report = retrieval_eval.run(
            retrieve=rag.retrieve,
            # No floor and no cap: the band edges must not move as the swept constants move,
            # or headroom is not comparable between rows.
            unfiltered=lambda q, kk: rag.retrieve(q, kk, max_dist=inf, max_per_source=0))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Carry the two band edges alongside the metrics. They are measured UNFILTERED, so they do
    # not move as the floor moves — which is what makes headroom comparable across variants.
    s = dict(report["summary"])
    on = [r["raw_top_score"] for r in report["per_query"] if r.get("raw_top_score") is not None]
    off = [n["top_score"] for n in report["negative_controls"] if n["top_score"] is not None]
    s["worst_on_topic"] = max(on) if on else None
    s["best_off_topic"] = min(off) if off else None
    return s


def _row(label: str, s: dict, headroom=None) -> str:
    line = (f"  {label:<7}"
            + "".join(f"{s[m]:>8.3f}" for m in _METRICS)
            + f"{s['floor_silenced_on_topic']:>9d}"
            + f"{s['floor_rejected_off_topic']:>8d}/{s['negative_controls']}")
    if headroom is not None:
        line += f"{headroom:>10.3f}"
    return line


def _header(with_headroom: bool = False) -> str:
    h = ("  " + f"{'value':<7}" + "".join(f"{m:>8}" for m in _METRICS)
         + f"{'silenced':>9}" + f"{'rejected':>10}")
    return h + f"{'headroom':>10}" if with_headroom else h


def sweep(kind: str, values: list, fixed: dict) -> list[dict]:
    is_floor = kind == "floor"
    print(f"\n{'=' * 88}\n{kind.upper()} sweep   (holding {fixed or 'shipped defaults'})\n{'=' * 88}")
    print(_header(with_headroom=is_floor))
    out = []
    for v in values:
        s = _score(**{**fixed, kind: v})
        row = {"value": v, **{m: round(s[m], 4) for m in _METRICS},
               "silenced_on_topic": s["floor_silenced_on_topic"],
               "rejected_off_topic": s["floor_rejected_off_topic"],
               "negative_controls": s["negative_controls"],
               "latency_ms": round(s["latency_ms_mean"])}
        headroom = None
        if is_floor and s["worst_on_topic"] is not None:
            headroom = round(v - s["worst_on_topic"], 4)
            row["worst_on_topic"] = round(s["worst_on_topic"], 4)
            row["headroom"] = headroom
        out.append(row)
        label = "none" if (kind == "cap" and v == 0) else str(v)
        print(_row(label, s, headroom))
    return out


def pick_floor(rows: list[dict]) -> dict | None:
    """Best floor under TWO hard constraints, then most off-topic rejections.

    Constraint 1 — silences zero real queries. Not a metric to trade against: an off-topic
    question that gets an honest "no matching passages" has been served correctly, while a real
    question refused service has not.

    Constraint 2 — at least `_MIN_HEADROOM` above the worst real query. This is the constraint a
    naive picker misses and it changes the answer. Maximising rejections alone selects the
    tightest floor that happens to clear the 33 queries in the golden set, which on this corpus
    means sitting 0.017 away from a real query — a value that passes the sample and would refuse
    the next operator whose phrasing lands slightly further out.

    Ties break toward MORE headroom, never toward tighter: when two floors reject the same number
    of controls with the same hit rate, the looser one is strictly safer and costs nothing.
    """
    viable = [r for r in rows if r["silenced_on_topic"] == 0
              and r.get("headroom", 0) >= _MIN_HEADROOM]
    if not viable:
        return None
    best_reject = max(r["rejected_off_topic"] for r in viable)
    front = [r for r in viable if r["rejected_off_topic"] == best_reject]
    best_hit = max(r["hit_rate"] for r in front)
    front = [r for r in front if r["hit_rate"] >= best_hit - 0.001]
    return max(front, key=lambda r: r.get("headroom", 0.0))


def pick_by(rows: list[dict], metric: str) -> dict:
    """Best row by `metric`, treating differences under `_NOISE` as no difference.

    Rows within the noise band of the leader are re-ranked on hit_rate, then recall. A strict
    argmax over a 33-query sample will happily hand a decision to the third decimal place, and
    on this corpus it did.
    """
    best = max(r[metric] for r in rows)
    front = [r for r in rows if r[metric] >= best - _NOISE]
    return max(front, key=lambda r: (r["hit_rate"], r["recall@k"], r[metric]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--floor", nargs="*", type=float, metavar="D")
    ap.add_argument("--cap", nargs="*", type=int, metavar="N")
    ap.add_argument("--beta", nargs="*", type=float, metavar="B")
    ap.add_argument("--all", action="store_true", help="sweep all three, in order")
    ap.add_argument("--save", metavar="FILE", help="write the sweep to docs/dpg/retrieval_eval/")
    args = ap.parse_args()

    if not any([args.floor is not None, args.cap is not None, args.beta is not None, args.all]):
        ap.error("nothing to sweep — pass --floor/--cap/--beta, or --all")

    result: dict = {"description": "Re-measurement of the tunable retrieval constants against "
                                   "the corpus as it stands at run time."}
    fixed: dict = {}

    if args.all or args.floor is not None:
        rows = sweep("floor", args.floor or _FLOORS, {})
        chosen = pick_floor(rows)
        result["floor"] = {"rows": rows, "chosen": chosen}
        if chosen is None:
            print(f"\n  NO VIABLE FLOOR — no candidate both silences 0 real queries and keeps "
                  f"{_MIN_HEADROOM} headroom. Keeping the shipped value and sweeping the rest "
                  f"under it.")
        else:
            print(f"\n  -> {chosen['value']}: rejects "
                  f"{chosen['rejected_off_topic']}/{chosen['negative_controls']} off-topic, "
                  f"silences 0 real, {chosen['headroom']:.3f} headroom above the worst real "
                  f"query ({chosen['worst_on_topic']:.3f}), hit {chosen['hit_rate']:.3f}")
            rejected_tighter = [r for r in rows
                                if r["silenced_on_topic"] == 0
                                and r["rejected_off_topic"] > chosen["rejected_off_topic"]]
            for r in rejected_tighter:
                print(f"     (not {r['value']}: rejects {r['rejected_off_topic']} but leaves only "
                      f"{r['headroom']:.3f} headroom, under the {_MIN_HEADROOM} minimum)")
            fixed["floor"] = chosen["value"]

    if args.all or args.cap is not None:
        rows = sweep("cap", args.cap if args.cap is not None else _CAPS, dict(fixed))
        chosen = pick_by(rows, "MAP@k")
        result["cap"] = {"rows": rows, "chosen": chosen, "held": dict(fixed)}
        print(f"\n  -> best MAP@k at cap={chosen['value'] or 'none'} "
              f"(MAP {chosen['MAP@k']:.3f}, recall {chosen['recall@k']:.3f}, "
              f"precision {chosen['precision@k']:.3f})")
        fixed["cap"] = chosen["value"]

    if args.all or args.beta is not None:
        rows = sweep("beta", args.beta or _BETAS, dict(fixed))
        chosen = pick_by(rows, "MAP@k")
        result["beta"] = {"rows": rows, "chosen": chosen, "held": dict(fixed)}
        print(f"\n  -> best MAP@k at beta={chosen['value']} (MAP {chosen['MAP@k']:.3f})")
        fixed["beta"] = chosen["value"]

    if fixed:
        print(f"\n{'=' * 78}\nVERIFY — the chosen combination, measured together\n{'=' * 78}")
        print(_header())
        s = _score(**fixed)
        print(_row("chosen", s))
        result["verified"] = {"config": fixed,
                              **{m: round(s[m], 4) for m in _METRICS},
                              "silenced_on_topic": s["floor_silenced_on_topic"],
                              "rejected_off_topic": s["floor_rejected_off_topic"],
                              "latency_ms": round(s["latency_ms_mean"])}
        print(f"\n  config: {fixed}")

    if args.save:
        out = Path(args.save)
        if not out.is_absolute() and out.parent == Path("."):
            out = _OUT_DIR / out.name
        out.write_text(json.dumps(result, indent=2))
        print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
