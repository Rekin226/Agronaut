"""Agronaut Telegram bot entrypoint.

Run:  python bot.py
Needs (in .env or the environment):
  TELEGRAM_BOT_TOKEN   from @BotFather
  AGRONAUT_ALLOWED_IDS comma-separated Telegram user IDs allowed to use the bot
  LLM_PROVIDER=nvidia  NVIDIA_API_KEY=...   (tool-calling brain; free at build.nvidia.com)
  LLM_MODEL            optional; mistralai/mistral-nemotron measured 20x faster than
                       llama-3.3-70b with correct tool calls (docs/telegram_twin_testing.md)
"""

from __future__ import annotations

import logging

import agent  # noqa: F401 — importing the package loads project-root .env (agent/__init__.py)
from agronaut_agent.core import AgronautAgent
from agronaut_agent.channels.telegram_adapter import TelegramAdapter


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    brain = AgronautAgent()
    TelegramAdapter(brain).run()


if __name__ == "__main__":
    main()
