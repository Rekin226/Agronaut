"""WhatsApp onboarding: the pieces that made the first working setup take an afternoon.

Every test here is a failure that actually happened on 2026-09-30, when a WhatsApp reply
reached a phone for the first time: a tunnel address copied by hand, a verify token that
setup regenerated, a number typed as "+886 0912...", a token pasted with sed from a prompt
that could not read input. No network, no cloudflared, no Meta.
"""

import io

import pytest

from agronaut_agent import setup_wizard as W
from agronaut_agent import whatsapp_tunnel as T
from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter

CLOUDFLARED_LINE = ("2026-09-30T02:37:14Z INF |  https://pictures-significance-warrior-"
                    "translator.trycloudflare.com                        |")


# --- the tunnel ----------------------------------------------------------------------------

def test_the_address_is_read_from_cloudflareds_own_log_line():
    assert T.parse_tunnel_url(CLOUDFLARED_LINE) == (
        "https://pictures-significance-warrior-translator.trycloudflare.com")
    assert T.parse_tunnel_url("INF Requesting new quick Tunnel on trycloudflare.com...") is None


def test_the_callback_url_always_ends_in_webhook():
    assert T.webhook_url("https://a-b.trycloudflare.com/") == "https://a-b.trycloudflare.com/webhook"


def test_the_paste_card_names_both_values_and_warns_off_the_access_token():
    card = T.paste_card("https://a-b.trycloudflare.com", "Xy7kQ2mZpL9vRt3wNa8bCd")
    assert "Callback URL   https://a-b.trycloudflare.com/webhook" in card
    assert "Verify token   Xy7kQ2mZpL9vRt3wNa8bCd" in card
    assert "NOT the long access token" in card
    assert "Configure Webhooks" in card


def test_a_missing_cloudflared_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(T.shutil, "which", lambda name: None)
    with pytest.raises(T.TunnelUnavailable, match="brew install cloudflared"):
        T.QuickTunnel(8080).start()


class _FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_the_tunnel_waits_for_the_address_in_cloudflareds_output(monkeypatch):
    proc = _FakeProc(["INF Requesting new quick Tunnel on trycloudflare.com...",
                      CLOUDFLARED_LINE, "INF Registered tunnel connection"])
    monkeypatch.setattr(T.shutil, "which", lambda name: "/usr/bin/cloudflared")
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: proc)
    tun = T.QuickTunnel(8080)
    assert tun.start(timeout=5).endswith("pictures-significance-warrior-translator.trycloudflare.com")
    tun.stop()
    assert proc.terminated


def test_a_tunnel_that_never_prints_an_address_is_stopped_and_explained(monkeypatch):
    proc = _FakeProc(["ERR failed to request quick Tunnel"])
    monkeypatch.setattr(T.shutil, "which", lambda name: "/usr/bin/cloudflared")
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: proc)
    with pytest.raises(T.TunnelUnavailable, match="no address"):
        T.QuickTunnel(8080).start(timeout=0.5)
    assert proc.terminated


# --- the allowed number --------------------------------------------------------------------

@pytest.mark.parametrize("typed, stored, warns", [
    ("+886 0912-345-678", "8860912345678", True),   # the one that dropped every message
    ("+886 912 345 678", "886912345678", False),
    ("+44 07911 123456", "4407911123456", True),
    ("22670123456", "22670123456", False),          # Burkina Faso, already right
    ("+1 202 555 0123", "12025550123", False),      # a 0 inside a US area code is fine
    ("+39 06 1234 5678", "390612345678", False),    # Italy keeps its 0
])
def test_numbers_are_stored_as_meta_sends_them_and_a_trunk_zero_is_flagged(typed, stored, warns):
    digits, warnings = W.normalize_wa_numbers(typed)
    assert digits == stored
    assert bool(warnings) is warns


def test_the_bot_still_answers_a_number_saved_with_its_trunk_zero():
    a = WhatsAppAdapter(agent=object(), token="t", phone_number_id="p", verify_token="v",
                        app_secret="s", allowed_ids=["+886 0912 345 678"])
    assert a._allowed("886912345678")
    assert not a._allowed("886912345679")


# --- setup ---------------------------------------------------------------------------------

def _answers(monkeypatch, inputs, hidden):
    it, hid = iter(inputs), iter(hidden)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    monkeypatch.setattr(W.getpass, "getpass", lambda *a: next(hid))


def test_setup_keeps_an_existing_verify_token(monkeypatch):
    """It used to mint a new one every run, silently breaking the copy saved in Meta."""
    _answers(monkeypatch, ["123", "456", ""], ["", ""])
    out = W._prompt_whatsapp({"WHATSAPP_VERIFY_TOKEN": "keep-me-please"})
    assert out["WHATSAPP_VERIFY_TOKEN"] == "keep-me-please"


def test_setup_collects_the_business_account_id_and_a_clean_number(monkeypatch):
    _answers(monkeypatch, ["1345793778613167", "4799714900248979", "+886 0912 345 678"],
             ["EAAtoken", "secret"])
    out = W._prompt_whatsapp({})
    assert out["WHATSAPP_WABA_ID"] == "4799714900248979"
    assert out["AGRONAUT_ALLOWED_IDS"] == "8860912345678"
    assert len(out["WHATSAPP_VERIFY_TOKEN"]) >= 16


def test_replacing_the_token_checks_it_with_meta_before_writing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("WHATSAPP_TOKEN=old\nWHATSAPP_PHONE_NUMBER_ID=123\n")
    monkeypatch.setattr(W, "env_path", lambda: env)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr(W.getpass, "getpass", lambda *a: "EAAnew")
    from agronaut_agent import whatsapp_doctor as doc

    monkeypatch.setattr(doc, "_get", lambda *a, **k: (400, {"error": {"code": 190}}))
    assert W.replace_whatsapp_token() == 1
    assert "WHATSAPP_TOKEN=old" in env.read_text()

    monkeypatch.setattr(doc, "_get", lambda *a, **k: (200, {"display_phone_number": "+1 555"}))
    assert W.replace_whatsapp_token() == 0
    assert "WHATSAPP_TOKEN=EAAnew" in env.read_text()


def test_the_cli_exposes_tunnel_and_token():
    from agronaut_agent.cli import _build_parser

    args = _build_parser().parse_args(["whatsapp", "--tunnel"])
    assert args.tunnel and not args.token
    assert _build_parser().parse_args(["whatsapp", "--token"]).token


# --- a permanent address (2026-10-03: every restart meant pasting a new one into Meta) ----

@pytest.mark.parametrize("given", [
    "https://mac.tail1234.ts.net",
    "https://mac.tail1234.ts.net/",
    "https://mac.tail1234.ts.net/webhook",       # the Callback URL pasted back
    "  https://mac.tail1234.ts.net/webhook/  ",
])
def test_the_permanent_address_is_read_however_it_was_pasted(given):
    assert T.public_base(given) == "https://mac.tail1234.ts.net"


@pytest.mark.parametrize("given", [None, "", "http://mac.tail1234.ts.net", "mac.ts.net",
                                   "https://"])
def test_an_unusable_permanent_address_is_ignored(given):
    """Meta only calls https, so an http address would verify nothing and fail silently."""
    assert T.public_base(given) is None


def test_the_permanent_card_gives_the_callback_url_and_says_it_does_not_change():
    card = T.permanent_card("https://mac.tail1234.ts.net")
    assert "https://mac.tail1234.ts.net/webhook" in card
    assert "nothing to paste" in card and "changes every time" not in card
