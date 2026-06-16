"""agent — the LLM-facing layer that wraps the deterministic aqua_model core.

Dependency rule (eng review): this layer may import from `aqua_model`, never the reverse.
`aqua_model` stays pure and Ollama-free; everything that touches the LLM or the UI lives here.
"""
