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
  5. Whole dialogues are kept only if they pass consult_eval's code metrics; single examples
     are dropped when the loop had to correct them (a fabricated "[earlier result ...]" or an
     announced action with no tool call), because a corrected reply is not one to imitate.

Output: JSONL, one example per line, {"messages": [...], "tools": [...]} in the OpenAI chat
shape that mlx-lm and most trainers read. The system prompt and recall are merged into one
leading system message; later operator notes become user turns tagged "[operator note]",
the same fold agent.llm uses for Anthropic, because Qwen's chat template rejects a system
message anywhere but first.

Run it (a pilot first, to measure cost before a full run):
    AGRONAUT_FINETUNE_GEN=1 python -m scripts.finetune.generate_dialogues --n 20 \\
        --out data/finetune/pilot.jsonl
    # then read the printed token totals and price them before running --n 1500

The teacher is TEACHER_PROVIDER / TEACHER_MODEL when set, else the configured model. Check
the teacher provider's terms on using its outputs as training data before a full run.
"""

from __future__ import annotations

import argparse
import json
import os
import random
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
            "persona": (f"A {experience} in {place} who wants to {wish}. They "
                        f"{rng.choice(_QUIRKS)}. They write the opening in their own words."),
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
    off), and transient provider errors are retried rather than losing the dialogue."""

    def __init__(self, inner, calls: list | None = None, invoke_kwargs: dict | None = None):
        self._inner = inner
        self.calls = calls if calls is not None else []
        self._kw = invoke_kwargs or {}

    def bind_tools(self, tools):
        return RecordingChat(self._inner.bind_tools(tools), self.calls, self._kw)

    def invoke(self, messages, *args, **kwargs):
        reply = with_retries(lambda: self._inner.invoke(messages, *args, **{**self._kw, **kwargs}))
        self.calls.append((list(messages), reply))
        return reply


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
    return out


def dialogue_passes(conv: dict, **kw) -> bool:
    return not rejection_reasons(conv, **kw)


# --- the run -----------------------------------------------------------------------------

def tool_schemas() -> list[dict]:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from agronaut_agent.tools import AGRONAUT_TOOLS
    return [convert_to_openai_tool(t) for t in AGRONAUT_TOOLS]


def teacher_invoke_kwargs(provider: str | None) -> dict:
    """Per-call options for the teacher. NVIDIA's Nemotron 3 models reason before answering
    unless told not to; the reasoning is slow, costs tokens, and must never become a
    training target. TEACHER_THINKING=on keeps it."""
    if (provider or "").lower() == "nvidia" and os.getenv("TEACHER_THINKING", "off") != "on":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def generate(personas: list[dict], teacher_factory, simulate, out_path: Path,
             invoke_kwargs: dict | None = None) -> dict:
    from agronaut_agent.core import AgronautAgent

    tools = tool_schemas()
    tmp = Path(tempfile.mkdtemp(prefix="finetune_gen_"))
    stats = {"dialogues": 0, "kept": 0, "examples": 0, "tokens_in": 0, "tokens_out": 0}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = out_path.with_suffix(".dialogues.jsonl")
    with out_path.open("a", encoding="utf-8") as fh, log_path.open("a", encoding="utf-8") as log:
        for i, p in enumerate(personas):
            print(f"  {p['id']} ({i + 1}/{len(personas)})", file=sys.stderr, flush=True)
            rec = RecordingChat(teacher_factory(), invoke_kwargs=invoke_kwargs)
            agent_factory = lambda: AgronautAgent(db_path=tmp / f"{p['id']}.sqlite3",  # noqa: E731
                                                  chat_model=rec)
            try:
                conv = ce.run_scenario(p, agent_factory, simulate, ask=lambda _p: "")
            except Exception as exc:  # noqa: BLE001 — one failed dialogue must not end the run
                print(f"    failed: {exc}", file=sys.stderr)
                continue
            stats["dialogues"] += 1
            for _, reply in rec.calls:
                usage = getattr(reply, "usage_metadata", None) or {}
                stats["tokens_in"] += usage.get("input_tokens", 0)
                stats["tokens_out"] += usage.get("output_tokens", 0)
            reasons = rejection_reasons(conv)
            log.write(json.dumps({"persona": p["id"], "kept": not reasons, "reasons": reasons,
                                  "turns_to_advice": conv["turns_to_advice"],
                                  "transcript": conv["transcript"]}, ensure_ascii=False) + "\n")
            if reasons:
                stats.setdefault("rejected_for", {})
                for r in reasons:
                    key = r.split(":")[0].split(" ", 1)[-1] if r[0].isdigit() else r.split(":")[0]
                    stats["rejected_for"][key] = stats["rejected_for"].get(key, 0) + 1
                continue
            stats["kept"] += 1
            for ex in examples_from_calls(rec.calls, p.get("channel", "whatsapp"), tools):
                ex["persona"] = p["id"]
                fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
                stats["examples"] += 1
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
    args = ap.parse_args()
    if os.getenv("AGRONAUT_FINETUNE_GEN") != "1":
        print("This calls a paid teacher model many times. Set AGRONAUT_FINETUNE_GEN=1 to run.")
        return 2

    from agent.llm import get_chat_model

    provider = os.getenv("TEACHER_PROVIDER")
    model = os.getenv("TEACHER_MODEL")
    kw = teacher_invoke_kwargs(provider)
    actor = get_chat_model(provider, model, temperature=0.8)

    def simulate(scenario, turns):
        prompt = ce._USER_SIM.format(
            done=ce.DONE, persona=scenario["persona"], facts=json.dumps(scenario["facts"]),
            language=scenario.get("language", "English"), transcript=ce._transcript(turns))
        return _text(with_retries(lambda: actor.invoke(prompt, **kw)).content).strip()

    personas = persona_grid(args.n, args.seed)
    if args.with_scenarios:
        personas = json.loads(ce._SCENARIOS.read_text())["scenarios"] + personas
    with ce._RestoreClimateFiles():
        stats = generate(personas, lambda: get_chat_model(provider, model), simulate,
                         Path(args.out), invoke_kwargs=kw)
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
