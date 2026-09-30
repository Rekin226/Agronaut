"""Consultation quality scorer: does Agronaut talk like a patient consultant, or like a form?

`retrieval_eval` scores what the bot finds and `faithfulness_eval` whether the answer is
grounded. Neither can see HOW it talks, which is where a beginner gives up: five questions in
one message, a wall of text on a phone, jargon, re-asking what they already said. This scorer
plays whole conversations and measures that.

A simulated user (an LLM given a persona and hidden facts) talks to the REAL agent, tools and
all, until the persona has a recommendation or the turn cap is hit. Then:

  CODE-judged, per assistant message (exact, no model):
    em_dashes          count of em dashes (house style: zero)
    words              words per message (phone-sized is the goal)
    questions          questions per message (the consultation asks ONE)
    reasked            questions about a fact the profile already held (heuristic keyword
                       match, so it undercounts rephrasings; a floor, not an exact figure)
  CODE-judged, per conversation:
    turns_to_advice    user turns before the first recommending tool ran (sizing, triage,
                       business case...), or None if it never did

  LLM-judged, per conversation, binary with named labels (see faithfulness_eval for why):
    reflected          played back what it understood before advising
    beginner_level     plain words, jargon explained
    actionable         ended with one concrete next step tied to THIS user

NOT HERMETIC. It drives the configured chat model and a judge over the network, so it is
opt-in (AGRONAUT_CONSULT_EVAL=1), never runs in CI, and never blocks a merge. The metric
functions are pure and unit-tested (agronaut_agent/tests/test_consult_eval.py).

Run it:
    AGRONAUT_CONSULT_EVAL=1 python -m scripts.consult_eval --limit 3
    AGRONAUT_CONSULT_EVAL=1 python -m scripts.consult_eval --save docs/dpg/consult_eval/baseline.json
    AGRONAUT_CONSULT_EVAL=1 python -m scripts.consult_eval --compare docs/dpg/consult_eval/baseline.json

The simulated user and judge use CONSULT_EVAL_PROVIDER / CONSULT_EVAL_MODEL when set (a
strong model is the better actor and judge), else the same provider as the agent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DIR = Path(__file__).resolve().parents[1] / "docs" / "dpg" / "consult_eval"
_SCENARIOS = _DIR / "scenarios.json"

EM_DASH = "—"
DONE = "[DONE]"

# Tools whose call means the bot moved from gathering to advising. Troubleshooting advice is
# usually cited knowledge plus a scheduled check-in rather than a sizing, so those count too.
RECOMMENDING_TOOLS = frozenset({
    "search_knowledge_base", "schedule_followup",
    "size_aquaponics_system", "size_hydroponic_system_tool", "size_mixed_bed_aquaponics",
    "optimize_fish_crop_ratio", "design_full_system", "triage_visual_symptoms",
    "estimate_system_cost", "business_case", "simulate_season", "simulate_my_system",
    "my_system_forecast", "render_design_report",
})

# Phrases that mean "I am asking for fact X". Deliberately narrow: a false re-ask would
# punish the bot for a question it needed, so a miss is the cheaper error.
_ASKS_FOR: dict[str, tuple[str, ...]] = {
    "fish_species": ("which fish", "what fish", "fish species", "what kind of fish"),
    "crop": ("what would you like to grow", "which crop", "what crop", "what do you want to grow"),
    "grow_area_m2": ("how much space", "how big", "grow area", "how many square"),
    "location": ("where are you based", "where are you located", "which town", "where do you live"),
    "water_budget_lpd": ("where will the water come from", "water budget", "run short",
                         "runs short"),
}

# Question marks in the scripts our users write: Latin, full-width (Chinese), Arabic.
_QMARK = re.compile(r"[?？؟]")


# --- pure metrics ---------------------------------------------------------------------

def count_questions(message: str) -> int:
    """Questions in one message: sentences ending in a question mark. Several marks in a
    row ("really??") count once."""
    return len(re.findall(r"[?？؟]+", message or ""))


def count_words(message: str) -> int:
    """Words, plus CJK characters counted one each (Chinese has no spaces)."""
    text = message or ""
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[^\s一-鿿]+", re.sub(r"[一-鿿]", " ", text)))
    return latin + cjk


def reasked_keys(message: str, known: dict) -> list[str]:
    """Profile keys already known that this message asks for again."""
    low = (message or "").lower()
    if not _QMARK.search(low):
        return []
    return [k for k, phrases in _ASKS_FOR.items()
            if str(known.get(k, "")).strip() and any(p in low for p in phrases)]


def message_metrics(message: str, known: dict) -> dict:
    return {
        "em_dashes": (message or "").count(EM_DASH),
        "words": count_words(message),
        "questions": count_questions(message),
        "reasked": reasked_keys(message, known),
    }


def turns_to_advice(rows: list[dict]) -> int | None:
    """User turns before the first recommending tool call, from the stored transcript
    (rows of {role, tool_name}). None when no recommending tool ever ran."""
    user_turns = 0
    for r in rows:
        if r["role"] == "user":
            user_turns += 1
        elif r["role"] == "tool" and r.get("tool_name") in RECOMMENDING_TOOLS:
            return user_turns
    return None


def parse_label(text: str, yes: str, no: str) -> bool | None:
    """A binary judge verdict. The LAST label named wins, so "not REFLECTED ... verdict:
    REFLECTED" reads as the verdict; anything else is unjudged, never silently a no."""
    found = re.findall(rf"\b({re.escape(yes)}|{re.escape(no)})\b", (text or "").upper())
    if not found:
        return None
    return found[-1] == yes


def summarize(conversations: list[dict]) -> dict:
    """Aggregate per-conversation results into the headline numbers."""
    msgs = [m for c in conversations for m in c["messages"]]
    n = len(msgs)

    def _share(pred) -> float | None:
        return round(sum(1 for m in msgs if pred(m)) / n, 3) if n else None

    def _judged(key) -> tuple[float | None, int]:
        vals = [c["judge"].get(key) for c in conversations]
        seen = [v for v in vals if v is not None]
        return (round(sum(seen) / len(seen), 3) if seen else None), len(vals) - len(seen)

    advice = [c["turns_to_advice"] for c in conversations if c["turns_to_advice"] is not None]
    words = sorted(m["words"] for m in msgs)
    out = {
        "n_conversations": len(conversations),
        "n_messages": n,
        "em_dash_messages": sum(1 for m in msgs if m["em_dashes"]),
        "share_one_question_or_less": _share(lambda m: m["questions"] <= 1),
        "share_under_60_words": _share(lambda m: m["words"] <= 60),
        "median_words": words[n // 2] if n else None,
        "reasked_total": sum(len(m["reasked"]) for m in msgs),
        "reached_advice": f"{len(advice)}/{len(conversations)}",
        "mean_turns_to_advice": round(sum(advice) / len(advice), 2) if advice else None,
    }
    for key in ("reflected", "beginner_level", "actionable"):
        out[key], out[f"{key}_unjudged"] = _judged(key)
    return out


# --- the live run ------------------------------------------------------------------------

_USER_SIM = """You are role-playing a person texting an aquaponics assistant on your phone.
Stay in character. Write ONLY your next message, short and natural, the way this person
would type it. Answer what you were asked using your facts; if asked something your facts
don't cover, answer as this person plausibly would (it's fine to say you don't know).
When the assistant has given you a concrete recommendation you can act on, reply with
exactly {done}.

WHO YOU ARE: {persona}
YOUR FACTS (reveal them only when asked, never all at once): {facts}
LANGUAGE: write in {language}.

CONVERSATION SO FAR:
{transcript}

Your next message:"""

_JUDGE = """You are auditing how a farm-advice assistant handled a conversation with a
{experience} user. Answer each question with ONE label.

1. Before giving its main advice, did the assistant play back in its own words what it
   understood about the user's situation? Label REFLECTED or NOT_REFLECTED.
2. Was the language right for this user: plain words, any technical term explained?
   Label PLAIN or JARGON.
3. Did the assistant end with one concrete next step tied to THIS user's situation (not a
   generic list)? Label ACTIONABLE or VAGUE.

CONVERSATION:
{transcript}

Reply as three lines:
1: <label>
2: <label>
3: <label>"""


def _transcript(turns: list[tuple[str, str]]) -> str:
    return "\n".join(f"{who}: {text}" for who, text in turns) or "(nothing yet)"


def judge_conversation(ask, turns, experience: str) -> dict:
    """Judge one conversation, given as (speaker, text) turns or an already-rendered
    transcript string (what a saved report holds, so it can be re-judged)."""
    transcript = turns if isinstance(turns, str) else _transcript(turns)
    raw = ask(_JUDGE.format(experience=experience, transcript=transcript))
    # Labels only, so "1: REFLECTED / 2: PLAIN / 3: VAGUE" on one line parses as well as three
    # lines do (the first version read the whole rest of the line as label 1).
    lines = {m.group(1): m.group(2)
             for m in re.finditer(r"([123])\s*\**\s*[:.)]\s*\**\s*([A-Za-z_]+)", raw or "")}
    return {
        "raw": (raw or "")[:300],
        "reflected": parse_label(lines.get("1", ""), "REFLECTED", "NOT_REFLECTED"),
        "beginner_level": parse_label(lines.get("2", ""), "PLAIN", "JARGON"),
        "actionable": parse_label(lines.get("3", ""), "ACTIONABLE", "VAGUE"),
    }


def run_scenario(scenario: dict, make_agent, simulate, ask) -> dict:
    agent = make_agent()
    channel = scenario.get("channel", "whatsapp")
    user = f"eval-{scenario['id']}"
    turns: list[tuple[str, str]] = []
    per_msg: list[dict] = []
    user_msg = scenario["opening"]
    for _ in range(int(scenario.get("max_turns", 8))):
        turns.append(("User", user_msg))
        uid = agent._conv.get_or_create_user(channel, user)
        known = dict(agent._mem.get_facts(uid))
        reply = agent.handle_message(channel, user, user_msg)
        turns.append(("Assistant", reply))
        per_msg.append({**message_metrics(reply, known), "text": reply})
        user_msg = (simulate(scenario, turns) or "").strip()
        if not user_msg or DONE in user_msg:
            break
    rows = agent._conv.recent_context_messages(uid, limit=500)
    return {
        "id": scenario["id"],
        "messages": per_msg,
        "turns_to_advice": turns_to_advice(rows),
        "judge": judge_conversation(ask, turns, scenario.get("experience", "beginner")),
        "transcript": _transcript(turns),
    }


def _live(provider=None, model=None):
    from agent.llm import get_llm
    from agronaut_agent.core import AgronautAgent

    actor = get_llm(provider=os.getenv("CONSULT_EVAL_PROVIDER") or provider,
                    model=os.getenv("CONSULT_EVAL_MODEL") or model, temperature=0.7)
    judge = get_llm(provider=os.getenv("CONSULT_EVAL_PROVIDER") or provider,
                    model=os.getenv("CONSULT_EVAL_MODEL") or model, temperature=0.0)
    tmp = Path(tempfile.mkdtemp(prefix="consult_eval_"))
    counter = iter(range(10_000))

    def make_agent():
        return AgronautAgent(db_path=tmp / f"run{next(counter)}.sqlite3")

    def simulate(scenario, turns):
        return actor.invoke(_USER_SIM.format(
            done=DONE, persona=scenario["persona"], facts=json.dumps(scenario["facts"]),
            language=scenario.get("language", "English"), transcript=_transcript(turns)))

    return make_agent, simulate, judge.invoke


def run(limit: int | None = None, only: str | None = None, backends=None) -> dict:
    scenarios = json.loads(_SCENARIOS.read_text())["scenarios"]
    if only:
        wanted = {i.strip() for i in only.split(",")}
        scenarios = [s for s in scenarios if s["id"] in wanted]
    scenarios = scenarios[:limit] if limit else scenarios
    make_agent, simulate, ask = backends or _live()
    convs = []
    for s in scenarios:
        print(f"  {s['id']} ...", file=sys.stderr, flush=True)
        convs.append(run_scenario(s, make_agent, simulate, ask))
    from agent.llm import resolve
    provider, model = resolve()
    return {"agent_model": f"{provider}/{model}", "summary": summarize(convs),
            "conversations": convs}


_HIGHER_IS_BETTER = ("share_one_question_or_less", "share_under_60_words", "reflected",
                     "beginner_level", "actionable")
_LOWER_IS_BETTER = ("em_dash_messages", "median_words", "reasked_total", "mean_turns_to_advice")


def compare(now: dict, then: dict) -> list[str]:
    lines = []
    for key in _HIGHER_IS_BETTER + _LOWER_IS_BETTER:
        a, b = then.get(key), now.get(key)
        if a is None or b is None or a == b:
            continue
        better = (b > a) if key in _HIGHER_IS_BETTER else (b < a)
        lines.append(f"  {key:28} {a} -> {b}  {'better' if better else 'WORSE'}")
    return lines or ["  no change"]


class _RestoreClimateFiles:
    """Put data/climate/ back as it was after a run.

    The agent's fetch_site_climate tool writes a weather file into the repo for every town a
    persona names, and re-fetching an existing town rewrites a tracked file. That is right for
    a real user and wrong for an evaluation, which should leave the checkout untouched."""

    def __init__(self, folder: Path = Path(__file__).resolve().parents[1] / "data" / "climate"):
        self.folder = folder

    def __enter__(self):
        self.before = {p: p.read_bytes() for p in self.folder.glob("*.json")}
        return self

    def __exit__(self, *exc):
        for p in self.folder.glob("*.json"):
            if p not in self.before:
                p.unlink()
        for p, data in self.before.items():
            if not p.exists() or p.read_bytes() != data:
                p.write_bytes(data)
        return False


def rejudge(report: dict, ask) -> dict:
    """Re-run only the judge over a saved report's transcripts, so reports produced by
    different versions of the scorer are compared under one judge."""
    experience = {s["id"]: s.get("experience", "beginner")
                  for s in json.loads(_SCENARIOS.read_text())["scenarios"]}
    for c in report["conversations"]:
        c["judge"] = judge_conversation(ask, c["transcript"], experience.get(c["id"], "beginner"))
    report["summary"] = summarize(report["conversations"])
    return report


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="run only the first N scenarios")
    ap.add_argument("--only", help="run only these scenario ids (comma-separated)")
    ap.add_argument("--save", help="write the full report to this JSON path")
    ap.add_argument("--compare", help="compare against a saved report")
    ap.add_argument("--rejudge", help="re-judge a saved report's transcripts (no agent run); "
                                      "use with --save to write the result")
    args = ap.parse_args()
    if os.getenv("AGRONAUT_CONSULT_EVAL") != "1":
        print("consult_eval talks to live models. Set AGRONAUT_CONSULT_EVAL=1 to run it.")
        return 2
    if args.rejudge:
        report = rejudge(json.loads(Path(args.rejudge).read_text()), _live()[2])
    else:
        with _RestoreClimateFiles():
            report = run(limit=args.limit, only=args.only)
    print(f"agent: {report['agent_model']}")
    for k, v in report["summary"].items():
        print(f"  {k:28} {v}")
    if args.compare:
        then = json.loads(Path(args.compare).read_text())
        print(f"\nvs {args.compare} ({then.get('agent_model')}):")
        print("\n".join(compare(report["summary"], then["summary"])))
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(f"\nsaved {args.save}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
