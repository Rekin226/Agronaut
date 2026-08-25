"""The Telegram adapter's command wiring — verified without running the bot."""

from agronaut_agent.channels.telegram_adapter import TelegramAdapter


def _adapter():
    # token bypasses env lookup; allowed_ids=[] -> open (we only inspect wiring here).
    return TelegramAdapter(agent=object(), token="x:y", allowed_ids=[])


def test_command_specs_include_mode_commands():
    names = [c for c, _h, _desc in _adapter()._command_specs()]
    for cmd in ("design", "optimize", "troubleshoot"):
        assert cmd in names


def test_command_specs_keep_existing_commands_for_menu():
    names = [c for c, _h, _desc in _adapter()._command_specs()]
    for cmd in ("start", "help", "whoami", "reset", "forget"):
        assert cmd in names


def test_command_specs_include_data_rights_commands():
    a = _adapter()
    names = [c for c, _h, _desc in a._command_specs()]
    assert "export" in names and "delete_me" in names
    assert hasattr(a, "_on_export") and hasattr(a, "_on_delete_me")


def test_every_command_spec_has_a_callable_handler_and_description():
    for cmd, handler, desc in _adapter()._command_specs():
        assert callable(handler), cmd
        assert isinstance(desc, str) and desc, cmd


def test_mode_handlers_exist():
    a = _adapter()
    for attr in ("_on_design", "_on_optimize", "_on_troubleshoot", "_set_mode", "_post_init"):
        assert hasattr(a, attr)


def test_adapter_has_followup_poller():
    a = _adapter()
    assert hasattr(a, "_followup_tick")
    import inspect
    assert inspect.iscoroutinefunction(a._followup_tick)


# --- media handlers -----------------------------------------------------------

import asyncio


class _FakePhoto:
    async def get_file(self):
        class _F:
            async def download_as_bytearray(self_inner):
                return bytearray(b"imgbytes")
        return _F()


class _FakeDoc:
    def __init__(self, mime):
        self.mime_type = mime

    async def get_file(self):
        class _F:
            async def download_as_bytearray(self_inner):
                return bytearray(b"imgbytes")
        return _F()


class _Recorder:
    def __init__(self):
        self.replies = []
        self.photos = []
        self.documents = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)

    async def reply_photo(self, photo, **kw):
        self.photos.append(getattr(photo, "name", "photo"))

    async def reply_document(self, document, **kw):
        self.documents.append(getattr(document, "name", "document"))


class _FakeUpdate:
    def __init__(self, message, user_id=1, chat_id=99):
        self.message = message
        self.effective_user = type("U", (), {"id": user_id, "full_name": "Tester"})()
        self.effective_chat = type("C", (), {"id": chat_id})()


class _FakeVoice:
    def __init__(self, mime="audio/ogg"):
        self.mime_type = mime

    async def get_file(self):
        class _F:
            async def download_as_bytearray(self_inner):
                return bytearray(b"oggbytes")
        return _F()


class _FakeMessage:
    def __init__(self, photo=None, document=None, caption=None, voice=None, audio=None):
        self.photo = photo
        self.document = document
        self.caption = caption
        self.voice = voice
        self.audio = audio
        self.recorder = _Recorder()

    async def reply_text(self, text, **kw):
        await self.recorder.reply_text(text, **kw)

    async def reply_photo(self, photo, **kw):
        await self.recorder.reply_photo(photo, **kw)

    async def reply_document(self, document, **kw):
        await self.recorder.reply_document(document, **kw)


class _FakeCtx:
    class _Bot:
        async def send_chat_action(self, *a, **k):
            return None
    bot = _Bot()


class _ImgAgent:
    channel = None

    def __init__(self, attachments=None):
        self._atts = attachments or []

    def handle_image(self, channel, chat_id, image_bytes, caption, display_name=None):
        assert image_bytes == b"imgbytes"
        return f"saw image (caption={caption})"

    def handle_voice(self, channel, chat_id, audio_bytes, mime, display_name=None):
        assert audio_bytes == b"oggbytes"
        return f"heard voice (mime={mime})"

    def handle_message(self, channel, chat_id, text, display_name=None):
        return "here's your diagram"

    def take_attachments(self, channel, chat_id):
        atts, self._atts = self._atts, []
        return atts


def test_photo_handler_routes_to_handle_image():
    a = TelegramAdapter(agent=_ImgAgent(), token="x:y", allowed_ids=[])
    msg = _FakeMessage(photo=[_FakePhoto()], caption="what's wrong?")
    upd = _FakeUpdate(msg)
    asyncio.run(a._on_photo(upd, _FakeCtx()))
    assert any("saw image" in r and "what's wrong?" in r for r in msg.recorder.replies)


def test_document_image_routes_to_handle_image():
    a = TelegramAdapter(agent=_ImgAgent(), token="x:y", allowed_ids=[])
    msg = _FakeMessage(document=_FakeDoc("image/png"))
    asyncio.run(a._on_document(_FakeUpdate(msg), _FakeCtx()))
    assert any("saw image" in r for r in msg.recorder.replies)


def test_non_image_document_declined_gracefully():
    a = TelegramAdapter(agent=_ImgAgent(), token="x:y", allowed_ids=[])
    msg = _FakeMessage(document=_FakeDoc("application/pdf"))
    asyncio.run(a._on_document(_FakeUpdate(msg), _FakeCtx()))
    assert any("can't read files like that yet" in r.lower() for r in msg.recorder.replies)


def test_text_handler_sends_schematic_attachment_as_photo(tmp_path):
    png = tmp_path / "schematic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    a = TelegramAdapter(agent=_ImgAgent(attachments=[str(png)]), token="x:y", allowed_ids=[])
    msg = _FakeMessage()
    msg.text = "draw my system"
    asyncio.run(a._on_text(_FakeUpdate(msg), _FakeCtx()))
    assert any("diagram" in r for r in msg.recorder.replies)   # text reply
    assert len(msg.recorder.photos) == 1                       # + the image, inline
    assert msg.recorder.photos[0].endswith("schematic.png")


def test_voice_handler_routes_to_handle_voice():
    a = TelegramAdapter(agent=_ImgAgent(), token="x:y", allowed_ids=[])
    msg = _FakeMessage(voice=_FakeVoice("audio/ogg"))
    asyncio.run(a._on_voice(_FakeUpdate(msg), _FakeCtx()))
    assert any("heard voice" in r for r in msg.recorder.replies)


def test_media_handlers_registered_in_run():
    import inspect
    src = inspect.getsource(TelegramAdapter.run)
    assert "filters.PHOTO" in src
    assert "Document" in src
    assert "filters.VOICE" in src
