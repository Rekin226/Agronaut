"""WhatsApp adapter (Meta WhatsApp Cloud API).

The channel smallholder-facing NGOs actually reach farmers on. Built on the same
ChannelAdapter contract as Telegram — the brain, tools, and memory are unchanged; this only
translates WhatsApp webhook events into agent.handle_message / handle_image / handle_voice and sends
replies back via the Graph API. Inbound photos and voice notes route through the same seams Telegram
uses, so the observation guard and cited tools apply identically here.

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
import mimetypes
import os
import threading
import time

import requests

from ..core import AgronautAgent
from ..style import to_bubbles
from . import commands
from .base import ChannelAdapter, chunk, room_identity

log = logging.getLogger(__name__)


def _digits(value) -> str:
    """A phone number as Meta writes it: digits only, no "+", spaces or dashes."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _same_but_trunk_zero(allowed: str, sender: str) -> bool:
    """True when `allowed` is `sender` with one extra 0 just after a 1-4 digit country code."""
    if len(allowed) != len(sender) + 1:
        return False
    return any(allowed[i] == "0" and allowed[:i] + allowed[i + 1:] == sender
               for i in (1, 2, 3, 4) if i < len(allowed))

GRAPH = "https://graph.facebook.com/v20.0"
POLL_SECONDS = 60


class WhatsAppSendError(RuntimeError):
    """The Graph API refused (or failed) a WhatsApp send. Raised by send_text on
    HTTP 4xx/5xx so callers can tell 'never arrived' apart from 'sent' — silently
    swallowing HTTP errors once marked follow-ups sent when they had not been (#215)."""


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
        raw_ids = list(map(str, allowed_ids)) if allowed_ids is not None else \
            [x for x in (os.getenv("AGRONAUT_ALLOWED_IDS") or "").split(",") if x.strip()]
        # Digits only, because Meta sends `from` as digits ("88691...") while people write
        # "+886 912 ..." in .env, and setup accepted that form. Compared as typed, every
        # message from the one allowed number was dropped without a word.
        self.allowed_ids = {_digits(x) for x in raw_ids if _digits(x)}

    # --- pure helpers (unit-tested) --------------------------------------
    def _allowed(self, sender: str) -> bool:
        if not self.allowed_ids or _digits(sender) in self.allowed_ids:
            return True
        # The second silent drop of 2026-09-30: "8860912..." in .env, "886912..." from Meta.
        # A local trunk 0 kept after the country code is tolerated, and said, because the
        # allowlist gates quota, not identity (Meta's signature already vouches for sender).
        for allowed in self.allowed_ids:
            if _same_but_trunk_zero(allowed, _digits(sender)):
                log.info("allowed number in .env keeps a local trunk 0 after the country "
                         "code; matched anyway. Remove that 0 in AGRONAUT_ALLOWED_IDS.")
                return True
        # Said out loud: a silent drop here cost an afternoon of "the bot does not answer".
        # Only the last two digits, so the log does not become a list of who wrote in.
        log.warning("message from a number ending %s DROPPED: not in AGRONAUT_ALLOWED_IDS "
                    "(%d allowed). Add it in full international form, digits only.",
                    _digits(sender)[-2:] or "??", len(self.allowed_ids))
        return False

    @staticmethod
    def describe_payload(payload: dict) -> str:
        """What a webhook POST carried, by kind only: never the text, never a full number."""
        kinds, statuses = [], []
        for entry in (payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                kinds += [str(m.get("type")) for m in value.get("messages", [])]
                for st in value.get("statuses", []):
                    # The status and Meta's error code/title say WHY an outbound message
                    # failed (e.g. 131047, the 24-hour window), which is the whole point.
                    errs = "; ".join(f"error {e.get('code')}: {e.get('title')}"
                                     for e in st.get("errors", []) or [])
                    statuses.append(f"{st.get('status')}" + (f" ({errs})" if errs else ""))
        if not kinds and not statuses:
            return "no messages and no status updates (nothing to answer)"
        parts = []
        if kinds:
            parts.append(f"{len(kinds)} message(s): {', '.join(kinds)}")
        if statuses:
            parts.append(f"{len(statuses)} delivery status update(s) for a message the "
                         f"number SENT, nothing to answer: {', '.join(statuses)}")
        return "; ".join(parts)

    def verify_webhook(self, mode: str, token: str, challenge: str):
        """The GET verification handshake Meta performs when you register the webhook."""
        if mode == "subscribe" and token and token == self.verify_token:
            return challenge
        return None

    def describe_token_mismatch(self, received: str) -> str:
        """Why a verify token was refused, without ever saying what either token is.

        A bare "did not match" left the maintainer pasting into Meta's form four times with
        no idea what was wrong. The usual causes are a stale token from an earlier setup run,
        or one of the OTHER WhatsApp secrets pasted into the verify field, and both are
        visible from shape alone: length, and the prefix Meta gives its access tokens.
        """
        expected = self.verify_token or ""
        if not expected:
            return "WHATSAPP_VERIFY_TOKEN is not set, so nothing can match"
        if not received:
            return "Meta sent an empty verify token"
        if received.strip() == expected:
            return "it matches once surrounding spaces are removed: re-paste it without them"
        if received.lower() == expected.lower():
            return "same letters, different capitals"
        if received.startswith("EAA"):
            return ("that looks like the WhatsApp ACCESS token (starts EAA), not the verify "
                    "token: paste WHATSAPP_VERIFY_TOKEN from .env instead")
        if self.app_secret and received == self.app_secret:
            return "that is the APP SECRET, not the verify token"
        return (f"Meta sent {len(received)} characters, .env has {len(expected)}: a different "
                "token, probably one from an earlier setup run")

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

    def parse_incoming_images(self, payload: dict) -> list[tuple[str, str, str | None]]:
        """Extract [(sender_wa_id, media_id, caption)] for inbound photos.

        Two shapes count as a photo: a native `image` message, and a `document` whose
        mime_type is an image (what you get when someone sends an uncompressed file) —
        mirroring the Telegram adapter, which accepts both. Anything else is left alone so
        handle_payload can decline it honestly rather than guess at the contents."""
        out: list[tuple[str, str, str | None]] = []
        for entry in (payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    kind = msg.get("type")
                    if kind == "image":
                        media = msg.get("image", {})
                    elif kind == "document" and str(
                            msg.get("document", {}).get("mime_type", "")).startswith("image/"):
                        media = msg.get("document", {})
                    else:
                        continue
                    media_id, sender = media.get("id"), msg.get("from")
                    if media_id and sender:
                        out.append((str(sender), str(media_id), media.get("caption")))
        return out

    def parse_incoming_audio(self, payload: dict) -> list[tuple[str, str, str]]:
        """Extract [(sender_wa_id, media_id, mime)] for inbound voice notes.

        Three shapes count: `audio`, `voice`, and a `document` whose mime is audio. This is
        the channel where voice matters most — it is what reaches an operator who would
        rather talk than type."""
        out: list[tuple[str, str, str]] = []
        for entry in (payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    kind = msg.get("type")
                    if kind in {"audio", "voice"}:
                        media = msg.get(kind, {})
                    elif kind == "document" and str(
                            msg.get("document", {}).get("mime_type", "")).startswith("audio/"):
                        media = msg.get("document", {})
                    else:
                        continue
                    media_id, sender = media.get("id"), msg.get("from")
                    if media_id and sender:
                        out.append((str(sender), str(media_id),
                                    str(media.get("mime_type") or "audio/ogg")))
        return out

    def parse_unsupported_files(self, payload: dict) -> list[tuple[str, str]]:
        """[(sender, filename_or_type)] for inbound files we deliberately do NOT read —
        a PDF, a spreadsheet, a video. Surfaced so the user is told what we can read
        instead of being met with silence."""
        out: list[tuple[str, str]] = []
        for entry in (payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    kind = msg.get("type")
                    if kind in {"text", "image", "audio", "voice"}:
                        continue          # all handled elsewhere
                    doc = msg.get(kind or "", {}) if isinstance(msg.get(kind or ""), dict) else {}
                    mime = str(doc.get("mime_type", ""))
                    if kind == "document" and mime.startswith(("image/", "audio/")):
                        continue          # those ARE readable — handled as photo / voice note
                    sender = msg.get("from")
                    if sender and kind:
                        out.append((str(sender), str(doc.get("filename") or kind)))
        return out

    @staticmethod
    def parse_status_updates(payload: dict) -> list[tuple[str, str, list[int]]]:
        """[(message_id, status, error_codes)] for outbound delivery updates.

        Pure and unit-tested. The message id is Meta's handle for a send this number
        made — it is what links a later `failed` status back to the follow-up it
        belongs to (#215). Error codes are Meta's numeric reasons (e.g. 131047, the
        24-hour re-engagement window). Never the text, never a phone number."""
        out: list[tuple[str, str, list[int]]] = []
        for entry in (payload or {}).get("entry", []):
            for change in entry.get("changes", []):
                for st in change.get("value", {}).get("statuses", []):
                    mid = st.get("id")
                    status = st.get("status")
                    if not mid or not status:
                        continue
                    codes = []
                    for e in st.get("errors", []) or []:
                        try:
                            codes.append(int(e.get("code")))
                        except (TypeError, ValueError):
                            continue
                    out.append((str(mid), str(status), codes))
        return out

    # --- inbound media ---------------------------------------------------
    def download_media(self, media_id: str) -> bytes | None:
        """Fetch inbound media bytes. The Cloud API needs two authenticated hops: look the
        id up to get a short-lived lookaside URL, then fetch that URL — the second hop needs
        the bearer token too, which is easy to miss. Returns None on any failure so a photo
        we cannot fetch degrades to an honest reply instead of an exception."""
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            look = requests.get(f"{GRAPH}/{media_id}", headers=headers, timeout=30)
            if look.status_code >= 400:
                log.warning("whatsapp media lookup failed (%s): %s",
                            look.status_code, look.text[:200])
                return None
            url = look.json().get("url")
            if not url:
                return None
            blob = requests.get(url, headers=headers, timeout=60)
            if blob.status_code >= 400:
                log.warning("whatsapp media fetch failed (%s)", blob.status_code)
                return None
            return blob.content
        except Exception:
            log.warning("whatsapp media download failed", exc_info=True)
            return None

    # --- outbound --------------------------------------------------------
    def send_reply(self, to: str, text: str) -> None:
        """Send a MODEL reply as a few short bubbles, the way a person texts. Fixed texts
        (command output, error lines) go through send_text as one message."""
        for bubble in to_bubbles(text, self.channel_name):
            self.send_text(to, bubble)

    def send_text(self, to: str, text: str) -> list[str]:
        """Send plain text, chunked into bubbles. Returns the WhatsApp message id
        of each sent bubble, in order (empty if a response carried none). Raises
        WhatsAppSendError when the Graph API answers 4xx/5xx; network errors from
        requests propagate as-is."""
        ids: list[str] = []
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
                raise WhatsAppSendError(
                    f"whatsapp send failed ({resp.status_code}): {resp.text[:200]}")
            try:
                mid = (resp.json().get("messages") or [{}])[0].get("id")
            except (ValueError, AttributeError, IndexError, TypeError):
                mid = None
            # The id is Meta's handle for this exact send; without it a later
            # delivery-status webhook (e.g. 131047) cannot be matched back (#215).
            # Never the text, never the number — just the id.
            if mid:
                ids.append(str(mid))
            else:
                # A successful send response carries the recipient's number in
                # contacts (input and wa_id): never log the body here, the
                # status code alone says the send worked but left no handle (#215).
                log.warning("whatsapp send response carried no message id (status %s)",
                            resp.status_code)
        return ids

    def send_media(self, to: str, path: str, mime: str = "image/png") -> bool:
        """Send a local file (e.g. a rendered schematic or a 3D scene). WhatsApp Cloud API
        is two steps: upload the media to get an id, then send a message referencing it.
        Returns True on success, False on upload or message failure."""
        try:
            with open(path, "rb") as fh:
                up = requests.post(
                    f"{GRAPH}/{self.phone_number_id}/media",
                    headers={"Authorization": f"Bearer {self.token}"},
                    files={"file": (os.path.basename(path), fh, mime)},
                    data={"messaging_product": "whatsapp", "type": mime},
                    timeout=60,
                )
        except Exception:
            log.warning("whatsapp media upload failed for %s", path, exc_info=True)
            return False

        if up.status_code >= 400:
            log.warning("whatsapp media upload failed (%s): %s", up.status_code, up.text[:200])
            return False

        media_id = up.json().get("id")
        if not media_id:
            log.warning("whatsapp media upload missing id: %s", up.text[:200])
            return False

        kind = "image" if mime.startswith("image/") else "document"
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": kind,
            kind: {"id": media_id},
        }
        if kind == "document":
            body[kind]["filename"] = os.path.basename(path)

        try:
            resp = requests.post(
                f"{GRAPH}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            if resp.status_code >= 400:
                log.warning("whatsapp media send failed (%s): %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception:
            log.warning("whatsapp media send failed for %s", path, exc_info=True)
            return False

    # --- routing ---------------------------------------------------------
    def handle_payload(self, payload: dict) -> None:
        # Delivery statuses first: a `failed` status for a message id this number sent
        # may belong to a follow-up, and the record should say so (#215). A status for
        # anything else changes nothing.
        for wa_id, status, codes in self.parse_status_updates(payload):
            if status != "failed":
                continue
            try:
                self.agent.followup_status_failed(wa_id, codes)
            except Exception:
                log.warning("whatsapp follow-up status handling failed", exc_info=True)

        for sender, text in self.parse_incoming(payload):
            if not self._allowed(sender):
                continue
            uid = room_identity(sender, "private", sender)   # WhatsApp is 1:1 by number

            # Slash commands first. WhatsApp has no command menu — the Cloud API delivers
            # "/log ammonia 0.5" as ordinary text — so without this every message went to
            # the LLM and the whole deterministic command layer was missing on the channel
            # most operators actually use. These are the paths with no model in them.
            try:
                cmd = commands.dispatch(self.agent, self.channel_name, uid, text)
            except Exception:
                log.exception("slash command failed (whatsapp)")
                cmd = commands.Reply("That command didn't work just now. Try again?")
            if cmd is not None:
                try:
                    for part in chunk(cmd.text):
                        self.send_text(sender, part)
                except WhatsAppSendError:
                    log.warning("whatsapp command reply failed for %s", sender, exc_info=True)
                if cmd.document:
                    doc_mime = cmd.document_mime or mimetypes.guess_type(cmd.document)[0] or "application/octet-stream"
                    if self.send_media(sender, cmd.document, doc_mime) is False:
                        self._notify_send_failed(sender, cmd.document)
                continue

            try:
                reply = self.agent.handle_message(self.channel_name, uid, text)
            except Exception:
                log.exception("agent.handle_message failed (whatsapp)")
                reply = "Something went wrong on my side. Try again, or rephrase?"
            try:
                self.send_reply(sender, reply)
            except WhatsAppSendError:
                log.warning("whatsapp reply failed for %s", sender, exc_info=True)
            finally:
                self._flush_attachments(sender, uid)

        # Photos: the same agent seam Telegram uses, so the observation guard, memory, and
        # cited tools all apply identically on this channel.
        for sender, media_id, caption in self.parse_incoming_images(payload):
            if not self._allowed(sender):
                continue
            uid = room_identity(sender, "private", sender)
            image_bytes = self.download_media(media_id)
            if not image_bytes:
                try:
                    self.send_text(sender, "I couldn't download that photo. Could you send it "
                                           "again, or describe what you see?")
                except WhatsAppSendError:
                    log.warning("whatsapp download-failure notice failed for %s", sender, exc_info=True)
                continue
            try:
                reply = self.agent.handle_image(self.channel_name, uid, image_bytes, caption)
            except Exception:
                log.exception("agent.handle_image failed (whatsapp)")
                reply = "Something went wrong reading that photo. Try again, or describe what you see?"
            try:
                self.send_reply(sender, reply)
            except WhatsAppSendError:
                log.warning("whatsapp reply failed for %s", sender, exc_info=True)
            finally:
                self._flush_attachments(sender, uid)

        # Voice notes: the same agent seam Telegram uses, so the transcript runs through a
        # normal turn with memory, tools and cited knowledge intact.
        for sender, media_id, mime in self.parse_incoming_audio(payload):
            if not self._allowed(sender):
                continue
            uid = room_identity(sender, "private", sender)
            audio_bytes = self.download_media(media_id)
            if not audio_bytes:
                try:
                    self.send_text(sender, "I couldn't download that voice note. Could you send "
                                           "it again, or type your message?")
                except WhatsAppSendError:
                    log.warning("whatsapp download-failure notice failed for %s", sender, exc_info=True)
                continue
            try:
                reply = self.agent.handle_voice(self.channel_name, uid, audio_bytes, mime)
            except Exception:
                log.exception("agent.handle_voice failed (whatsapp)")
                reply = ("Something went wrong with that voice note. Try again, or type your "
                         "message?")
            try:
                self.send_reply(sender, reply)
            except WhatsAppSendError:
                log.warning("whatsapp reply failed for %s", sender, exc_info=True)
            finally:
                self._flush_attachments(sender, uid)

        for sender, what in self.parse_unsupported_files(payload):
            if not self._allowed(sender):
                continue
            log.info("whatsapp: declined unsupported inbound %r", what)
            try:
                self.send_text(sender, "I can't read files like that yet. I work with text and "
                                       "photos. Send a photo of the plants, fish, or water and "
                                       "I'll take a look.")
            except WhatsAppSendError:
                log.warning("whatsapp unsupported-file notice failed for %s", sender, exc_info=True)

    def _flush_attachments(self, sender: str, uid: str) -> None:
        for path in self.agent.take_attachments(self.channel_name, uid):
            try:
                if path.lower().endswith((".html", ".htm")):
                    self.send_text(
                        sender,
                        "The 3D view isn't available on WhatsApp yet (Telegram has it).",
                    )
                else:
                    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                    if self.send_media(sender, path, mime=mime) is False:
                        self._notify_send_failed(sender, path)
            except Exception:
                log.warning("whatsapp media send failed for %s", path, exc_info=True)
                self._notify_send_failed(sender, path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _notify_send_failed(self, sender: str, path: str) -> None:
        """Tell the grower an attachment didn't arrive. Never raises.

        It runs exactly when sending is already failing, often because the network is down,
        and `send_text` does not catch its own errors. Left unguarded, the notice raised
        out of `_flush_attachments`, so the attachments after it were never tried and
        their temp files were never deleted: the leak #124 fixed, back by another door.
        """
        filename = os.path.basename(path)
        try:
            self.send_text(sender, f"I couldn't send the attachment ({filename}), "
                                   "try asking again?")
        except Exception:
            log.warning("whatsapp could not send the failure notice for %s", filename,
                        exc_info=True)

    def deliver_due_followups(self) -> None:
        try:
            due = self.agent.due_followups(self.channel_name)
        except Exception:
            log.debug("whatsapp follow-up poll failed", exc_info=True)
            return
        for fu in due:
            try:
                ids = self.send_text(str(fu["channel_user"]), fu["question"])
                self.agent.mark_followup_sent(fu["id"])
            except Exception:
                log.warning("whatsapp follow-up send failed for %s", fu["id"], exc_info=True)
                self.agent.followup_send_failed(fu["id"])
                continue
            # Best-effort and separate from the send above: the id is the handle a
            # later delivery-status webhook will carry, so without it a 131047
            # (24-hour window) failure can never be matched back to this follow-up
            # (#215). The first bubble's id: outside the window a 131047 fails all
            # bubbles, so the first one sent is enough to match on. If recording
            # fails, the send still counts — the follow-up just keeps the old
            # behaviour of having no status linkage.
            try:
                self.agent.record_followup_message_id(fu["id"], ids[0] if ids else None)
            except Exception:
                log.warning("whatsapp could not record message id for follow-up %s",
                            fu["id"], exc_info=True)

    # --- server ----------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - exercised in a live deployment, not unit tests
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse

        adapter = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                # BaseHTTPRequestHandler's default access log is noise, but total silence
                # is worse: when Meta stops delivering, an operator sees a running process,
                # a green webhook in the dashboard, and nothing else to distinguish
                # "no message arrived" from "a message arrived and was dropped". Every
                # inbound request is now logged at INFO, with a reason for each rejection.
                pass

            def do_GET(self):
                log.info("webhook GET from %s", self.client_address[0])
                q = parse_qs(urlparse(self.path).query)
                challenge = adapter.verify_webhook(
                    q.get("hub.mode", [""])[0], q.get("hub.verify_token", [""])[0],
                    q.get("hub.challenge", [""])[0])
                if challenge is not None:
                    log.info("webhook verification OK — echoing challenge")
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(challenge.encode())
                else:
                    log.warning("webhook verification REFUSED: hub.verify_token did not "
                                "match WHATSAPP_VERIFY_TOKEN (%s)",
                                adapter.describe_token_mismatch(
                                    q.get("hub.verify_token", [""])[0]))
                    self.send_response(403)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                log.info("webhook POST from %s, %d bytes", self.client_address[0], length)
                if adapter.app_secret and not adapter.verify_signature(
                        body, self.headers.get("X-Hub-Signature-256")):
                    log.warning("webhook POST REJECTED — bad X-Hub-Signature-256. The "
                                "WHATSAPP_APP_SECRET does not match the app Meta signed "
                                "with; a message DID arrive and was dropped.")
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)   # ack fast; Meta retries on non-200
                self.end_headers()
                try:
                    payload = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    log.warning("webhook POST body was not JSON; ignored")
                    return
                log.info("webhook POST carried %s", adapter.describe_payload(payload))
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
