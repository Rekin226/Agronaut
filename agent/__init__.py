"""agent — the LLM-facing layer that wraps the deterministic aqua_model core.

Dependency rule (eng review): this layer may import from `aqua_model`, never the reverse.
`aqua_model` stays pure and Ollama-free; everything that touches the LLM or the UI lives here.
"""

# Load project-root .env (HF_TOKEN, etc.) as early as possible. Importing any agent
# submodule — the UI (calculator_ui/optimizer_ui) or the chat layer (agent.llm) —
# triggers this, so secrets land in os.environ before any backend is built.
from agent.env import load_env as _load_env

_load_env()
