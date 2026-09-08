"""Check a WhatsApp setup end to end, and fix the one thing Meta's dashboard hides.

WhatsApp has more moving parts than Telegram, and most of them fail SILENTLY. A setup can
look complete in Meta's UI, answer the webhook handshake correctly, and still deliver every
message somewhere you will never see. There is no error anywhere; the bot simply never
replies, which tells an operator nothing about which of eight things is wrong.

The worst offender, and the reason this module exists: Meta's dashboard shows
"Configure Webhooks ✅" once the callback URL verifies, but verifying the URL does NOT
subscribe your app to your own WhatsApp Business Account. On a fresh account the WABA is
subscribed to Meta's internal "WA DevX Webhook Events 1P App" instead, so inbound messages
go there. The dashboard offers no way to see this and no way to change it: the only route
is POST /{waba-id}/subscribed_apps. Someone can lose an afternoon to it, because every
visible indicator says the setup is finished.

So this runs the checks in dependency order (a bad token makes every later check
meaningless) and reports the FIRST thing that is actually wrong, with the fix.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 20

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}


@dataclass
class Check:
    status: str
    label: str
    detail: str = ""
    fix: str = ""

    def render(self) -> str:
        line = f"[{_MARK[self.status]}] {self.label}"
        if self.detail:
            line += f"\n       {self.detail}"
        if self.fix:
            line += f"\n       fix: {self.fix}"
        return line


def _get(url: str, token: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
            return f.status, json.load(f)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}
    except Exception as e:  # noqa: BLE001 — a network blip is a finding, not a crash
        return 0, {"error": {"message": str(e)}}


def _err(body: dict) -> str:
    e = body.get("error") or {}
    code = e.get("code")
    msg = e.get("message", "unknown error")
    return f"({code}) {msg}" if code else msg


def run_checks(*, subscribe: bool = False, public_url: str | None = None) -> list[Check]:
    """Check the chain in dependency order. `subscribe` repairs the WABA subscription."""
    out: list[Check] = []
    token = (os.getenv("WHATSAPP_TOKEN") or "").strip()
    phone_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    secret = (os.getenv("WHATSAPP_APP_SECRET") or "").strip()
    allowed = [x.strip() for x in (os.getenv("AGRONAUT_ALLOWED_IDS") or "").split(",") if x.strip()]

    if not token:
        out.append(Check(FAIL, "WHATSAPP_TOKEN is not set",
                         fix="Meta app dashboard > WhatsApp > API Setup, copy the access token"))
        return out
    if not phone_id:
        out.append(Check(FAIL, "WHATSAPP_PHONE_NUMBER_ID is not set",
                         fix="same page as the token; it is an id, not a phone number"))
        return out

    # 1. The token, and the number it belongs to. Everything below depends on this.
    status, body = _get(f"{GRAPH}/{phone_id}?fields=display_phone_number,verified_name", token)
    if status != 200:
        out.append(Check(FAIL, "token rejected by the Graph API", _err(body),
                         "the API Setup token expires in 24 h — generate a new one"))
        return out
    number = body.get("display_phone_number", "?")
    out.append(Check(OK, f"token valid — {body.get('verified_name','?')} {number}"))

    # 2. Which WhatsApp Business Account this number belongs to. The phone-number node does
    #    not expose it (v21 returns "nonexisting field"), so it comes from config. Meta
    #    prints it directly beside the token on the API Setup page.
    waba = (os.getenv("WHATSAPP_WABA_ID") or "").strip()
    if not waba:
        out.append(Check(
            FAIL, "WHATSAPP_WABA_ID is not set",
            "without it the subscription check cannot run, and that check is the one that "
            "catches the failure Meta's dashboard hides.",
            "copy 'WhatsApp Business account ID' from WhatsApp > API Setup into .env"))
        return out
    out.append(Check(OK, f"WhatsApp Business Account {waba}"))

    # 3. The silent killer. Verifying the callback URL does not subscribe the app.
    status, body = _get(f"{GRAPH}/{waba}/subscribed_apps", token)
    if status != 200:
        out.append(Check(WARN, "could not read the WABA's subscribed apps", _err(body)))
    else:
        apps = [(a.get("whatsapp_business_api_data") or {}) for a in body.get("data", [])]
        names = {a.get("id"): a.get("name") for a in apps}
        mine = [i for i in names if i and i not in ("2202427980234937",)]
        if mine:
            out.append(Check(OK, "this app is subscribed to the WABA — "
                                 f"{', '.join(f'{names[i]} ({i})' for i in mine)}"))
        elif subscribe:
            s2, b2 = _get(f"{GRAPH}/{waba}/subscribed_apps", token, method="POST")
            if s2 == 200 and b2.get("success"):
                out.append(Check(OK, "app was NOT subscribed to the WABA — subscribed it now"))
            else:
                out.append(Check(FAIL, "could not subscribe the app to the WABA", _err(b2)))
        else:
            out.append(Check(
                FAIL, "this app is NOT subscribed to your WhatsApp Business Account",
                "inbound messages are being delivered to Meta's internal app, not to you. "
                "Meta's dashboard still shows 'Configure Webhooks' as complete, which is why "
                "this is so easy to miss.",
                "agronaut whatsapp --subscribe"))

    # 4. Is the webhook actually reachable from the internet? Meta must reach YOU.
    if public_url:
        verify = (os.getenv("WHATSAPP_VERIFY_TOKEN") or "").strip()
        probe = (f"{public_url.rstrip('/')}/?hub.mode=subscribe"
                 f"&hub.verify_token={verify}&hub.challenge=agronaut_probe")
        try:
            with urllib.request.urlopen(probe, timeout=TIMEOUT) as f:
                echoed = f.read().decode().strip()
            if echoed == "agronaut_probe":
                out.append(Check(OK, f"webhook reachable and answering at {public_url}"))
            else:
                out.append(Check(FAIL, "webhook reachable but did not echo the challenge",
                                 f"got {echoed[:60]!r}",
                                 "WHATSAPP_VERIFY_TOKEN here must match Meta's webhook config"))
        except Exception as e:  # noqa: BLE001
            out.append(Check(FAIL, f"webhook NOT reachable at {public_url}", str(e),
                             "is the tunnel running, and is `agronaut whatsapp` up?"))

    # 5. Signature verification. Not fatal, but it is the only thing stopping a stranger
    #    who learns the URL from talking to the bot.
    if secret:
        out.append(Check(OK, "app secret set — inbound signatures are verified"))
    else:
        out.append(Check(WARN, "WHATSAPP_APP_SECRET not set — inbound signatures NOT verified",
                         "anyone who learns your webhook URL can speak to your bot",
                         "Meta app dashboard > App settings > Basic > App secret"))

    # 6. The allowlist. A message that arrives and is refused looks identical, to the
    #    operator, to a message that never arrived at all.
    if not allowed:
        out.append(Check(WARN, "AGRONAUT_ALLOWED_IDS is empty — the bot is open to anyone",
                         fix="set it to your own number, digits only, no +"))
    else:
        digits = [a for a in allowed if a.isdigit() and len(a) >= 8]
        out.append(Check(OK if digits else WARN,
                         f"allowlist has {len(allowed)} entry(ies)",
                         "" if digits else
                         "none of them look like a phone number. Telegram ids live in the "
                         "same variable, so a WhatsApp number must be added alongside them, "
                         "or your messages arrive and are silently refused.",
                         "" if digits else "append your WhatsApp number, digits only, no +"))
    return out


def report(checks: list[Check]) -> tuple[str, int]:
    """Render the checks and an exit code: 0 clean, 1 something is broken."""
    lines = [c.render() for c in checks]
    worst = FAIL if any(c.status == FAIL for c in checks) else (
        WARN if any(c.status == WARN for c in checks) else OK)
    if worst == FAIL:
        lines.append("\nSomething above is broken — the bot will not reply until it is fixed.")
    elif worst == WARN:
        lines.append("\nUsable, with the warnings above.")
    else:
        lines.append("\nAll good. Message your WhatsApp number and the bot should answer.")
    return "\n".join(lines), (1 if worst == FAIL else 0)


def set_callback_url(url: str) -> tuple[bool, str]:
    """Point Meta's webhook at `url` and subscribe the `messages` field.

    Two things Meta keeps separate, and a setup is dead unless BOTH are right:
    the callback URL, and which event types the app wants. A subscription can sit there
    with `active: true`, a correct URL, and an empty `fields` list — the dashboard shows a
    green tick and not one message is ever delivered. That exact state is what made this
    bot silent for an afternoon, so this call always sends `fields` too.

    Uses the APP access token (`{app_id}|{app_secret}`), not the user token: the app token
    does not expire, so re-registering after a tunnel restart never depends on whether
    today's 24-hour token is still alive.
    """
    app_id = (os.getenv("WHATSAPP_APP_ID") or "").strip()
    secret = (os.getenv("WHATSAPP_APP_SECRET") or "").strip()
    verify = (os.getenv("WHATSAPP_VERIFY_TOKEN") or "").strip()
    if not (app_id and secret):
        return False, "WHATSAPP_APP_ID and WHATSAPP_APP_SECRET are both needed to re-register"

    import urllib.parse
    body = urllib.parse.urlencode({
        "object": "whatsapp_business_account",
        "callback_url": url,
        "verify_token": verify,
        "fields": "messages",
        "access_token": f"{app_id}|{secret}",
    }).encode()
    req = urllib.request.Request(f"{GRAPH}/{app_id}/subscriptions", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
            ok = json.load(f).get("success") is True
        return (ok, f"webhook registered at {url}" if ok else "Meta did not confirm success")
    except urllib.error.HTTPError as e:
        try:
            detail = _err(json.loads(e.read().decode() or "{}"))
        except ValueError:
            detail = f"HTTP {e.code}"
        return False, f"Meta refused the webhook: {detail}"
    except Exception as e:  # noqa: BLE001
        return False, f"could not reach Meta: {e}"
