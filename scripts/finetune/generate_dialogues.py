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

class RecordingChat:
    """Wraps a chat model; every invoke(messages) -> reply is kept for training."""

    def __init__(self, inner, calls: list | None = None):
        self._inner = inner
        self.calls = calls if calls is not None else []

    def bind_tools(self, tools):
        return RecordingChat(self._inner.bind_tools(tools), self.calls)

    def invoke(self, messages, *args, **kwargs):
        reply = self._inner.invoke(messages, *args, **kwargs)
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


def dialogue_passes(conv: dict, max_median_words: int = 80,
                    min_one_question_share: float = 0.9) -> bool:
    """consult_eval's code metrics, as a keep/drop rule for a whole dialogue."""
    msgs = conv["messages"]
    if not msgs:
        return False
    words = sorted(m["words"] for m in msgs)
    one_q = sum(1 for m in msgs if m["questions"] <= 1) / len(msgs)
    return (all(m["em_dashes"] == 0 for m in msgs)
            and words[len(words) // 2] <= max_median_words
            and one_q >= min_one_question_share
            and not any(m["reasked"] for m in msgs))


# --- the run -----------------------------------------------------------------------------

def tool_schemas() -> list[dict]:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from agronaut_agent.tools import AGRONAUT_TOOLS
    return [convert_to_openai_tool(t) for t in AGRONAUT_TOOLS]


def generate(personas: list[dict], teacher_factory, simulate, out_path: Path) -> dict:
    from agronaut_agent.core import AgronautAgent

    tools = tool_schemas()
    tmp = Path(tempfile.mkdtemp(prefix="finetune_gen_"))
    stats = {"dialogues": 0, "kept": 0, "examples": 0, "tokens_in": 0, "tokens_out": 0}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        for i, p in enumerate(personas):
            print(f"  {p['id']} ({i + 1}/{len(personas)})", file=sys.stderr, flush=True)
            rec = RecordingChat(teacher_factory())
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
            if not dialogue_passes(conv):
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

    from agent.llm import get_chat_model, get_llm

    provider = os.getenv("TEACHER_PROVIDER")
    model = os.getenv("TEACHER_MODEL")
    actor = get_llm(provider=provider, model=model, temperature=0.8)

    def simulate(scenario, turns):
        return actor.invoke(ce._USER_SIM.format(
            done=ce.DONE, persona=scenario["persona"], facts=json.dumps(scenario["facts"]),
            language=scenario.get("language", "English"), transcript=ce._transcript(turns)))

    personas = persona_grid(args.n, args.seed)
    if args.with_scenarios:
        personas = json.loads(ce._SCENARIOS.read_text())["scenarios"] + personas
    with ce._RestoreClimateFiles():
        stats = generate(personas, lambda: get_chat_model(provider, model), simulate,
                         Path(args.out))
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
