"""WhatsApp Cloud API adapter, verified without a live Meta number or a running server.
The adapter is built on the same ChannelAdapter contract as Telegram; these tests exercise
its pure pieces (webhook parse, verify handshake, signature check) and its routing to the
agent + outbound send (with requests mocked).
"""

import hashlib
import hmac
import json

from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
from agronaut_agent.channels import base


class _FakeAgent:
    def __init__(self):
        self.calls = []

    def handle_message(self, channel, channel_user, text, display_name=None):
        self.calls.append((channel, channel_user, text))
        return f"reply to {text}"

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


def test_deliver_due_followups_sends_and_marks(monkeypatch):
    agent = _FakeAgent()
    a = _adapter(agent)
    sent = []
    monkeypatch.setattr(a, "send_text", lambda to, text: sent.append((to, text)))
    a.deliver_due_followups()
    assert sent == [("15551234567", "did it work?")]
    assert ("sent", 1) in agent.calls
