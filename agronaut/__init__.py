"""Agronaut command-line interface.

A thin, dependency-free (stdlib only) wrapper over the deterministic trust zone, so the
Design Calculator and Ratio Optimizer run from a terminal or a script without Streamlit,
an LLM, or a network. It reuses the SAME validation seam as the UI (`agent.facts`), so the
CLI can never put a number into the model that the app wouldn't.

    python -m agronaut design   --species tilapia --crop lettuce --grow-area 20 --temp 26 --water-budget 200
    python -m agronaut optimize --grow-area 20 --temp 26 --water-budget 200 --objective water_efficiency
    python -m agronaut species
    python -m agronaut crops
"""

from __future__ import annotations

from .cli import main

__all__ = ["main"]
