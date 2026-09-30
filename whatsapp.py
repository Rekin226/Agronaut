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
        "access token. Best: a permanent System User token (business.facebook.com > Settings "
        "> System users > Generate token, expiration Never). The test token from the app's "
        "Step 1. Try it out can die within an hour. Save either with `agronaut whatsapp "
        "--token`; docs/whatsapp_setup.md has every screen.",
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


def main(tunnel: bool = False) -> int:
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
    if not tunnel:
        log.info("starting WhatsApp webhook on port %d. Meta must reach it over HTTPS, so "
                 "point a tunnel or reverse proxy at it (or run `agronaut whatsapp "
                 "--tunnel` to start one here)", port)
        WhatsAppAdapter(AgronautAgent(), port=port).run()
        return 0
    return _run_with_tunnel(port, log)


def _run_with_tunnel(port: int, log) -> int:
    """Bot and public address in one process, and the values Meta needs printed on screen."""
    import threading

    from agronaut_agent import whatsapp_tunnel as T

    tun = T.QuickTunnel(port)
    try:
        base = tun.start()
    except T.TunnelUnavailable as err:
        print(f"\nCould not start the tunnel: {err}\n", file=sys.stderr)
        return 2
    verify = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    print(T.paste_card(base, verify), flush=True)

    def _self_check():
        if T.probe_until_reachable(base, verify):
            log.info("public address works: %s answers Meta's handshake. Paste it into Meta "
                     "now if you have not already.", T.webhook_url(base))
        else:
            log.warning("this computer could not reach %s yet. That is often only this "
                        "machine's DNS cache and Meta can reach it anyway: paste it and click "
                        "Verify and save. If Meta also fails, restart this command.",
                        T.webhook_url(base))

    threading.Thread(target=_self_check, daemon=True).start()
    import signal

    # A plain `kill` or a closed terminal sends SIGTERM, which skips `finally` unless it is
    # turned into an exit: that would leave cloudflared serving a dead address.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        WhatsAppAdapter(AgronautAgent(), port=port).run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        tun.stop()
        log.info("stopped the bot and the tunnel; the address above no longer works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
