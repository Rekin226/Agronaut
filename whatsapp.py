"""Agronaut WhatsApp bot entrypoint (Meta WhatsApp Cloud API).

Run:  python whatsapp.py          (or: agronaut whatsapp)

WhatsApp is webhook-based, not long-poll: Meta POSTs inbound messages to a public HTTPS URL
you register, so unlike the Telegram bot this process must be reachable from the internet.
In development that means a tunnel (`cloudflared tunnel --url http://localhost:8080` or
`ngrok http 8080`); in production, a reverse proxy with a real certificate.

Needs (in .env or the environment), all from the Meta app dashboard:
  WHATSAPP_TOKEN            access token for the WhatsApp product
  WHATSAPP_PHONE_NUMBER_ID  the SENDER's phone-number id (not the phone number itself)
  WHATSAPP_VERIFY_TOKEN     any string you invent; paste the same one into Meta's webhook form
  WHATSAPP_APP_SECRET       app secret, used to verify inbound request signatures
  AGRONAUT_ALLOWED_IDS      comma-separated sender numbers allowed to use the bot

Plus a model, the same as Telegram: LLM_PROVIDER=ollama with a pulled model, or a hosted key.
"""

from __future__ import annotations

import logging
import os
import sys

import agent  # noqa: F401 — importing the package loads project-root .env (agent/__init__.py)
from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
from agronaut_agent.core import AgronautAgent

# What each variable is and where in the Meta dashboard to find it. The adapter reads these
# straight from os.environ, so without this check a missing one surfaces as a bare
# `KeyError: 'WHATSAPP_TOKEN'` traceback — which says what is absent but not what to do,
# and this is the setup step people get stuck on.
REQUIRED = {
    "WHATSAPP_TOKEN":
        "access token — Meta app dashboard > WhatsApp > API Setup. The temporary one there "
        "expires in 24 h; generate a permanent token from a System User for anything real.",
    "WHATSAPP_PHONE_NUMBER_ID":
        "the SENDER's phone number ID, on the same API Setup page. It is a long number, not "
        "a phone number — do not paste the +country digits.",
}
RECOMMENDED = {
    "WHATSAPP_VERIFY_TOKEN":
        "any string you invent. Meta's webhook form asks for the same one, and the handshake "
        "fails silently if they differ.",
    "WHATSAPP_APP_SECRET":
        "Meta app dashboard > App settings > Basic. Without it inbound request signatures "
        "are NOT verified, so anyone who learns your webhook URL can speak to your bot.",
}


def preflight() -> list[str]:
    """Return human-readable problems, empty when the environment is usable."""
    problems = [f"{k} is not set — {why}" for k, why in REQUIRED.items() if not os.getenv(k)]
    return problems


def warnings() -> list[str]:
    return [f"{k} is not set — {why}" for k, why in RECOMMENDED.items() if not os.getenv(k)]


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("agronaut.whatsapp")

    problems = preflight()
    if problems:
        print("WhatsApp is not configured yet:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}\n", file=sys.stderr)
        print("Put them in a .env file in the project root, then run this again.\n"
              "Full walkthrough: README, 'Run on WhatsApp (Cloud API)'.", file=sys.stderr)
        return 2

    for w in warnings():
        log.warning("%s", w)
    if not os.getenv("AGRONAUT_ALLOWED_IDS"):
        log.warning("AGRONAUT_ALLOWED_IDS is empty, so ANY number that reaches the webhook "
                    "can use this bot and consume your model quota. Set it to your own "
                    "number(s) unless you mean to run it open.")

    port = int(os.getenv("WHATSAPP_PORT", "8080"))
    log.info("starting WhatsApp webhook on port %d — Meta must reach it over HTTPS, so "
             "point a tunnel or reverse proxy at it", port)
    WhatsAppAdapter(AgronautAgent(), port=port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
