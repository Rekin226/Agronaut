"""agronaut_agent — the channel-agnostic, tool-calling assistant brain.

The LLM orchestrates; it never invents numbers. Every figure it reports comes from a
deterministic `aqua_model` tool call, carried through with its cited coefficients and
"not modeled" caveats. Channels (Telegram first) are thin adapters over `AgronautAgent`.
"""
