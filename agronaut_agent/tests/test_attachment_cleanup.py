"""Temp files created by tools must be cleaned up after the channel adapter sends them.

Bug: Agronaut #124 — every 3D model the bot sends leaves a temp file behind forever.
Fix: _deliver (Telegram) and _flush_attachments (WhatsApp) now unlink the file
     in a finally block so the cleanup runs regardless of success or failure.

No external deps — uses sync stub adapters to exercise the cleanup path.
"""

import asyncio
import os
import unittest.mock

from agronaut_agent.channels.telegram_adapter import TelegramAdapter
from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter


class _StubAgent:
    """Minimal agent that records one attachment per (channel, user_id) and drains it once."""
    def __init__(self, attachments=None):
        # Keyed by (channel, user_id) — must match what _flush_attachments / _deliver expects
        self._attachments = attachments or {}

    def take_attachments(self, channel, user_id):
        return self._attachments.pop((channel, user_id), [])


# --- Telegram ---------------------------------------------------------------


def test_telegram_deletes_file_after_photo_send(tmp_path):
    """Verify the Telegram adapter cleans up temp PNG files after sending."""
    png_path = str(tmp_path / "schematic.png")
    with open(png_path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert os.path.exists(png_path)

    agent = _StubAgent({("telegram", "chat"): [png_path]})
    adapter = TelegramAdapter(agent=agent, token="x:y", allowed_ids=[])

    class MockMessage:
        photos = []

        async def reply_photo(self, *, photo):
            MockMessage.photos.append(getattr(photo, "name", str(photo)))

        async def reply_document(self, *, document): pass
        async def reply_text(self, *a, **kw): pass

    class MockUpdate:
        message = MockMessage()

    update = MockUpdate()

    async def run():
        await adapter._deliver(update, "chat", "Here is your diagram")
    asyncio.run(run())

    assert not os.path.exists(png_path), "temp file must be deleted after send"
    assert MockMessage.photos == [png_path], "the PNG must actually be sent as a photo"


def test_telegram_deletes_file_even_when_send_fails(tmp_path):
    """Verify cleanup happens even if sending the file raises an error."""
    html_path = str(tmp_path / "scene.html")
    with open(html_path, "w") as fh:
        fh.write("<html>3D scene</html>")
    assert os.path.exists(html_path)

    agent = _StubAgent({("telegram", "chat"): [html_path]})
    adapter = TelegramAdapter(agent=agent, token="x:y", allowed_ids=[])

    class FailingMessage:
        async def reply_photo(self, *, photo):
            raise OSError("disk full")
        async def reply_document(self, *, document):
            raise OSError("disk full")
        async def reply_text(self, *a, **kw): pass

    class FailingUpdate:
        message = FailingMessage()

    update = FailingUpdate()

    async def run():
        await adapter._deliver(update, "chat", "oops")
    asyncio.run(run())

    assert not os.path.exists(html_path), "temp must be cleaned even on send failure"


# --- WhatsApp ---------------------------------------------------------------


def test_whatsapp_deletes_file_after_media_send(tmp_path):
    """Verify the WhatsApp adapter cleans up media files after sending."""
    png_path = str(tmp_path / "schematic.png")
    with open(png_path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert os.path.exists(png_path)

    # _flush_attachments calls take_attachments(self.channel_name, uid)
    # self.channel_name = "whatsapp", uid = "uid" → key is ("whatsapp", "uid")
    agent = _StubAgent({("whatsapp", "uid"): [png_path]})
    adapter = WhatsAppAdapter(agent=agent, token="x", phone_number_id="pn")

    # Mock send_media on the instance so it never hits the network
    with unittest.mock.patch.object(adapter, "send_media"):
        adapter._flush_attachments("sender", "uid")

    assert not os.path.exists(png_path), "media file must be deleted after send"


def test_whatsapp_deletes_file_even_when_send_fails(tmp_path):
    """Verify cleanup happens even if sending fails."""
    png_path = str(tmp_path / "schematic.png")
    with open(png_path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert os.path.exists(png_path)

    agent = _StubAgent({("whatsapp", "uid"): [png_path]})
    adapter = WhatsAppAdapter(agent=agent, token="x", phone_number_id="pn")

    with unittest.mock.patch.object(adapter, "send_media", side_effect=OSError("network")):
        adapter._flush_attachments("sender", "uid")

    assert not os.path.exists(png_path), "media must be cleaned even on send failure"


def test_whatsapp_cleanup_on_runtime_error(tmp_path):
    """Verify cleanup even when send_media raises an exception."""
    html_path = str(tmp_path / "scene.html")
    with open(html_path, "w") as fh:
        fh.write("<html>3D</html>")
    assert os.path.exists(html_path)

    agent = _StubAgent({("whatsapp", "uid"): [html_path]})
    adapter = WhatsAppAdapter(agent=agent, token="x", phone_number_id="pn")

    with unittest.mock.patch.object(adapter, "send_media", side_effect=RuntimeError("network")):
        adapter._flush_attachments("sender", "uid")

    assert not os.path.exists(html_path), "file cleaned even when send_media raises"