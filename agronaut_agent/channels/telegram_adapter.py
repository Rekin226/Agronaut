"""Telegram adapter (python-telegram-bot, async).

Maps a Telegram chat to a stable user_id, runs the (sync) agent in a worker thread so the
bot's event loop never blocks, and enforces a personal-assistant allowlist. /start and
/reset are handled locally; everything else goes to the agent.
"""

from __future__ import annotations

import asyncio
import logging
import os

from telegram import Update, BotCommand
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

    async def _on_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await update.message.reply_text(
            "🌱 *Agronaut* — your aquaponics assistant.\n\n"
            "Just tell me about your system or ask a question. I can:\n"
            "• *Size* a system (species, grow area, water temp, water budget)\n"
            "• *Optimize* the fish/crop ratio for a goal\n"
            "• *Troubleshoot* problems (e.g. \"fish gasping at dawn\")\n"
            "• *Remember* your setup across chats\n\n"
            "Commands:\n"
            "/design — size a new system\n"
            "/optimize — best fish/crop ratio\n"
            "/troubleshoot — diagnose a problem\n"
            "/whoami — what I remember about you\n"
            "/reset — clear this conversation (keeps long-term memory)\n"
            "/forget — wipe everything I know about you",
            parse_mode="Markdown",
        )

    async def _on_whoami(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        text = await asyncio.to_thread(
            self.agent.profile_text, self.channel_name, str(update.effective_chat.id)
        )
        await update.message.reply_text("Here's what I remember:\n\n" + text)

    async def _on_forget(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await asyncio.to_thread(self.agent.forget_everything, self.channel_name, str(update.effective_chat.id))
        await update.message.reply_text("Done — I've wiped everything I knew about your system. Clean slate.")

    async def _on_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await asyncio.to_thread(self.agent.reset, self.channel_name, str(update.effective_chat.id))
        await update.message.reply_text("Cleared this conversation (I still remember your setup). What's next?")

    async def _set_mode(self, update: Update, goal: str) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        msg = await asyncio.to_thread(
            self.agent.set_goal, self.channel_name, str(update.effective_chat.id), goal
        )
        await update.message.reply_text(msg)

    async def _on_design(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_mode(update, "design")

    async def _on_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_mode(update, "optimize")

    async def _on_troubleshoot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_mode(update, "troubleshoot")

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

    def _command_specs(self):
        """Single source of (command, handler, menu description) — drives both handler
        registration and the Telegram / command menu, so the two never drift."""
        return [
            ("start", self._on_start, "What Agronaut is"),
            ("help", self._on_help, "Show help"),
            ("design", self._on_design, "Mode: size a new system"),
            ("optimize", self._on_optimize, "Mode: best fish/crop ratio"),
            ("troubleshoot", self._on_troubleshoot, "Mode: diagnose a problem"),
            ("whoami", self._on_whoami, "What I remember about you"),
            ("reset", self._on_reset, "Clear this conversation"),
            ("forget", self._on_forget, "Wipe everything I know"),
        ]

    async def _post_init(self, app: Application) -> None:
        """Register the / command menu once the app is up. Non-fatal on failure."""
        commands = [BotCommand(c, desc) for c, _h, desc in self._command_specs()]
        try:
            await app.bot.set_my_commands(commands)
        except Exception:  # transient network etc. — commands still work by typing
            log.warning("set_my_commands failed; commands still work by typing", exc_info=True)

    def run(self) -> None:
        app = Application.builder().token(self.token).post_init(self._post_init).build()
        for name, handler, _desc in self._command_specs():
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        scope = f"{len(self.allowed_ids)} allowed id(s)" if self.allowed_ids else "OPEN (no allowlist)"
        log.info("Agronaut Telegram bot starting — %s", scope)
        app.run_polling()
