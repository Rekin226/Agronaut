"""WhatsApp adapter (Meta WhatsApp Cloud API).

The channel smallholder-facing NGOs actually reach farmers on. Built on the same
ChannelAdapter contract as Telegram — the brain, tools, and memory are unchanged; this only
translates WhatsApp webhook events into agent.handle_message and sends replies back via the
Graph API.

WhatsApp is webhook-based (not long-poll): Meta POSTs inbound messages to a public HTTPS
URL you register, and you POST replies to the Graph API. This module uses stdlib http.server
(no extra web-framework dependency) and `requests` (already required). Follow-ups are
delivered by a background poller thread, since there's no JobQueue here.

Setup needs (from Meta / a WhatsApp Business account — owner-provided):
  WHATSAPP_TOKEN            permanent access token
  WHATSAPP_PHONE_NUMBER_ID  the sender phone-number id
  WHATSAPP_VERIFY_TOKEN     an arbitrary string you also enter in the webhook config
  WHATSAPP_APP_SECRET       app secret, to verify request signatures (recommended)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time

import requests

from ..core import AgronautAgent
from .base import ChannelAdapter, chunk, room_identity

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v20.0"
POLL_SECONDS = 60


class WhatsAppAdapter(ChannelAdapter):
    channel_name = "whatsapp"

    def __init__(self, agent: AgronautAgent, token: str | None = None,
                 phone_number_id: str | None = None, verify_token: str | None = None,
                 app_secret: str | None = None, host: str = "0.0.0.0", port: int = 8080,
                 allowed_ids=None):
        super().__init__(agent)
        self.token = token or os.environ["WHATSAPP_TOKEN"]
        self.phone_number_id = phone_number_id or os.environ["WHATSAPP_PHONE_NUMBER_ID"]
        self.verify_token = verify_token or os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        self.app_secret = app_secret or os.getenv("WHATSAPP_APP_SECRET")
        self.host, self.port = host, port
        self.allowed_ids = set(map(str, allowed_ids)) if allowed_ids is not None else \
            {x.strip() for x in (os.getenv("AGRONAUT_ALLOWED_IDS") or "").split(",") if x.strip()}

    # --- pure helpers (unit-tested) --------------------------------------
    def _allowed(self, sender: str) -> bool:
        return not self.allowed_ids or str(sender) in self.allowed_ids

    def verify_webhook(self, mode: str, token: str, challenge: str):
        """The GET verification handshake Meta performs when you register the webhook."""
        if mode == "subscribe" and token and token == self.verify_token:
            return challenge
        return None

    def verify_signature(self, body: bytes, signature_header: str | None) -> bool:
        """Validate Meta's X-Hub-Signature-256 (HMAC-SHA256 over the raw body). Without an
        app_secret configured we can't verify — treat as untrusted (False)."""
        if not self.app_secret or not signature_header:
            return False
        expected = "sha256=" + hmac.new(
            self.app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    def parse_incoming(self, payload: dict) -> list[tuple[str, str]]:
        """Extract [(sender_wa_id, text)] from a webhook payload, ignoring delivery-status
        events and non-text messages."""
        out: list[tuple[str, str]] = []
        for entry in (payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    if msg.get("type") == "text":
                        body = msg.get("text", {}).get("body")
                        sender = msg.get("from")
                        if body and sender:
                            out.append((str(sender), body))
        return out

    # --- outbound --------------------------------------------------------
    def send_text(self, to: str, text: str) -> None:
        for part in chunk(text):
            resp = requests.post(
                f"{GRAPH}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}",
                         "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": to,
                      "type": "text", "text": {"body": part}},
                timeout=30,
            )
            if resp.status_code >= 400:
                log.warning("whatsapp send failed (%s): %s", resp.status_code, resp.text[:200])

    # --- routing ---------------------------------------------------------
    def handle_payload(self, payload: dict) -> None:
        for sender, text in self.parse_incoming(payload):
            if not self._allowed(sender):
                continue
            uid = room_identity(sender, "private", sender)   # WhatsApp is 1:1 by number
            try:
                reply = self.agent.handle_message(self.channel_name, uid, text)
            except Exception:
                log.exception("agent.handle_message failed (whatsapp)")
                reply = "Something went wrong on my side. Try again, or rephrase?"
            self.send_text(sender, reply)

    def deliver_due_followups(self) -> None:
        try:
            due = self.agent.due_followups(self.channel_name)
        except Exception:
            log.debug("whatsapp follow-up poll failed", exc_info=True)
            return
        for fu in due:
            try:
                self.send_text(str(fu["channel_user"]), fu["question"])
                self.agent.mark_followup_sent(fu["id"])
            except Exception:
                log.warning("whatsapp follow-up send failed for %s", fu["id"], exc_info=True)
                self.agent.followup_send_failed(fu["id"])

    # --- server ----------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - exercised in a live deployment, not unit tests
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import urlparse, parse_qs

        adapter = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet default logging
                pass

            def do_GET(self):
                q = parse_qs(urlparse(self.path).query)
                challenge = adapter.verify_webhook(
                    q.get("hub.mode", [""])[0], q.get("hub.verify_token", [""])[0],
                    q.get("hub.challenge", [""])[0])
                if challenge is not None:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(challenge.encode())
                else:
                    self.send_response(403)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                if adapter.app_secret and not adapter.verify_signature(
                        body, self.headers.get("X-Hub-Signature-256")):
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)   # ack fast; Meta retries on non-200
                self.end_headers()
                try:
                    payload = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    return
                threading.Thread(target=adapter.handle_payload, args=(payload,),
                                 daemon=True).start()

        def _poller():
            while True:
                time.sleep(POLL_SECONDS)
                adapter.deliver_due_followups()

        threading.Thread(target=_poller, daemon=True).start()
        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        scope = f"{len(self.allowed_ids)} allowed number(s)" if self.allowed_ids else "OPEN"
        log.info("Agronaut WhatsApp webhook listening on %s:%s — %s", self.host, self.port, scope)
        server.serve_forever()
