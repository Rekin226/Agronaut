"""The fixed prompt has to fit the context window Agronaut asks Ollama for (#181).

Every turn starts with the system prompt and all 30 tool schemas, about 11.3K tokens on
2026-09-30. When a prompt overflows Ollama's window, Ollama does not refuse it: it keeps
the first 24 tokens and the end, and silently drops the middle, where the instructions are.
This fails CI if the fixed part grows past half the default window, leaving too little room
for the conversation, instead of letting that happen quietly one tool at a time.

The token count is an estimate (characters / 3.5). The measured ratio with a real tokenizer
was about 4 characters per token, so the estimate errs on the side of failing early.
"""

import json

from langchain_core.utils.function_calling import convert_to_openai_tool

from agent.llm import DEFAULT_OLLAMA_NUM_CTX
from agronaut_agent.core import SYSTEM_PROMPT
from agronaut_agent.tools import AGRONAUT_TOOLS


def _fixed_prompt_tokens() -> int:
    schemas = json.dumps([convert_to_openai_tool(t) for t in AGRONAUT_TOOLS])
    return int((len(SYSTEM_PROMPT) + len(schemas)) / 3.5)


def test_the_fixed_prompt_leaves_half_the_default_window_for_the_conversation():
    fixed = _fixed_prompt_tokens()
    assert fixed <= DEFAULT_OLLAMA_NUM_CTX // 2, (
        f"The system prompt and tool schemas are now ~{fixed} tokens, more than half of the "
        f"{DEFAULT_OLLAMA_NUM_CTX}-token window Agronaut asks Ollama for. Real turns add "
        "history and tool results on top, and an overflowing prompt loses its middle "
        "silently. Trim the prompt or the tool descriptions, or send fewer tools per turn.")
