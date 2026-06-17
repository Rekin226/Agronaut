"""Telegram adapter (python-telegram-bot, async).

Maps a Telegram chat to a stable user_id, runs the (sync) agent in a worker thread so the
bot's event loop never blocks, and enforces a personal-assistant allowlist. /start and
/reset are handled locally; everything else goes to the agent.
"""

from __future__ import annotations

import asyncio
import logging
import os

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from ..core import AgronautAgent
from .base import ChannelAdapter, chunk

log = logging.getLogger(__name__)


def _parse_allowlist(raw: str | None) -> set[str]:
    return {x.strip() for x in (raw or "").split(",") if x.strip()}


class TelegramAdapter(ChannelAdapter):
    channel_name = "telegram"

    def __init__(self, agent: AgronautAgent, token: str | None = None, allowed_ids=None):
        super().__init__(agent)
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        # Allowlist of Telegram user IDs. Empty set => open to anyone (discouraged).
        self.allowed_ids = (
            set(map(str, allowed_ids)) if allowed_ids is not None
            else _parse_allowlist(os.getenv("AGRONAUT_ALLOWED_IDS"))
        )

    def _allowed(self, update: Update) -> bool:
        if not self.allowed_ids:
            return True
        user = update.effective_user
        return bool(user and str(user.id) in self.allowed_ids)

    async def _on_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await update.message.reply_text(
            "🌱 I'm Agronaut — your aquaponics assistant. Tell me about your system "
            "(species, grow area, water temp, water budget) and I'll size it, optimize the "
            "fish/crop ratio, or help troubleshoot. /reset clears our conversation."
        )

    async def _on_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await asyncio.to_thread(self.agent.reset, self.channel_name, str(update.effective_chat.id))
        await update.message.reply_text("Cleared. Fresh start — what are we working on?")

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        chat_id = str(update.effective_chat.id)
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            reply = await asyncio.to_thread(
                self.agent.handle_message,
                self.channel_name, chat_id, update.message.text,
                update.effective_user.full_name if update.effective_user else None,
            )
        except Exception:  # never leave the user hanging on an unexpected error
            log.exception("agent.handle_message failed")
            reply = "Something went wrong on my side. Try again, or rephrase?"
        for part in chunk(reply):
            await update.message.reply_text(part)

    async def _deny(self, update: Update) -> None:
        await update.message.reply_text(
            "This is a private Agronaut assistant. Ask the owner to add your Telegram ID "
            f"(yours is {update.effective_user.id})." if update.effective_user else "Access restricted."
        )

    def run(self) -> None:
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self._on_start))
        app.add_handler(CommandHandler("reset", self._on_reset))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        scope = f"{len(self.allowed_ids)} allowed id(s)" if self.allowed_ids else "OPEN (no allowlist)"
        log.info("Agronaut Telegram bot starting — %s", scope)
        app.run_polling()
