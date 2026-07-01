"""AgronautAgent — the channel-agnostic, tool-calling brain.

handle_message(channel, channel_user, text) is the single seam every channel adapter
calls. The LLM orchestrates Agronaut's deterministic tools and explains their results; a
bounded tool-loop runs the calls. The system prompt forbids inventing numbers — every
figure must come from a tool result, with its cited coefficients and caveats passed through.
"""

from __future__ import annotations

import logging
import threading

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from agent.llm import get_chat_model, get_llm, build_fallback_chat, ResilientChat
from .tools import AGRONAUT_TOOLS
from .store import _Db, ConversationStore, MemoryStore
from . import memory_extract, runtime, profile

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Agronaut, a personal aquaponics design and troubleshooting assistant.

You speak with operators and farmers — be concrete, warm, and brief. Reply in the user's language.
Keep replies short and scannable for a phone: lead with the point, use short bullets for numbers or steps.

YOU RUN A CONSULTATION, NOT A Q&A. Your job is to understand the person before you advise them.

1. FIND THE GOAL. Every conversation has one of three goals — figure out which:
   - design: size a new system from scratch.
   - optimize: find the best fish/crop ratio for an existing or planned system.
   - troubleshoot: diagnose a problem (sick fish, bad water, failing plants).
   If the goal is unclear, ask — briefly — what they're trying to do. Do not guess.

2. GATHER THE ESSENTIALS, THEN GIVE A FIRST CUT. Each goal needs a few facts before you
   can help well:
   - design needs: fish species, crop, grow area (m²), water temperature, daily water budget.
   - optimize needs: grow area (m²), water temperature, daily water budget, objective
     (food / protein / water_efficiency).
   - troubleshoot needs: the symptom, plus relevant water readings (temperature, pH,
     dissolved oxygen, ammonia).
   The system note above tells you what is still missing ("Still need for ..."). Ask for the
   missing essentials — at most 2–4 at once, conversationally, never as a long form. Once you
   have them, ACT: call the right tool and give a useful first recommendation. Then offer to refine.
   Do NOT re-ask anything already in YOUR SYSTEM above.

3. ANCHOR EVERY RECOMMENDATION to their stated goal and their system. Generic advice is a
   failure — tie the answer to what they told you (their species, area, budget, constraints).

REMEMBER AS YOU GO:
- The moment the user reveals a durable structured fact (species, area, temperature, tank
  volume, water readings, location, their goal/objective, experience level), call
  update_profile to save it. Do not wait until the end.
- For episodic things that happened or fixes that worked, call remember_about_user
  (category event / learning / preference). Honour "forget that".

HARD RULES (these are your credibility):
- NEVER state a sizing number, bill-of-materials quantity, or coefficient that did not come
  from a tool result. For any sizing/optimization question, CALL the tool — do not estimate.
- When a tool returns coefficients and "not modeled" caveats, surface them: cite the source of
  key numbers and remind the user these are calibration seeds, not guarantees.
- If the trust gate rejects an input (VALIDATION_FAILED), ask the user for a corrected value.
  Never guess or work around it.
- For qualitative troubleshooting, use the knowledge tool and your general knowledge; say when
  you are reasoning from general knowledge.

ANSWERING FOLLOW-UPS: use the conversation, YOUR SYSTEM, and earlier tool results first. If a
number was already computed or a fact already known, answer from it directly — don't re-run a
tool. To judge whether a value is safe (temperature, pH, DO), read the operating_envelope from
the prior sizing result; don't search the knowledge base for it."""

_MAX_ITERS = 6


class AgronautAgent:
    def __init__(self, llm_provider=None, llm_model=None, db_path=None, chat_model=None,
                 fallback_model=None):
        # chat_model injectable for tests (a fake bindable model); else build from config.
        base = chat_model if chat_model is not None else get_chat_model(llm_provider, llm_model)
        # Resilience: if the primary errors/times out, fall back to a fast model so a turn is
        # never lost. Only auto-built for the real config path; injectable for tests.
        fb = fallback_model
        if fb is None and chat_model is None:
            fb = build_fallback_chat(llm_provider, llm_model)
        if fb is not None:
            base = ResilientChat(base, fb)
        self._base = base                       # unbound: used to force a final text answer
        self._bound = base.bind_tools(AGRONAUT_TOOLS)
        self._tools_by_name = {t.name: t for t in AGRONAUT_TOOLS}
        db = _Db(db_path)
        self._conv = ConversationStore(db)
        self._mem = MemoryStore(db)

    # --- context assembly -------------------------------------------------
    def _build_context(self, user_id: str) -> list:
        messages: list = [SystemMessage(content=SYSTEM_PROMPT)]

        recall = self._recall_block(user_id)
        if recall:
            messages.append(SystemMessage(content=recall))

        for m in self._conv.recent_messages(user_id, limit=20):
            if m["role"] == "user":
                messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                messages.append(AIMessage(content=m["content"]))
            # stored 'tool' rows are audit history; the live loop handles tool exchange
        return messages

    def _recall_block(self, user_id: str) -> str:
        """Assemble cross-session recall: goal-aware profile + missing essentials,
        episodic memories, and the rolling summary."""
        parts: list[str] = []
        facts = self._mem.get_facts(user_id)
        goal = facts.get("goal")

        rendered = profile.render_profile(facts, goal=goal)
        if rendered:
            parts.append(rendered)
        missing = profile.missing_essentials(goal, facts)
        if missing:
            parts.append(f"Still need for {goal}: " + ", ".join(missing))

        memories = self._mem.get_memories(user_id)
        if memories:
            if (goal or "").strip().lower() == "troubleshoot":
                # surface what happened / what worked first when diagnosing
                memories = sorted(
                    memories,
                    key=lambda m: 0 if m["category"] in ("event", "learning") else 1,
                )
            parts.append("RECENT HISTORY\n" + "\n".join(
                f"- ({m['category']}) {m['content']}" for m in memories
            ))

        summary = self._mem.get_summary(user_id)
        if summary:
            parts.append("PAST SUMMARY: " + summary)
        return "\n\n".join(parts)

    # --- the tool-calling loop -------------------------------------------
    def _run_tool_loop(self, messages: list, user_id: str) -> str:
        for _ in range(_MAX_ITERS):
            ai = self._bound.invoke(messages)
            messages.append(ai)
            tool_calls = getattr(ai, "tool_calls", None)
            if not tool_calls:
                return (ai.content or "").strip() or "I'm not sure how to help with that yet."
            for call in tool_calls:
                tool = self._tools_by_name.get(call["name"])
                if tool is None:
                    result = f"TOOL_ERROR: unknown tool {call['name']!r}"
                else:
                    try:
                        result = tool.invoke(call["args"])
                    except Exception as exc:  # fed back so the model can correct; never hidden
                        result = f"TOOL_ERROR: {exc}"
                self._conv.append_message(user_id, "tool", result, tool_name=call["name"])
                captured = profile.profile_updates_from_tool(call["name"], call["args"], result)
                if captured:
                    self._mem.set_facts(user_id, captured, source="tool_call")
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        # Hit the tool-call cap (e.g. the model kept calling tools without answering). Force a
        # final natural-language reply with tools disabled, so the user always gets a real answer.
        try:
            messages.append(SystemMessage(content="Now reply to the user in plain text using what "
                                                   "you have. Do not call any more tools."))
            final = self._base.invoke(messages)
            text = (getattr(final, "content", "") or "").strip()
            if text:
                return text
        except Exception:
            log.debug("forced final answer failed", exc_info=True)
        return "Here's what I have so far — could you tell me a bit more so I can pin it down?"

    # --- the single public seam ------------------------------------------
    def handle_message(self, channel: str, channel_user: str, text: str, display_name: str | None = None) -> str:
        user_id = self._conv.get_or_create_user(channel, channel_user, display_name)
        self._mem.set_facts(user_id, memory_extract.extract_facts(text), source="parsed")
        self._conv.append_message(user_id, "user", text)
        runtime.set_current(self._mem, user_id)  # lets memory tools reach this user
        try:
            messages = self._build_context(user_id)
            reply = self._run_tool_loop(messages, user_id)
        finally:
            runtime.clear_current()
        self._conv.append_message(user_id, "assistant", reply)
        self._schedule_summary(user_id)
        return reply

    def profile_text(self, channel: str, channel_user: str) -> str:
        """Human-readable view of what the agent remembers — backs the /whoami command."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        block = self._recall_block(user_id)
        return block or "I don't know anything about your system yet. Tell me about it!"

    def reset(self, channel: str, channel_user: str) -> None:
        """Clear the conversation thread. Long-term memory (facts/memories) is kept."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._conv.reset_conversation(user_id)

    def forget_everything(self, channel: str, channel_user: str) -> None:
        """Wipe conversation AND long-term memory for this user (the /forget command)."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._conv.reset_conversation(user_id)
        self._mem.forget(user_id)

    def set_goal(self, channel: str, channel_user: str, goal: str) -> str:
        """Explicitly set the consultation goal (backs the /design, /optimize, /troubleshoot
        commands). Persists profile['goal'] and returns the user-facing confirmation. Does
        NOT touch conversation history or other facts. Raises ValueError on an unknown goal."""
        g = (goal or "").strip().lower()
        if g not in profile.GOALS:
            raise ValueError(f"unknown goal {goal!r}")
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._mem.set_fact(user_id, "goal", g, source="user_stated")
        facts = self._mem.get_facts(user_id)
        return f"{profile.GOAL_HEADERS[g]}. {profile.essentials_hint(g, facts)}"

    # --- background cross-session summary (no user-facing latency) --------
    def _schedule_summary(self, user_id: str, every: int = 12) -> None:
        """Refresh the rolling summary in a daemon thread once history is long enough."""
        msgs = self._conv.recent_messages(user_id, limit=200)
        if len(msgs) < every:
            return
        threading.Thread(target=self._refresh_summary, args=(user_id, msgs), daemon=True).start()

    def _refresh_summary(self, user_id: str, msgs: list) -> None:
        try:
            transcript = "\n".join(
                f"{m['role']}: {m['content']}" for m in msgs if m["role"] in ("user", "assistant")
            )[:6000]
            prompt = (
                "Summarise this user's aquaponics system and the key points of the conversation "
                "in 2-4 sentences, for your own future recall. Focus on durable facts, decisions, "
                "and open problems — not pleasantries.\n\n" + transcript
            )
            summary = get_llm(temperature=0.0).invoke(prompt).strip()
            if summary:
                self._mem.set_summary(user_id, summary)
        except Exception:  # background best-effort; never affect the live turn
            log.debug("summary refresh failed", exc_info=True)


def _repl() -> None:
    """Local dry-run: talk to the agent from the terminal, no Telegram. Needs a configured
    tool-calling provider (e.g. LLM_PROVIDER=nvidia NVIDIA_API_KEY=...)."""
    import agent  # loads .env
    agent_ = AgronautAgent()
    print("Agronaut REPL — type 'quit' to exit, '/reset' to clear.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"quit", "exit"}:
            break
        if text == "/reset":
            agent_.reset("cli", "local")
            print("(conversation reset)")
            continue
        if text:
            print("agronaut>", agent_.handle_message("cli", "local", text))


if __name__ == "__main__":
    _repl()
