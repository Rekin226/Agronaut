"""Synthetic consultant dialogues for fine-tuning the local model.

The default local model (qwen3.5:4b on Ollama) follows a long prompt less faithfully than a
large hosted model: it drifts back to long replies, several questions at once, and dashes.
Fine-tuning teaches it the behaviour directly. The data comes from a TEACHER model running the
real agent, never from users' conversations (AI_TRANSPARENCY.md: no training on user data).

How one dialogue is made:
  1. A persona (from docs/dpg/consult_eval/scenarios.json, plus a seeded grid of places,
     languages, goals, experience levels and quirks) is played by a simulated user.
  2. The teacher runs INSIDE AgronautAgent, so its tool calls hit the real deterministic
     tools and the tool results in the data are genuine, not imagined.
  3. Every call the teacher makes is recorded as one training example: the exact context it
     saw (prompt, recall, history, tool results so far) and the reply it gave. The student
     learns the policy, including when to call a tool, not just the final prose.
  4. Final text targets are passed through style.polish_reply, so the student learns the
     house style directly instead of relying on the post-pass.
  5. Each final reply is checked in code as it is made (at most one question, a word cap, no
     announced action without a tool call, no figure that no tool result or user message
     gave). A failing reply is redrafted: the teacher sees its draft and a note naming what
     broke, up to --max-drafts times. Only the original context and the accepted reply are
     recorded, so the note never reaches the training data. Nothing but code judges a reply.
  6. Whole dialogues are kept only if they pass consult_eval's code metrics and no reply ran
     out of drafts; single examples are dropped when the loop had to correct them (a
     fabricated "[earlier result ...]" or an announced action with no tool call), because a
     corrected reply is not one to imitate.

Output: JSONL, one example per line, {"messages": [...], "tools": [...]} in the OpenAI chat
shape that mlx-lm and most trainers read. The system prompt and recall are merged into one
leading system message; later operator notes become user turns tagged "[operator note]",
the same fold agent.llm uses for Anthropic, because Qwen's chat template rejects a system
message anywhere but first.

Run it (a pilot first, to measure cost before a full run):
    AGRONAUT_FINETUNE_GEN=1 python -m scripts.finetune.generate_dialogues --n 20 \\
        --out data/finetune/pilot.jsonl
    # then read the printed token totals and price them before running --n 1500

The teacher is TEACHER_PROVIDER / TEACHER_MODEL, both required: it never falls back to the
configured model, because that is often Claude. Claude is refused as teacher and as simulated
user, since Anthropic's Usage Policy forbids training a model on its outputs without prior
authorization. Check any other teacher's terms on the same point before a full run.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import consult_eval as ce  # noqa: E402

_OPERATOR = "[operator note] "

# --- persona grid ------------------------------------------------------------------------

_PLACES = {
    "English": ["Ouagadougou", "Bobo-Dioulasso", "Koudougou", "Accra", "Kumasi", "Tamale",
                "Kano", "Niamey", "Taichung", "Kaohsiung", "Tainan", "Manila"],
    "French": ["Ouagadougou", "Bobo-Dioulasso", "Banfora", "Dédougou", "Bamako", "Niamey",
               "Lomé", "Cotonou", "Dakar", "Abidjan"],
    "Traditional Chinese": ["台中", "高雄", "台南", "屏東", "桃園", "宜蘭"],
}
_GOALS = [
    ("start a new system", "design"),
    ("grow food for the family", "design"),
    ("earn money selling fish and vegetables", "design"),
    ("grow vegetables without fish", "design"),
    ("get more out of an existing system", "optimize"),
    ("fix a problem in a running system", "troubleshoot"),
    ("know if it can make money before investing", "design"),
]
_PROBLEMS = [
    "fish gasping at the surface in the morning", "yellow leaves on older plant leaves",
    "white spots on the fish", "cloudy, smelly water", "plants growing very slowly",
    "fish not eating since the nights got cold", "green water in the fish tank",
    "a few fish dying every day", "high ammonia on the test kit", "roots turning brown",
]
_SPACES = ["a balcony", "a small courtyard", "a yard about 5 by 6 metres",
           "a corner of a rooftop", "some land behind the house", "about 100 m2 of land",
           "a greenhouse of about 50 m2"]
_WATER = ["tap water, never short", "a borehole that runs low in the dry season",
          "a well", "rainwater tanks plus tap", "don't know"]
_QUIRKS = [
    "answers briefly and says 'I don't know' to anything technical",
    "gives a lot of detail at once",
    "is impatient and wants numbers quickly",
    "asks follow-up questions about cost",
    "is worried about doing something wrong",
    "writes short phone messages with typos",
]
_EXPERIENCE = ["beginner"] * 6 + ["intermediate"] * 3 + ["expert"]


def persona_grid(n: int, seed: int = 7) -> list[dict]:
    """`n` varied, reproducible personas in the consult_eval scenario shape."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        language = rng.choice(["English"] * 5 + ["French"] * 3 + ["Traditional Chinese"] * 2)
        place = rng.choice(_PLACES[language])
        wish, goal = rng.choice(_GOALS)
        experience = rng.choice(_EXPERIENCE)
        facts = {"location": place, "space": rng.choice(_SPACES), "water": rng.choice(_WATER)}
        opening = {"design": "I want to " + wish, "optimize": "I want to " + wish,
                   "troubleshoot": ""}[goal]
        if goal == "troubleshoot":
            problem = rng.choice(_PROBLEMS)
            facts["problem"] = problem
            opening = problem
        out.append({
            "id": f"grid-{i:04d}",
            "channel": rng.choice(["whatsapp", "telegram"]),
            "language": language,
            "experience": experience,
            "max_turns": 8,
            "opening": opening,
            "persona": (f"{'An' if experience[0] in 'aeiou' else 'A'} {experience} in {place} who wants to {wish}. This person "
                        f"{rng.choice(_QUIRKS)}, and writes the opening in their own words."),
            "facts": facts,
        })
    return out


# --- recording ---------------------------------------------------------------------------

_TRANSIENT = ("500", "502", "503", "504", "429", "overloaded", "internal server error",
              "rate limit", "temporarily", "timed out")


def with_retries(call, attempts: int = 5, base_delay: float = 5.0, sleep=None):
    """Run `call()`, retrying transient provider errors (free tiers answer 503 or 429 when
    busy) with exponential backoff. Anything else is raised at once."""
    import time
    sleep = sleep or time.sleep
    for n in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if n == attempts - 1 or not any(t in str(exc).lower() for t in _TRANSIENT):
                raise
            sleep(min(120.0, base_delay * 2 ** n))


class RecordingChat:
    """Wraps a chat model; every invoke(messages) -> reply is kept for training.

    `invoke_kwargs` ride along on every call (e.g. switching a reasoning model's thinking
    off), and transient provider errors are retried rather than losing the dialogue.

    With a `check` (messages, reply) -> problems, a reply with problems is redrafted up to
    `max_drafts` times in all: the teacher gets its draft back with a note listing the
    problems. The recorded example is still (original messages, accepted reply). Discarded
    drafts are kept in `discarded` for token counts, and a reply still failing on the last
    draft is noted in `failures` as {"call": its index in `calls`, "problems": [...]}."""

    def __init__(self, inner, calls: list | None = None, invoke_kwargs: dict | None = None,
                 check=None, max_drafts: int = 1, discarded: list | None = None,
                 failures: list | None = None):
        self._inner = inner
        self.calls = calls if calls is not None else []
        self._kw = invoke_kwargs or {}
        self._check = check
        self._max_drafts = max(1, max_drafts)
        self.discarded = discarded if discarded is not None else []
        self.failures = failures if failures is not None else []

    def bind_tools(self, tools):
        return RecordingChat(self._inner.bind_tools(tools), self.calls, self._kw, self._check,
                             self._max_drafts, self.discarded, self.failures)

    def invoke(self, messages, *args, **kwargs):
        from langchain_core.messages import HumanMessage

        def call(msgs):
            return with_retries(lambda: self._inner.invoke(msgs, *args, **{**self._kw, **kwargs}))

        reply = call(messages)
        problems = self._check(messages, reply) if self._check else []
        for _ in range(self._max_drafts - 1):
            if not problems:
                break
            self.discarded.append(reply)
            reply = call(list(messages) + [reply, HumanMessage(content=revision_note(problems))])
            problems = self._check(messages, reply)
        if problems:
            self.failures.append({"call": len(self.calls), "problems": problems})
        self.calls.append((list(messages), reply))
        return reply


def revision_note(problems: list[str]) -> str:
    return (_OPERATOR + "That draft was not sent. Rewrite it for the same user, in the same "
            "language. What to fix:\n- " + "\n- ".join(problems) + "\nReply with the "
            "rewritten message only, or call a tool if the answer needs one.")


# Announced actions in the other languages the personas write (core._PROMISE covers English).
_PROMISE_MORE = re.compile(r"\b(je vais|laisse[- ]moi|je m'en occupe)\b|稍等|正在(計算|计算|查|幫|帮)|"
                           r"我(來|来)(幫你|帮你)?(算|查|計算|计算)", re.I)
# A number standing on its own: not part of a word or code (CO2, A1, m2, qwen3).
_FIGURE = re.compile(rf"(?<![A-Za-z0-9_.])({ce._NUM})")
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)、]\s")
_UNIT_AFTER = re.compile(r"\s?(?:[A-Za-z%°²³/$€]{1,5}|[一-鿿]{1,2})")


def strict_untraced(text: str, sources: list[float], tol: float = 0.02) -> list[str]:
    """Every figure in `text` that matches no source number within `tol`, whatever its unit.

    `tol` is 2%, not consult_eval's 5%: in pilot 2 a reply reported a "loss of 3,900 to 2,800"
    that the arithmetic does not give, and 2,800 passed because it sits within 5% of a tool's
    2,700. Honest rounding ("574" written "about 570") is well inside 2%.

    Stricter than the runtime grounding check, which only reads physical units: training data
    must not teach the student to quote prices, yields, counts or months that nothing gave.
    List markers and the bare integers 0 to 3 ("2 options") are not figures; "2 cm" is."""
    out = []
    body = _LIST_MARKER.sub(" ", text or "")
    for m in _FIGURE.finditer(body):
        v = ce._to_float(m.group(1))
        if v is None:
            continue
        # a small bare count is wording; with a unit after it ("2 cm") it is a figure, and
        # consult_eval's dialogue check would reject it after the teacher had no chance
        if v == int(v) and 0 <= v <= 3 and not ce._QUANTITY.match(body, m.start()):
            continue
        if not any(abs(v - src) <= max(tol * max(abs(src), abs(v)), 0.051) for src in sources):
            unit = _UNIT_AFTER.match(body, m.end())
            out.append(m.group(1) + (unit.group(0) if unit else ""))
    return out


def question_sentences(text: str) -> list[str]:
    """The pieces of `text` that end in a question mark, trimmed to their own sentence."""
    out = []
    for m in re.finditer(r"[^?？؟\n]*[?？؟]+", text or ""):
        piece = re.split(r"(?<=[.!。！:：])\s*", m.group(0).strip())[-1].strip()
        if piece:
            out.append(piece[:120])
    return out


def turn_problems(messages, reply, max_words: int = 100) -> list[str]:
    """What is wrong with one final reply, in words the teacher can act on; [] when fine.
    Tool calls pass: the loop runs them and the reply that follows is checked in its turn.
    Sources are what core._check_grounding reads: every message but the system prompt and
    the model's own."""
    from langchain_core.messages import AIMessage

    if getattr(reply, "tool_calls", None):
        return []
    text = _text(getattr(reply, "content", ""))
    if not text.strip():
        return ["it was empty"]
    out = []
    q = ce.count_questions(text)
    if q > 1:
        # Most "two questions" are one question plus its answer choices, a rhetorical "The
        # hard truth?", or a closing "Want me to...?" offer. A generic note did not move the
        # teacher, so the note quotes its own question sentences back to it.
        quoted = " ".join(f"\u00ab{x}\u00bb" for x in question_sentences(text)[:4])
        out.append(f"it has {q} question marks: {quoted}. Keep exactly ONE question mark, "
                   "at the end of the one question that matters now. Turn the others into "
                   "statements or drop them: a rhetorical \"The hard truth?\" becomes \"The "
                   "hard truth is that...\", examples become part of the question "
                   "(\"Which would you like to grow, leafy greens like lettuce or fruiting "
                   "crops like tomatoes?\"), and drop a closing offer such as \"Want me "
                   "to...?\" when you already asked something")
    w = ce.count_words(text)
    if w > max_words:
        out.append(f"it is {w} words, the limit is {max_words}. Keep only what the user "
                   "needs right now, at most 3 short points, no tables, no headings, and "
                   "leave the rest for a later message")
    if is_corrected(reply) or _PROMISE_MORE.search(text):
        out.append("it says you will do something (\"let me calculate\", \"je vais\", "
                   "\"稍等\") without doing it. Call the tool in this turn, or give the "
                   "answer without announcing it")
    sources = [n for m in messages[1:] if not isinstance(m, AIMessage)
               for n in ce.numbers_in(_text(m.content))]
    figures = strict_untraced(text, sources)
    if figures:
        out.append("it states figures that no tool result or user message gave ("
                   + ", ".join(figures[:6]) + "). Quote a figure only exactly as a tool "
                   "result or the user gave it; otherwise call the tool that computes it, or "
                   "leave the figure out")
    return out


def to_chat_messages(messages) -> list[dict]:
    """LangChain messages -> OpenAI-shaped dicts with ONE leading system message."""
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    out: list[dict] = []
    lead: list[str] = []
    started = False
    for m in messages:
        if isinstance(m, SystemMessage):
            if not started:
                lead.append(m.content)
            else:
                out.append({"role": "user", "content": _OPERATOR + m.content})
            continue
        started = True
        if isinstance(m, AIMessage):
            msg = {"role": "assistant", "content": _text(m.content)}
            if m.tool_calls:
                msg["tool_calls"] = [{"id": c.get("id") or f"call_{i}", "type": "function",
                                      "function": {"name": c["name"],
                                                   "arguments": json.dumps(c["args"],
                                                                           ensure_ascii=False)}}
                                     for i, c in enumerate(m.tool_calls)]
            out.append(msg)
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": str(m.content)})
        else:
            out.append({"role": "user", "content": _text(m.content)})
    return ([{"role": "system", "content": "\n\n".join(lead)}] if lead else []) + out


def _text(content) -> str:
    """Anthropic returns a list of typed blocks; keep the text ones."""
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


def is_corrected(reply) -> bool:
    """Replies the agent loop would have corrected rather than delivered."""
    from agronaut_agent.core import _PROMISE

    text = _text(getattr(reply, "content", ""))
    if getattr(reply, "tool_calls", None):
        return False
    return "[earlier result" in text.lower() or bool(_PROMISE.search(text))


def examples_from_calls(calls: list, channel: str, tools: list[dict]) -> list[dict]:
    """One training example per recorded call, polished and filtered."""
    from agronaut_agent.style import polish_reply

    examples = []
    for messages, reply in calls:
        if is_corrected(reply):
            continue
        target = to_chat_messages([reply])[0]
        if not target.get("tool_calls"):
            target["content"] = polish_reply(target["content"], channel)
            if not target["content"]:
                continue
        examples.append({"messages": to_chat_messages(messages) + [target], "tools": tools})
    return examples


def rejection_reasons(conv: dict, max_median_words: int = 80,
                      min_one_question_share: float = 0.9, min_replies: int = 2) -> list[str]:
    """Why a dialogue fails consult_eval's code metrics; empty when it passes. A dialogue
    shorter than `min_replies` teaches nothing about running a consultation."""
    msgs = conv["messages"]
    if len(msgs) < min_replies:
        return [f"only {len(msgs)} assistant replies"]
    words = sorted(m["words"] for m in msgs)
    one_q = sum(1 for m in msgs if m["questions"] <= 1) / len(msgs)
    out = []
    if any(m["em_dashes"] for m in msgs):
        out.append("em dashes")
    if words[len(words) // 2] > max_median_words:
        out.append(f"median {words[len(words) // 2]} words")
    if one_q < min_one_question_share:
        out.append(f"{one_q:.0%} of replies ask at most one question")
    if any(m["reasked"] for m in msgs):
        out.append("re-asked a known fact")
    if any(m.get("untraced") for m in msgs):
        out.append("untraced numbers: " + ", ".join(u for m in msgs for u in m["untraced"])[:80])
    if conv.get("failed_turns"):
        out.append(f"{conv['failed_turns']} replies still failing after the last draft")
    return out


def dialogue_passes(conv: dict, **kw) -> bool:
    return not rejection_reasons(conv, **kw)


def salvage_cut(n_calls: int, failures: list[dict], reasons: list[str]) -> int:
    """How many of a dialogue's recorded calls may become training examples.

    All of them when it passed. When it failed only because some reply ran out of drafts,
    the calls before the first such reply: each passed the per-reply checks and saw only
    accepted replies in its context, so they are as clean as a kept dialogue's. A median or
    question share over the limit is a whole-dialogue average that every single reply here
    already met, so it costs nothing. A re-asked fact, an untraced figure the per-reply
    check did not catch, or a dialogue too short to teach a consultation cannot be pinned to
    one reply, so those keep nothing."""
    if not reasons:
        return n_calls
    if any(r.startswith(("re-asked", "untraced", "only ")) for r in reasons):
        return 0
    return failures[0]["call"] if failures else n_calls


# --- the run -----------------------------------------------------------------------------

def remaining(personas: list[dict], log_path: Path) -> list[dict]:
    """Personas not yet in a run's dialogue log, so a stopped run resumes where it left off.
    A dialogue cut off mid-way never reached the log, so it is simply run again."""
    done = set()
    if log_path.exists():
        done = {json.loads(ln)["persona"] for ln in log_path.read_text().splitlines()
                if ln.strip()}
    return [p for p in personas if p["id"] not in done]


def tool_schemas() -> list[dict]:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from agronaut_agent.tools import AGRONAUT_TOOLS
    return [convert_to_openai_tool(t) for t in AGRONAUT_TOOLS]


def resolve_teacher(provider: str | None, model: str | None) -> tuple[str, str]:
    """The teacher must be named explicitly and must not be Claude. Every simulated user turn
    and every teacher reply ends up in the training data, and Anthropic's Usage Policy forbids
    using its outputs to train a model without prior authorization. Claude still runs the bot
    for users and may run evaluations; it never writes training data."""
    if not provider or not model:
        raise SystemExit("Set TEACHER_PROVIDER and TEACHER_MODEL. The teacher never falls back "
                         "to LLM_PROVIDER, which is often Claude.")
    provider = provider.strip().lower()
    base_url = os.getenv("OPENAI_COMPAT_BASE_URL", "") if provider == "openai_compat" else ""
    if provider == "anthropic" or "claude" in model.lower() or "anthropic.com" in base_url:
        raise SystemExit("Claude cannot be the teacher or the simulated user: Anthropic's "
                         "Usage Policy forbids training a model on its outputs without "
                         "prior authorization. Use an open-weights teacher.")
    return provider, model


def pin_models_to_teacher(provider: str, model: str) -> None:
    """Point every model call in this process at the teacher. The agent makes side calls with
    the configured model (the rolling memory summary, recalled into later prompts), and on a
    machine configured for Claude those would put Claude's text into the training data."""
    os.environ["LLM_PROVIDER"] = provider
    os.environ["LLM_MODEL"] = model


def teacher_invoke_kwargs(provider: str | None) -> dict:
    """Per-call options for the teacher. NVIDIA's Nemotron 3 models reason before answering
    unless told not to; the reasoning is slow, costs tokens, and must never become a
    training target. TEACHER_THINKING=on keeps it."""
    if (provider or "").lower() == "nvidia" and os.getenv("TEACHER_THINKING", "off") != "on":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def generate(personas: list[dict], teacher_factory, simulate, out_path: Path,
             invoke_kwargs: dict | None = None, check=None, max_drafts: int = 1) -> dict:
    from agronaut_agent.core import AgronautAgent

    tools = tool_schemas()
    tmp = Path(tempfile.mkdtemp(prefix="finetune_gen_"))
    stats = {"dialogues": 0, "kept": 0, "examples": 0, "tokens_in": 0, "tokens_out": 0,
             "replies": 0, "redrafted": 0}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = out_path.with_suffix(".dialogues.jsonl")
    with out_path.open("a", encoding="utf-8") as fh, log_path.open("a", encoding="utf-8") as log:
        for i, p in enumerate(personas):
            print(f"  {p['id']} ({i + 1}/{len(personas)})", file=sys.stderr, flush=True)
            rec = RecordingChat(teacher_factory(), invoke_kwargs=invoke_kwargs, check=check,
                                max_drafts=max_drafts)
            agent_factory = lambda: AgronautAgent(db_path=tmp / f"{p['id']}.sqlite3",  # noqa: E731
                                                  chat_model=rec)
            try:
                conv = ce.run_scenario(p, agent_factory, simulate, ask=lambda _p: "")
            except Exception as exc:  # noqa: BLE001 — one failed dialogue must not end the run
                print(f"    failed: {exc}", file=sys.stderr)
                continue
            stats["dialogues"] += 1
            for reply in [r for _, r in rec.calls] + rec.discarded:
                usage = getattr(reply, "usage_metadata", None) or {}
                stats["tokens_in"] += usage.get("input_tokens", 0)
                stats["tokens_out"] += usage.get("output_tokens", 0)
            stats["replies"] += len(rec.calls)
            stats["redrafted"] += len(rec.discarded)
            conv["failed_turns"] = len(rec.failures)
            reasons = rejection_reasons(conv)
            cut = salvage_cut(len(rec.calls), rec.failures, reasons)
            examples = examples_from_calls(rec.calls[:cut], p.get("channel", "whatsapp"), tools)
            log.write(json.dumps({"persona": p["id"], "kept": not reasons, "reasons": reasons,
                                  "examples": len(examples), "of_calls": len(rec.calls),
                                  "turn_failures": [f["problems"] for f in rec.failures],
                                  "turns_to_advice": conv["turns_to_advice"],
                                  "transcript": conv["transcript"]}, ensure_ascii=False) + "\n")
            log.flush()
            if reasons:
                stats.setdefault("rejected_for", {})
                for r in reasons:
                    key = r.split(":")[0].split(" ", 1)[-1] if r[0].isdigit() else r.split(":")[0]
                    stats["rejected_for"][key] = stats["rejected_for"].get(key, 0) + 1
                if examples:
                    stats["salvaged"] = stats.get("salvaged", 0) + 1
            else:
                stats["kept"] += 1
            for ex in examples:
                ex["persona"] = p["id"]
                fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
                stats["examples"] += 1
            fh.flush()                 # a crash late in a long run keeps what came before
    return stats


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=20, help="grid personas to generate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--with-scenarios", action="store_true",
                    help="also run the 20 consult_eval scenarios (they are the eval set, so "
                         "leave this off when the tuned model will be scored on them)")
    ap.add_argument("--out", default="data/finetune/dialogues.jsonl")
    ap.add_argument("--price-in", type=float, help="teacher price per million input tokens")
    ap.add_argument("--price-out", type=float, help="teacher price per million output tokens")
    ap.add_argument("--max-drafts", type=int, default=4,
                    help="drafts per reply before it counts as failed (1 turns redrafting off)")
    ap.add_argument("--max-words", type=int, default=100, help="word cap for one reply")
    ap.add_argument("--resume", action="store_true",
                    help="skip personas already in <out>.dialogues.jsonl and append the rest; "
                         "use the same --n and --seed as the run being resumed")
    args = ap.parse_args()
    if os.getenv("AGRONAUT_FINETUNE_GEN") != "1":
        print("This calls a paid teacher model many times. Set AGRONAUT_FINETUNE_GEN=1 to run.")
        return 2

    from agent.llm import get_chat_model

    provider, model = resolve_teacher(os.getenv("TEACHER_PROVIDER"), os.getenv("TEACHER_MODEL"))
    pin_models_to_teacher(provider, model)
    kw = teacher_invoke_kwargs(provider)
    actor = get_chat_model(provider, model, temperature=0.8)

    def simulate(scenario, turns):
        prompt = ce._USER_SIM.format(
            done=ce.DONE, persona=scenario["persona"], facts=json.dumps(scenario["facts"]),
            language=scenario.get("language", "English"), transcript=ce._transcript(turns))
        return _text(with_retries(lambda: actor.invoke(prompt, **kw)).content).strip()

    personas = persona_grid(args.n, args.seed)
    if args.resume:
        personas = remaining(personas, Path(args.out).with_suffix(".dialogues.jsonl"))
        print(f"resuming: {len(personas)} personas left", file=sys.stderr)
    if args.with_scenarios:
        personas = json.loads(ce._SCENARIOS.read_text())["scenarios"] + personas
    with ce._RestoreClimateFiles():
        stats = generate(personas, lambda: get_chat_model(provider, model), simulate,
                         Path(args.out), invoke_kwargs=kw, max_drafts=args.max_drafts,
                         check=lambda msgs, reply: turn_problems(msgs, reply, args.max_words))
    print(json.dumps(stats, indent=1))
    if stats["dialogues"]:
        per = {k: stats[k] / stats["dialogues"] for k in ("tokens_in", "tokens_out")}
        print(f"per dialogue: ~{per['tokens_in']:,.0f} tokens in, ~{per['tokens_out']:,.0f} out "
              f"(teacher only; the simulated user adds a little)")
        if args.price_in is not None and args.price_out is not None:
            cost = (per["tokens_in"] * args.price_in + per["tokens_out"] * args.price_out) / 1e6
            print(f"at the prices given: ~${cost:.3f} per dialogue, "
                  f"~${cost * 1000:,.0f} per 1,000 dialogues")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
