"""`agronaut whatsapp --tunnel`: the bot and its public address in one command.

WhatsApp is the one channel where Meta has to reach the grower's machine over HTTPS. On a
laptop that meant a second terminal running `cloudflared`, reading a random address out of
its log, adding `/webhook`, and pasting it into Meta beside a token from a different screen.
Every one of those steps failed at least once in the session that got WhatsApp to answer for
the first time (2026-09-30): the tunnel tab was closed and its address died, the address was
saved without `/webhook`, an old tunnel's address stayed saved in Meta, and the verify token
in the form was a stale one.

So this starts the tunnel itself, waits for its address, and prints the two values Meta
needs, labelled with the exact screen they go on. The address still changes on every start
(that is what a free quick tunnel is), which is why it is printed every time rather than
saved anywhere.
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Where Meta keeps the webhook form as of 2026-09-30. Meta has moved it before (it used to
# be WhatsApp > Configuration), so this is the one line to update when it moves again.
META_WEBHOOK_SCREEN = ("developers.facebook.com > My Apps > your app > Use cases > "
                       "Connect on WhatsApp > Customize > Step 2. Production setup > "
                       "Configure Webhooks")


class TunnelUnavailable(RuntimeError):
    """cloudflared is missing or never produced an address."""


def install_hint() -> str:
    return ("cloudflared is not installed. On a Mac: `brew install cloudflared`. "
            "Elsewhere: https://developers.cloudflare.com/cloudflare-one/connections/"
            "connect-networks/downloads/ . Or run your own tunnel and use "
            "`agronaut whatsapp` without --tunnel.")


def parse_tunnel_url(line: str) -> str | None:
    """The public address in one line of cloudflared's output, or None."""
    m = _URL.search(line or "")
    return m.group(0) if m else None


def webhook_url(base: str) -> str:
    return base.rstrip("/") + "/webhook"


def paste_card(base_url: str, verify_token: str) -> str:
    """The two values Meta's form needs, and nothing that could be confused with them.

    The access token is named here only to say it does NOT go in this form: mixing the two
    up is the mistake that cost the most time.
    """
    url = webhook_url(base_url)
    width = max(len(url), len(verify_token)) + 18
    bar = "  " + "-" * width
    return "\n".join([
        "",
        bar,
        "  Paste these into Meta, then click 'Verify and save':",
        f"    {META_WEBHOOK_SCREEN}",
        "",
        f"    Callback URL   {url}",
        f"    Verify token   {verify_token or '(WHATSAPP_VERIFY_TOKEN is not set: run agronaut setup)'}",
        "",
        "  The verify token is NOT the long access token (EAA...) from Step 1.",
        "  Check that 'messages' is Subscribed under Webhook fields on the same screen.",
        "  This address changes every time you start the tunnel, so paste it again after",
        "  a restart, and keep this window open: closing it takes the address down.",
        bar,
        "",
    ])


class QuickTunnel:
    """A cloudflared quick tunnel to localhost:<port>, owned by this process."""

    def __init__(self, port: int):
        self.port = port
        self.url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._found: queue.Queue = queue.Queue(maxsize=1)

    def start(self, timeout: float = 45.0) -> str:
        exe = shutil.which("cloudflared")
        if not exe:
            raise TunnelUnavailable(install_hint())
        self._proc = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://localhost:{self.port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._drain, daemon=True).start()
        try:
            self.url = self._found.get(timeout=timeout)
        except queue.Empty:
            self.stop()
            raise TunnelUnavailable(
                f"cloudflared gave no address within {timeout:.0f} s. Check your internet "
                "connection, or run `cloudflared tunnel --url http://localhost:8080` "
                "yourself to see its error.") from None
        return self.url

    def _drain(self) -> None:
        # Read to the end even after the address is found: a pipe nobody reads fills up and
        # blocks cloudflared, which would take the tunnel down minutes later for no reason.
        for line in self._proc.stdout:
            url = parse_tunnel_url(line)
            if url and self._found.empty():
                self._found.put(url)
            if " ERR " in line:
                log.warning("cloudflared: %s", line.strip()[:200])

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def probe_until_reachable(base_url: str, verify_token: str, deadline: float = 90.0,
                          first_wait: float = 15.0) -> bool:
    """Send Meta's own handshake through the tunnel until the bot answers it.

    A quick tunnel's name does not exist for the first seconds after cloudflared prints it,
    and asking too early makes this machine cache "no such name" for a while (the browser's
    DNS_PROBE_FINISHED_NXDOMAIN). Measured 2026-09-30: an immediate probe failed for a full
    minute on a tunnel that was working. So the first probe waits.
    """
    time.sleep(first_wait)
    probe = (f"{webhook_url(base_url)}?hub.mode=subscribe&hub.verify_token={verify_token}"
             "&hub.challenge=agronaut_probe")
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(probe, timeout=10) as f:
                if f.read().decode(errors="replace").strip() == "agronaut_probe":
                    return True
        except Exception:  # noqa: BLE001 (not reachable yet; retry until the deadline)
            pass
        time.sleep(3)
    return False
