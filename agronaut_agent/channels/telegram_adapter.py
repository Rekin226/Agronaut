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
from .base import ChannelAdapter, chunk, room_identity, delivery_chat_id

log = logging.getLogger(__name__)

POLL_SECONDS = 60


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

    def _identity(self, update: Update) -> str:
        """Per-person memory key. Private chats -> the chat id (unchanged); group rooms ->
        '<chat>:<user>' so members don't share one profile."""
        chat = update.effective_chat
        user = update.effective_user
        return room_identity(chat.id, getattr(chat, "type", None),
                             user.id if user else chat.id)

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
            "• *Draw* your system as a labeled diagram (just ask me to draw it)\n"
            "• *See* a photo you send (sick fish, yellowing leaves, algae)\n"
            "• *Hear* a voice note and reply in your language\n"
            "• *Remember* your setup across chats\n\n"
            "Commands:\n"
            "/design — size a new system\n"
            "/optimize — best fish/crop ratio\n"
            "/troubleshoot — diagnose a problem\n"
            "/whoami — what I remember about you\n"
            "/export — download all your data (open JSON)\n"
            "/reset — clear this conversation (keeps long-term memory)\n"
            "/forget — wipe everything I know about you\n"
            "/delete\\_me — permanently erase all your data",
            parse_mode="Markdown",
        )

    async def _on_whoami(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        text = await asyncio.to_thread(
            self.agent.profile_text, self.channel_name, self._identity(update)
        )
        await update.message.reply_text("Here's what I remember:\n\n" + text)

    async def _on_forget(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await asyncio.to_thread(self.agent.forget_everything, self.channel_name, self._identity(update))
        await update.message.reply_text("Done — I've wiped everything I knew about your system. Clean slate.")

    async def _on_export(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        import io
        import json
        data = await asyncio.to_thread(
            self.agent.export_user_data, self.channel_name, self._identity(update))
        buf = io.BytesIO(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        buf.name = "agronaut_my_data.json"
        await update.message.reply_document(
            document=buf,
            caption="Everything I hold about you, in open JSON. /delete_me erases it all.")

    async def _on_delete_me(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await asyncio.to_thread(
            self.agent.delete_me, self.channel_name, self._identity(update))
        await update.message.reply_text(
            "Done — I've permanently erased all your data: conversation, profile, notes, and "
            "measurements. Nothing about you remains.")

    async def _deliver(self, update: Update, chat_id: str, reply: str) -> None:
        """Send the text reply, then any files the turn produced (e.g. a schematic).
        PNG/JPG go as inline photos; anything else as a document."""
        for part in chunk(reply):
            await update.message.reply_text(part)
        for path in self.agent.take_attachments(self.channel_name, chat_id):
            try:
                if str(path).lower().endswith((".png", ".jpg", ".jpeg")):
                    with open(path, "rb") as fh:
                        await update.message.reply_photo(photo=fh)
                else:
                    with open(path, "rb") as fh:
                        await update.message.reply_document(document=fh)
            except Exception:
                log.warning("failed to send attachment %s", path, exc_info=True)

    async def _on_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await asyncio.to_thread(self.agent.reset, self.channel_name, self._identity(update))
        await update.message.reply_text("Cleared this conversation (I still remember your setup). What's next?")

    async def _set_mode(self, update: Update, goal: str) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        msg = await asyncio.to_thread(
            self.agent.set_goal, self.channel_name, self._identity(update), goal
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
        chat_id = self._identity(update)
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
        await self._deliver(update, chat_id, reply)

    async def _on_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        chat_id = self._identity(update)
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            photo = update.message.photo[-1]              # largest available size
            tg_file = await photo.get_file()
            image_bytes = bytes(await tg_file.download_as_bytearray())
            reply = await asyncio.to_thread(
                self.agent.handle_image,
                self.channel_name, chat_id, image_bytes, update.message.caption,
                update.effective_user.full_name if update.effective_user else None,
            )
        except Exception:
            log.exception("agent.handle_image failed")
            reply = "Something went wrong reading that photo. Try again, or describe what you see?"
        await self._deliver(update, chat_id, reply)

    async def _on_document(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        doc = update.message.document
        mime = (getattr(doc, "mime_type", "") or "")
        if mime.startswith("image/"):
            # An image sent as an uncompressed file (not a Telegram "photo"): treat it as one.
            chat_id = self._identity(update)
            try:
                tg_file = await doc.get_file()
                image_bytes = bytes(await tg_file.download_as_bytearray())
                reply = await asyncio.to_thread(
                    self.agent.handle_image, self.channel_name, chat_id, image_bytes,
                    update.message.caption,
                    update.effective_user.full_name if update.effective_user else None,
                )
            except Exception:
                log.exception("agent.handle_image (document) failed")
                reply = "Something went wrong reading that image. Try again, or describe what you see?"
            await self._deliver(update, chat_id, reply)
            return
        await update.message.reply_text(
            "I can't read files like that yet — I work with text and photos. Send a photo of "
            "your fish, plants, or water, or just tell me what's going on."
        )

    async def _on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        chat_id = self._identity(update)
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            voice = update.message.voice or update.message.audio
            tg_file = await voice.get_file()
            audio_bytes = bytes(await tg_file.download_as_bytearray())
            mime = getattr(voice, "mime_type", None) or "audio/ogg"
            reply = await asyncio.to_thread(
                self.agent.handle_voice,
                self.channel_name, chat_id, audio_bytes, mime,
                update.effective_user.full_name if update.effective_user else None,
            )
        except Exception:
            log.exception("agent.handle_voice failed")
            reply = "Something went wrong with that voice note. Try again, or type your message?"
        await self._deliver(update, chat_id, reply)

    async def _deny(self, update: Update) -> None:
        await update.message.reply_text(
            "This is a private Agronaut assistant. Ask the owner to add your Telegram ID "
            f"(yours is {update.effective_user.id})." if update.effective_user else "Access restricted."
        )

    async def _followup_tick(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Deliver due outcome follow-ups. Runs once per JobQueue tick; best-effort — a
        failed poll or send never affects live message handling."""
        try:
            due = await asyncio.to_thread(self.agent.due_followups, self.channel_name)
            for fu in due:
                try:
                    await ctx.bot.send_message(chat_id=delivery_chat_id(fu["channel_user"]),
                                               text=fu["question"])
                    await asyncio.to_thread(self.agent.mark_followup_sent, fu["id"])
                except Exception:
                    log.warning("follow-up send failed for %s", fu["id"], exc_info=True)
                    await asyncio.to_thread(self.agent.followup_send_failed, fu["id"])
        except Exception:  # never let the poller die
            log.debug("follow-up poll failed", exc_info=True)

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
            ("export", self._on_export, "Download all my data (JSON)"),
            ("reset", self._on_reset, "Clear this conversation"),
            ("forget", self._on_forget, "Wipe everything I know"),
            ("delete_me", self._on_delete_me, "Permanently erase all my data"),
        ]

    async def _post_init(self, app: Application) -> None:
        """Register the / command menu and start the follow-up poller. Non-fatal on failure."""
        commands = [BotCommand(c, desc) for c, _h, desc in self._command_specs()]
        try:
            await app.bot.set_my_commands(commands)
        except Exception:  # transient network etc. — commands still work by typing
            log.warning("set_my_commands failed; commands still work by typing", exc_info=True)
        # JobQueue owns the poller's lifecycle: it starts after the app is running and is
        # cancelled cleanly on shutdown (unlike app.create_task in post_init, which warns).
        app.job_queue.run_repeating(self._followup_tick, interval=POLL_SECONDS, first=5)

    def run(self) -> None:
        app = Application.builder().token(self.token).post_init(self._post_init).build()
        for name, handler, _desc in self._command_specs():
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice))
        app.add_handler(MessageHandler(filters.Document.ALL, self._on_document))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        scope = f"{len(self.allowed_ids)} allowed id(s)" if self.allowed_ids else "OPEN (no allowlist)"
        log.info("Agronaut Telegram bot starting — %s", scope)
        app.run_polling()
