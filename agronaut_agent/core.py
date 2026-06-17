"""AgronautAgent — the channel-agnostic, tool-calling brain.

handle_message(channel, channel_user, text) is the single seam every channel adapter
calls. The LLM orchestrates Agronaut's deterministic tools and explains their results; a
bounded tool-loop runs the calls. The system prompt forbids inventing numbers — every
figure must come from a tool result, with its cited coefficients and caveats passed through.
"""

from __future__ import annotations

import os

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from agent.llm import get_chat_model
from .tools import AGRONAUT_TOOLS
from .store import _Db, ConversationStore, MemoryStore
from . import memory_extract

SYSTEM_PROMPT = """You are Agronaut, a personal aquaponics design and troubleshooting assistant.

You speak with operators and farmers — be concrete, warm, and brief. Reply in the user's language.

HARD RULES (these are your credibility):
- NEVER state a sizing number, bill-of-materials quantity, or coefficient that did not come
  from a tool result. For any sizing/optimization question, CALL the tool — do not estimate.
- When a tool returns coefficients and "not modeled" caveats, surface them: cite the source of
  key numbers and remind the user these are calibration seeds, not guarantees.
- If the trust gate rejects an input (VALIDATION_FAILED), ask the user for a corrected value.
  Never guess or work around it.
- For qualitative troubleshooting (symptoms, water quality, husbandry), use the knowledge tool
  and your general knowledge; say when you are reasoning from general knowledge.

You can size systems, optimize fish/crop ratios, render full reports, cross-check against real
pond data, and search curated knowledge. Use the tools; explain results plainly."""

_MAX_ITERS = 6


class AgronautAgent:
    def __init__(self, llm_provider=None, llm_model=None, db_path=None, chat_model=None):
        # chat_model injectable for tests (a fake bindable model); else build from config.
        base = chat_model if chat_model is not None else get_chat_model(llm_provider, llm_model)
        self._bound = base.bind_tools(AGRONAUT_TOOLS)
        self._tools_by_name = {t.name: t for t in AGRONAUT_TOOLS}
        db = _Db(db_path)
        self._conv = ConversationStore(db)
        self._mem = MemoryStore(db)

    # --- context assembly -------------------------------------------------
    def _build_context(self, user_id: str) -> list:
        messages: list = [SystemMessage(content=SYSTEM_PROMPT)]
        facts = self._mem.get_facts(user_id)
        if facts:
            known = "; ".join(f"{k}={v}" for k, v in facts.items())
            messages.append(SystemMessage(content=f"Known facts about this user's system: {known}"))
        for m in self._conv.recent_messages(user_id, limit=20):
            if m["role"] == "user":
                messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                messages.append(AIMessage(content=m["content"]))
            # stored 'tool' rows are audit history; the live loop handles tool exchange
        return messages

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
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        return ("I couldn't complete that reliably after several steps — "
                "let's narrow it down. Could you restate the key details?")

    # --- the single public seam ------------------------------------------
    def handle_message(self, channel: str, channel_user: str, text: str, display_name: str | None = None) -> str:
        user_id = self._conv.get_or_create_user(channel, channel_user, display_name)
        self._mem.set_facts(user_id, memory_extract.extract_facts(text), source="parsed")
        self._conv.append_message(user_id, "user", text)
        messages = self._build_context(user_id)
        reply = self._run_tool_loop(messages, user_id)
        self._conv.append_message(user_id, "assistant", reply)
        return reply

    def reset(self, channel: str, channel_user: str) -> None:
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._conv.reset_conversation(user_id)


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
