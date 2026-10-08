"""WhatsApp Cloud API adapter, verified without a live Meta number or a running server.
The adapter is built on the same ChannelAdapter contract as Telegram; these tests exercise
its pure pieces (webhook parse, verify handshake, signature check) and its routing to the
agent + outbound send (with requests mocked).
"""

import hashlib
import hmac
import json

import pytest

from agronaut_agent.channels import base
from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter, WhatsAppSendError


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

    def record_followup_message_id(self, fid, wa_id):
        self.calls.append(("wa_id", fid, wa_id))

    def followup_status_failed(self, wa_id, codes):
        self.calls.append(("status_failed", wa_id, codes))


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


# --- why a verify token was refused (never what it was) -------------------------------------

@pytest.mark.parametrize("received, says", [
    ("", "empty"),
    (" myverify ", "spaces"),
    ("MYVERIFY", "capitals"),
    ("EAAGm0PX4ZCpsBAAtoken", "ACCESS token"),
    ("an-old-token-from-setup", "23 characters"),
])
def test_a_refused_token_is_explained(received, says):
    assert says in _adapter().describe_token_mismatch(received)


def test_the_explanation_never_contains_either_token():
    a = _adapter()
    for received in ("an-old-token-from-setup", "EAAGm0PX4ZCpsBAAtoken", " myverify "):
        msg = a.describe_token_mismatch(received)
        assert received.strip() not in msg and "myverify" not in msg


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


def test_handle_payload_skips_html_attachment_and_explains_availability(monkeypatch, tmp_path):
    html_file = tmp_path / "scene.html"
    html_file.write_text("<!DOCTYPE html><html><body>3D Scene</body></html>", encoding="utf-8")
    agent = _FakeAgent(attachments=[str(html_file)])
    a = _adapter(agent)
    sent_media = []
    sent_text = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent_text.append((to, text)))
    monkeypatch.setattr(
        a, "send_media",
        lambda to, path, **kw: sent_media.append((to, path, kw.get("mime"))) or True,
    )

    a.handle_payload(_incoming_payload("show 3d scene"))
    assert sent_media == []  # HTML upload is skipped for WhatsApp
    assert any("3d view isn't available on whatsapp yet" in text.lower() for _, text in sent_text)
    assert any("telegram has it" in text.lower() for _, text in sent_text)
    assert not html_file.exists()  # verified finally block unlinks the temp file


def test_handle_payload_sends_document_attachment_with_guessed_mime(monkeypatch, tmp_path):
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    agent = _FakeAgent(attachments=[str(pdf_file)])
    a = _adapter(agent)
    sent_media = []
    monkeypatch.setattr(a, "send_text", lambda to, text: None)
    monkeypatch.setattr(
        a, "send_media",
        lambda to, path, **kw: sent_media.append((to, path, kw.get("mime"))) or True,
    )

    a.handle_payload(_incoming_payload("get report"))
    assert sent_media == [("15551234567", str(pdf_file), "application/pdf")]


def test_handle_payload_sends_png_attachment_with_png_mime(monkeypatch, tmp_path):
    png_file = tmp_path / "diagram.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    agent = _FakeAgent(attachments=[str(png_file)])
    a = _adapter(agent)
    sent_media = []
    monkeypatch.setattr(a, "send_text", lambda to, text: None)
    monkeypatch.setattr(
        a, "send_media",
        lambda to, path, **kw: sent_media.append((to, path, kw.get("mime"))) or True,
    )

    a.handle_payload(_incoming_payload("draw diagram"))
    assert sent_media == [("15551234567", str(png_file), "image/png")]


def test_flush_attachments_notifies_user_on_send_failure(monkeypatch, tmp_path):
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    agent = _FakeAgent(attachments=[str(pdf_file)])
    a = _adapter(agent)
    sent_text = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent_text.append((to, text)))
    monkeypatch.setattr(a, "send_media", lambda to, path, **kw: False)

    a._flush_attachments("15551234567", "15551234567")
    assert len(sent_text) == 1
    assert "couldn't send" in sent_text[0][1].lower()
    assert "report.pdf" in sent_text[0][1]
    assert "—" not in sent_text[0][1]  # house style avoids em dash


def test_flush_attachments_notifies_user_on_send_exception(monkeypatch, tmp_path):
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    agent = _FakeAgent(attachments=[str(pdf_file)])
    a = _adapter(agent)
    sent_text = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent_text.append((to, text)))

    def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(a, "send_media", _raise)

    a._flush_attachments("15551234567", "15551234567")
    assert len(sent_text) == 1
    assert "couldn't send" in sent_text[0][1].lower()
    assert "report.pdf" in sent_text[0][1]
    assert "—" not in sent_text[0][1]


def test_flush_attachments_survives_the_network_being_down(monkeypatch, tmp_path):
    """Every attachment is tried and every temp file deleted, even when the failure notice
    cannot be sent either. The notice used to raise out of the loop, so the files after the
    first failure were never tried and stayed on disk (the #124 leak by another door)."""
    import requests

    files = [tmp_path / "schematic.png", tmp_path / "scene.html", tmp_path / "plan.pdf"]
    for f in files:
        f.write_bytes(b"x")
    a = _adapter(_FakeAgent(attachments=[str(f) for f in files]))
    posts = []

    def _down(*args, **kwargs):
        posts.append(args[0])
        raise requests.ConnectionError("network down")

    monkeypatch.setattr("requests.post", _down)

    a._flush_attachments("15551234567", "15551234567")  # must not raise

    assert [f.name for f in files if f.exists()] == []
    uploads = [u for u in posts if u.endswith("/media")]
    assert len(uploads) == 2, "the PDF after the failed PNG was never tried"


def test_slash_command_document_failure_notice_does_not_raise(monkeypatch, tmp_path):
    a = _adapter()

    def _raise(to, text):
        raise RuntimeError("network down")

    monkeypatch.setattr(a, "send_text", _raise)
    a._notify_send_failed("15551234567", str(tmp_path / "export.json"))  # must not raise


def test_send_media_posts_document_payload_for_html(monkeypatch, tmp_path):
    html_file = tmp_path / "scene.html"
    html_file.write_text("<html><body>3D model</body></html>", encoding="utf-8")
    a = _adapter()
    posts = []

    class _Resp:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    def _fake_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        posts.append({"url": url, "headers": headers, "files": files, "data": data, "json": json})
        if "/media" in url:
            return _Resp(200, {"id": "DOC_MEDIA_123"})
        return _Resp(200, {"messages": [{"id": "wamid.DOC"}]})

    monkeypatch.setattr("requests.post", _fake_post)
    ok = a.send_media("15551234567", str(html_file), mime="text/html")
    assert ok is True
    assert len(posts) == 2

    # Check upload call
    upload_call = posts[0]
    assert upload_call["url"].endswith("/media")
    assert upload_call["data"]["type"] == "text/html"
    assert upload_call["files"]["file"][0] == "scene.html"
    assert upload_call["files"]["file"][2] == "text/html"

    # Check messages call
    msg_call = posts[1]
    assert msg_call["url"].endswith("/messages")
    assert msg_call["json"]["type"] == "document"
    assert msg_call["json"]["document"]["id"] == "DOC_MEDIA_123"
    assert msg_call["json"]["document"]["filename"] == "scene.html"


def test_send_media_returns_false_on_media_upload_failure(monkeypatch, tmp_path):
    html_file = tmp_path / "scene.html"
    html_file.write_text("<html></html>", encoding="utf-8")
    a = _adapter()

    class _Resp:
        status_code = 400
        text = '{"error": {"message": "Invalid MIME type"}}'

        def json(self):
            return {}

    monkeypatch.setattr("requests.post", lambda *args, **kw: _Resp())
    assert a.send_media("15551234567", str(html_file), mime="text/html") is False


def test_send_media_returns_false_on_message_send_failure(monkeypatch, tmp_path):
    html_file = tmp_path / "scene.html"
    html_file.write_text("<html></html>", encoding="utf-8")
    a = _adapter()
    calls = []

    class _Resp:
        def __init__(self, status_code=200, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def _fake_post(url, **kw):
        calls.append(url)
        if "/media" in url:
            return _Resp(200, {"id": "DOC123"})
        return _Resp(400, text='{"error": "recipient cannot receive documents"}')

    monkeypatch.setattr("requests.post", _fake_post)
    assert a.send_media("15551234567", str(html_file), mime="text/html") is False
    assert len(calls) == 2


def test_deliver_due_followups_sends_and_marks(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    a.deliver_due_followups()
    assert sent == [("15551234567", "did it work?")]
    assert ("sent", 1) in agent.calls


def test_send_text_raises_on_http_error(monkeypatch):
    # The follow-up loop marks a question sent unless send_text raises; an HTTP
    # error used to be logged and swallowed, so failures were recorded as sent.
    a = _adapter()

    class _Resp:
        status_code = 400
        text = '{"error": {"message": "bad request"}}'

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    with pytest.raises(WhatsAppSendError):
        a.send_text("15551234567", "hello")


def test_deliver_due_followups_records_failure_on_http_error(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)

    class _Resp:
        status_code = 500
        text = "internal error"

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    a.deliver_due_followups()
    assert ("failed", 1) in agent.calls
    assert ("sent", 1) not in agent.calls


# --- matching a delivery status back to its follow-up (#215) ---------------------------
# WhatsApp accepts a send outside the 24-hour window and reports the failure later,
# as a status webhook with error 131047. The message id is the only handle that
# links that status back to the follow-up it belongs to.

def _status_payload(wa_id="wamid.FU1", status="failed", codes=(131047,)):
    return {"entry": [{"changes": [{"value": {"statuses": [{
        "id": wa_id, "status": status,
        "errors": [{"code": c, "title": "Re-engagement message"} for c in codes],
    }]}}]}]}


def test_send_text_returns_message_ids(monkeypatch):
    # The Graph API answers a send with the message id; the adapter must surface
    # it so the follow-up row can be matched to a later status webhook.
    a = _adapter()

    class _Resp:
        status_code = 200
        text = '{"messages": [{"id": "wamid.ABC"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.ABC"}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    assert a.send_text("15551234567", "hello") == ["wamid.ABC"]


def test_deliver_due_followups_records_the_message_id(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)

    class _Resp:
        status_code = 200
        text = '{"messages": [{"id": "wamid.FU1"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.FU1"}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    a.deliver_due_followups()
    assert ("sent", 1) in agent.calls
    assert ("wa_id", 1, "wamid.FU1") in agent.calls


def test_deliver_due_followups_records_the_first_bubble_id(monkeypatch):
    # A long question goes out as several bubbles; the first bubble's id is the
    # one recorded, since outside the 24-hour window a 131047 fails them all and
    # the first status to arrive matches (#215).
    agent = _FakeAgent()
    a = _adapter(agent)
    monkeypatch.setattr(
        "agronaut_agent.channels.whatsapp_adapter.chunk",
        lambda text, size=4000: ["part one", "part two"],
    )
    sent_ids = ["wamid.FIRST", "wamid.SECOND"]

    class _Resp:
        status_code = 200
        text = '{"messages": [{"id": "wamid.X"}]}'

        def json(self):
            return {"messages": [{"id": sent_ids.pop(0)}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    a.deliver_due_followups()
    assert ("sent", 1) in agent.calls
    assert ("wa_id", 1, "wamid.FIRST") in agent.calls


def test_parse_status_updates_extracts_id_status_and_codes():
    a = _adapter()
    out = a.parse_status_updates(_status_payload())
    assert out == [("wamid.FU1", "failed", [131047])]
    assert a.parse_status_updates({}) == []
    # A status without an id or error codes is still a status, just not a failure.
    out = a.parse_status_updates(_status_payload(status="delivered", codes=()))
    assert out == [("wamid.FU1", "delivered", [])]


def test_handle_payload_routes_failed_status_to_followup():
    # A failed status must reach followup_status_failed with the message id and
    # Meta's error codes; the store decides what the record says.
    agent = _FakeAgent()
    a = _adapter(agent)
    a.handle_payload(_status_payload())
    assert ("status_failed", "wamid.FU1", [131047]) in agent.calls
    # ... and no reply was attempted for a status update (nothing inbound).
    assert agent.calls == [("status_failed", "wamid.FU1", [131047])]


def test_handle_payload_ignores_non_failed_statuses():
    agent = _FakeAgent()
    a = _adapter(agent)
    a.handle_payload(_status_payload(status="delivered", codes=()))
    assert not [c for c in agent.calls if c[0] == "status_failed"]


def test_handle_payload_cleans_up_attachments_when_reply_fails(monkeypatch, tmp_path):
    # An expired token makes the reply raise WhatsAppSendError; the handler must
    # not let it escape (which would skip the rest of the webhook batch) and the
    # attachment temp file must still be deleted (the leak #179 closed).
    png = tmp_path / "schematic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    agent = _FakeAgent(attachments=[str(png)])
    a = _adapter(agent)

    class _Resp:
        status_code = 401
        text = '{"error": {"message": "expired token"}}'

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    # Must not raise.
    a.handle_payload(_incoming_payload("draw my system"))
    assert not png.exists()


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


# --- the entrypoint -----------------------------------------------------------------------

def test_preflight_names_what_is_missing_and_why(monkeypatch):
    """The adapter reads os.environ directly, so without a preflight a missing token is a
    bare `KeyError: 'WHATSAPP_TOKEN'`. This is the step people get stuck on; the error has
    to say where in the Meta dashboard to look."""
    import whatsapp
    for k in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"):
        monkeypatch.delenv(k, raising=False)
    problems = whatsapp.preflight()
    assert len(problems) == 2
    joined = " ".join(problems)
    assert "WHATSAPP_TOKEN" in joined and "WHATSAPP_PHONE_NUMBER_ID" in joined
    assert "API Setup" in joined, "the message must say where to find it, not just that it is absent"


def test_preflight_passes_once_the_two_required_vars_exist(monkeypatch):
    import whatsapp
    monkeypatch.setenv("WHATSAPP_TOKEN", "x")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    assert whatsapp.preflight() == []


def test_the_signature_secret_is_recommended_not_required(monkeypatch):
    """Missing APP_SECRET must not block a first run, but it must warn: without it inbound
    signatures are unverified and anyone who learns the webhook URL can talk to the bot."""
    import whatsapp
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    assert any("WHATSAPP_APP_SECRET" in w for w in whatsapp.warnings())
    assert not any("WHATSAPP_APP_SECRET" in p for p in whatsapp.preflight())


def test_main_exits_nonzero_rather_than_starting_a_half_configured_server(monkeypatch, capsys):
    import whatsapp
    for k in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"):
        monkeypatch.delenv(k, raising=False)
    assert whatsapp.main() == 2
    assert "not configured yet" in capsys.readouterr().err


def test_the_cli_exposes_whatsapp_beside_bot():
    """Telegram had `agronaut bot` and WhatsApp had a three-line snippet in the README."""
    from agronaut_agent.cli import _build_parser
    actions = [a for a in _build_parser()._actions if hasattr(a, "choices") and a.choices]
    names = set()
    for a in actions:
        names.update(a.choices)
    assert "whatsapp" in names and "bot" in names


# --- the allowlist, in the form people actually type (found live 2026-09-30) ---------------

def test_an_allowed_number_written_with_plus_and_spaces_still_matches_meta_digits():
    """Setup accepted "+886 ..." while Meta sends "886...": every message was dropped."""
    a = _adapter(allowed_ids=["+886 912-345 678"])
    assert a._allowed("886912345678")


def test_a_number_not_on_the_list_is_dropped_out_loud(caplog):
    a = _adapter(allowed_ids=["886912345678"])
    with caplog.at_level("WARNING"):
        assert not a._allowed("22670000099")
    assert "DROPPED" in caplog.text and "ending 99" in caplog.text
    assert "22670000099" not in caplog.text


def test_describe_payload_names_kinds_never_content():
    msg = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "886912345678", "type": "text", "text": {"body": "my tilapia are gasping"}}]}}]}]}
    out = WhatsAppAdapter.describe_payload(msg)
    assert "1 message(s): text" in out
    assert "tilapia" not in out and "886912345678" not in out
    status = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    assert "status update" in WhatsAppAdapter.describe_payload(status)


def test_a_failed_status_names_metas_error():
    st = {"entry": [{"changes": [{"value": {"statuses": [{"status": "failed", "errors": [
        {"code": 131047, "title": "Re-engagement message"}]}]}}]}]}
    out = WhatsAppAdapter.describe_payload(st)
    assert "failed" in out and "131047" in out


def test_no_message_id_warning_logs_no_phone_number(monkeypatch, caplog):
    # #215 review: a successful send response carries the recipient's number in
    # contacts (input and wa_id) — the warning must not write it to the log.
    import logging

    a = _adapter()

    class _Resp:
        status_code = 200
        text = (
            '{"messages": [], "contacts": [{"input": "15551234567", "wa_id": "15551234567"}]}'
        )

        def json(self):
            return {
                "messages": [],
                "contacts": [{"input": "15551234567", "wa_id": "15551234567"}],
            }

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())
    with caplog.at_level(logging.WARNING):
        assert a.send_text("15551234567", "hello") == []
    assert "15551234567" not in caplog.text
