"""WhatsApp Cloud API adapter, verified without a live Meta number or a running server.
The adapter is built on the same ChannelAdapter contract as Telegram; these tests exercise
its pure pieces (webhook parse, verify handshake, signature check) and its routing to the
agent + outbound send (with requests mocked).
"""

import hashlib
import hmac
import json

from agronaut_agent.channels import base
from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter


class _FakeAgent:
    def __init__(self, attachments=None):
        self.calls = []
        self.images = []
        self.voices = []
        self._atts = attachments or []

    def handle_message(self, channel, channel_user, text, display_name=None):
        self.calls.append((channel, channel_user, text))
        return f"reply to {text}"

    def handle_image(self, channel, channel_user, image_bytes, caption=None,
                     display_name=None):
        self.images.append((channel, channel_user, image_bytes, caption))
        return "reply about the photo"

    def handle_voice(self, channel, channel_user, audio_bytes, mime=None,
                     display_name=None):
        self.voices.append((channel, channel_user, audio_bytes, mime))
        return "reply about the voice note"

    def take_attachments(self, channel, channel_user):
        atts, self._atts = self._atts, []
        return atts

    def due_followups(self, channel):
        return [{"id": 1, "channel_user": "15551234567", "question": "did it work?"}]

    def mark_followup_sent(self, fid):
        self.calls.append(("sent", fid))

    def followup_send_failed(self, fid):
        self.calls.append(("failed", fid))


def _adapter(agent=None, **kw):
    kw.setdefault("allowed_ids", [])   # open — we test wiring, not the allowlist
    return WhatsAppAdapter(
        agent=agent or _FakeAgent(),
        token="ACCESS_TOKEN", phone_number_id="PNID",
        verify_token="myverify", app_secret="s3cret", **kw,
    )


def _incoming_payload(text="my tilapia look sick", sender="15551234567"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": sender, "id": "wamid.X", "type": "text",
                          "text": {"body": text}}],
        }}]}],
    }


def test_is_a_channel_adapter():
    assert issubclass(WhatsAppAdapter, base.ChannelAdapter)
    assert _adapter().channel_name == "whatsapp"


def test_verify_handshake_returns_challenge_on_match():
    a = _adapter()
    assert a.verify_webhook("subscribe", "myverify", "CH4LL3NGE") == "CH4LL3NGE"
    assert a.verify_webhook("subscribe", "wrong", "CH4LL3NGE") is None


def test_parse_incoming_extracts_sender_and_text():
    a = _adapter()
    msgs = a.parse_incoming(_incoming_payload())
    assert msgs == [("15551234567", "my tilapia look sick")]


def test_parse_incoming_ignores_status_and_nontext_events():
    a = _adapter()
    status_only = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    assert a.parse_incoming(status_only) == []
    assert a.parse_incoming({}) == []


def test_signature_verification():
    a = _adapter()
    body = json.dumps(_incoming_payload()).encode()
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert a.verify_signature(body, good) is True
    assert a.verify_signature(body, "sha256=deadbeef") is False
    assert a.verify_signature(body, None) is False


def test_handle_payload_routes_to_agent_and_sends_reply(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))

    a.handle_payload(_incoming_payload("size my system"))
    # routed to the agent under the whatsapp channel, keyed by the sender's number
    assert agent.calls == [("whatsapp", "15551234567", "size my system")]
    assert sent == [("15551234567", "reply to size my system")]


def test_send_text_posts_to_graph_api(monkeypatch):
    a = _adapter()
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"messages": [{"id": "wamid.Y"}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _Resp()

    monkeypatch.setattr("requests.post", _fake_post)
    a.send_text("15551234567", "hello")
    assert "PNID/messages" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer ACCESS_TOKEN"
    assert captured["json"]["to"] == "15551234567"
    assert captured["json"]["text"]["body"] == "hello"


def test_handle_payload_sends_schematic_attachment(monkeypatch, tmp_path):
    png = tmp_path / "schematic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    agent = _FakeAgent(attachments=[str(png)])
    a = _adapter(agent)
    sent_text, sent_media = [], []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent_text.append((to, text)))
    monkeypatch.setattr(a, "send_media", lambda to, path, **kw: sent_media.append((to, path)))

    a.handle_payload(_incoming_payload("draw my system"))
    assert sent_text == [("15551234567", "reply to draw my system")]
    assert sent_media == [("15551234567", str(png))]        # image sent after the text


def test_deliver_due_followups_sends_and_marks(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    a.deliver_due_followups()
    assert sent == [("15551234567", "did it work?")]
    assert ("sent", 1) in agent.calls


# --- inbound images ---------------------------------------------------------------------
# A photo is the most natural troubleshooting input a farmer has, and WhatsApp is the
# channel NGOs actually reach farmers on. send_media existed; there was no receive path.

def _image_payload(media_id="MEDIA123", caption="what's wrong with these?",
                   sender="15551234567", mime="image/jpeg"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": sender, "id": "wamid.IMG", "type": "image",
                          "image": {"id": media_id, "mime_type": mime,
                                    "caption": caption}}],
        }}]}],
    }


def _document_payload(media_id="MEDIA456", mime="image/png", filename="leaf.png",
                      sender="15551234567"):
    return {
        "entry": [{"changes": [{"value": {
            "messages": [{"from": sender, "id": "wamid.DOC", "type": "document",
                          "document": {"id": media_id, "mime_type": mime,
                                       "filename": filename}}],
        }}]}],
    }


def test_parse_incoming_images_extracts_media_id_and_caption():
    a = _adapter()
    assert a.parse_incoming_images(_image_payload()) == [
        ("15551234567", "MEDIA123", "what's wrong with these?")]


def test_parse_incoming_images_handles_a_missing_caption():
    a = _adapter()
    payload = _image_payload(caption=None)
    del payload["entry"][0]["changes"][0]["value"]["messages"][0]["image"]["caption"]
    assert a.parse_incoming_images(payload) == [("15551234567", "MEDIA123", None)]


def test_parse_incoming_images_accepts_an_image_sent_as_a_document():
    a = _adapter()
    assert a.parse_incoming_images(_document_payload()) == [
        ("15551234567", "MEDIA456", None)]


def test_parse_incoming_images_ignores_non_image_documents():
    a = _adapter()
    assert a.parse_incoming_images(_document_payload(mime="application/pdf")) == []


def test_parse_incoming_images_ignores_text_and_empty_payloads():
    a = _adapter()
    assert a.parse_incoming_images(_incoming_payload()) == []
    assert a.parse_incoming_images({}) == []


def test_text_parser_still_ignores_image_messages():
    # parse_incoming is the text path; images must not leak into it as empty bodies.
    a = _adapter()
    assert a.parse_incoming(_image_payload()) == []


def test_handle_payload_routes_an_image_to_handle_image(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: b"jpegbytes")

    a.handle_payload(_image_payload())
    assert agent.images == [("whatsapp", "15551234567", b"jpegbytes",
                            "what's wrong with these?")]
    assert sent == [("15551234567", "reply about the photo")]


def test_handle_payload_declines_an_unsupported_document(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))

    a.handle_payload(_document_payload(mime="application/pdf"))
    assert agent.images == []                      # nothing invented from a PDF
    assert len(sent) == 1
    assert "photo" in sent[0][1].lower()           # told what it CAN read


def test_handle_payload_survives_a_failed_media_download(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: None)

    a.handle_payload(_image_payload())
    assert agent.images == []
    assert len(sent) == 1 and "couldn't" in sent[0][1].lower()


def test_handle_payload_survives_a_handle_image_error(monkeypatch):
    class _Boom(_FakeAgent):
        def handle_image(self, *a, **kw):
            raise RuntimeError("vlm down")

    a = _adapter(_Boom())
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: b"jpegbytes")

    a.handle_payload(_image_payload())
    assert len(sent) == 1 and "went wrong" in sent[0][1].lower()


def test_images_from_a_disallowed_sender_are_ignored(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent, allowed_ids=["15559999999"])
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: b"jpegbytes")

    a.handle_payload(_image_payload(sender="15551234567"))
    assert agent.images == [] and sent == []


def test_download_media_does_the_two_step_graph_fetch(monkeypatch):
    a = _adapter()
    calls = []

    class _Resp:
        def __init__(self, status=200, payload=None, content=b""):
            self.status_code, self._payload, self.content = status, payload or {}, content
            self.text = ""

        def json(self):
            return self._payload

    def _fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        if url.endswith("/MEDIA123"):
            return _Resp(payload={"url": "https://lookaside.fbsbx.com/x", "mime_type": "image/jpeg"})
        return _Resp(content=b"realjpegbytes")

    monkeypatch.setattr("requests.get", _fake_get)
    assert a.download_media("MEDIA123") == b"realjpegbytes"
    assert len(calls) == 2
    # BOTH hops need the bearer token — the lookaside URL is authenticated too
    assert calls[0][1]["Authorization"] == "Bearer ACCESS_TOKEN"
    assert calls[1][1]["Authorization"] == "Bearer ACCESS_TOKEN"


def test_download_media_returns_none_when_the_lookup_fails(monkeypatch):
    a = _adapter()

    class _Resp:
        status_code = 404
        text = "not found"

        def json(self):
            return {}

    monkeypatch.setattr("requests.get", lambda *args, **kw: _Resp())
    assert a.download_media("MEDIA123") is None


# --- inbound voice notes ------------------------------------------------------------------
# Telegram has had voice input since PLAN 1.4. WhatsApp is the channel that actually reaches
# low-literacy farmers, so a voice note mattering more here than anywhere else was the gap.

def _audio_payload(media_id="AUDIO789", sender="15551234567",
                   mime="audio/ogg; codecs=opus", kind="audio"):
    return {
        "entry": [{"changes": [{"value": {
            "messages": [{"from": sender, "id": "wamid.AUD", "type": kind,
                          kind: {"id": media_id, "mime_type": mime, "voice": True}}],
        }}]}],
    }


def test_parse_incoming_audio_extracts_media_id_and_mime():
    a = _adapter()
    assert a.parse_incoming_audio(_audio_payload()) == [
        ("15551234567", "AUDIO789", "audio/ogg; codecs=opus")]


def test_parse_incoming_audio_accepts_a_voice_type_message():
    a = _adapter()
    assert a.parse_incoming_audio(_audio_payload(kind="voice")) == [
        ("15551234567", "AUDIO789", "audio/ogg; codecs=opus")]


def test_parse_incoming_audio_accepts_an_audio_document():
    a = _adapter()
    payload = _document_payload(mime="audio/mpeg", filename="note.mp3")
    assert a.parse_incoming_audio(payload) == [("15551234567", "MEDIA456", "audio/mpeg")]


def test_parse_incoming_audio_ignores_text_and_images():
    a = _adapter()
    assert a.parse_incoming_audio(_incoming_payload()) == []
    assert a.parse_incoming_audio(_image_payload()) == []


def test_audio_is_not_also_reported_as_an_unsupported_file():
    """The decline path must not fire for something we now handle — otherwise the user gets
    an answer AND a "can't read that" message for the same voice note."""
    a = _adapter()
    assert a.parse_unsupported_files(_audio_payload()) == []
    assert a.parse_unsupported_files(_document_payload(mime="audio/mpeg")) == []


def test_handle_payload_routes_a_voice_note_to_handle_voice(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: b"oggbytes")

    a.handle_payload(_audio_payload())
    assert agent.voices == [("whatsapp", "15551234567", b"oggbytes", "audio/ogg; codecs=opus")]
    assert sent == [("15551234567", "reply about the voice note")]


def test_handle_payload_survives_a_failed_voice_download(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: None)

    a.handle_payload(_audio_payload())
    assert agent.voices == []
    assert len(sent) == 1 and "couldn't" in sent[0][1].lower()


def test_handle_payload_survives_a_handle_voice_error(monkeypatch):
    class _Boom(_FakeAgent):
        def handle_voice(self, *a, **kw):
            raise RuntimeError("asr down")

    a = _adapter(_Boom())
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(a, "download_media", lambda mid: b"oggbytes")

    a.handle_payload(_audio_payload())
    assert len(sent) == 1 and "went wrong" in sent[0][1].lower()


def test_voice_notes_from_a_disallowed_sender_are_ignored(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent, allowed_ids=["15559999999"])
    monkeypatch.setattr(a, "send_text", lambda to, text: None)
    monkeypatch.setattr(a, "download_media", lambda mid: b"oggbytes")
    a.handle_payload(_audio_payload())
    assert agent.voices == []
