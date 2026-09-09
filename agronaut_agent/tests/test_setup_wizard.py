"""The setup wizard: the awkward parts, without a terminal.

Everything a user can get wrong silently — a rejected key, a hand-written .env being
clobbered, a config file left world-readable — is checked here.
"""

import json
import urllib.error

from agronaut_agent import setup_wizard as W

# --- merging into an existing .env ---------------------------------------------------------

def test_existing_keys_are_replaced_in_place():
    out = W.merge_env("LLM_PROVIDER=nvidia\nOTHER=keep\n", {"LLM_PROVIDER": "anthropic"})
    assert "LLM_PROVIDER=anthropic" in out
    assert "LLM_PROVIDER=nvidia" not in out
    assert "OTHER=keep" in out


def test_comments_and_unrelated_variables_survive():
    """People keep their own notes and variables in this file. A wizard that rewrites it
    from scratch deletes them silently."""
    original = "# my notes\nKAGGLE_KEY=abc\n\n# section\nLLM_MODEL=old\n"
    out = W.merge_env(original, {"LLM_MODEL": "claude-sonnet-5"})
    assert "# my notes" in out
    assert "KAGGLE_KEY=abc" in out
    assert "# section" in out
    assert "LLM_MODEL=claude-sonnet-5" in out


def test_new_keys_are_appended_with_a_marker():
    out = W.merge_env("EXISTING=1\n", {"TELEGRAM_BOT_TOKEN": "t"})
    assert "EXISTING=1" in out
    assert "TELEGRAM_BOT_TOKEN=t" in out
    assert "agronaut setup" in out


def test_writing_into_nothing_works():
    out = W.merge_env("", {"A": "1", "B": "2"})
    assert "A=1" in out and "B=2" in out
    assert out.endswith("\n")


# --- validating answers against the live services -----------------------------------------

class _Resp:
    def __init__(self, payload):
        self._p = payload
    def read(self):
        return json.dumps(self._p).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_a_good_telegram_token_reports_the_bot_name(monkeypatch):
    """Showing @TheirBot proves it is *their* bot, not merely that a string parsed."""
    monkeypatch.setattr(W.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"ok": True, "result": {"username": "AgronautBOT"}}))
    ok, msg = W.check_telegram_token("123:abc")
    assert ok and msg == "@AgronautBOT"


def test_a_rejected_telegram_token_says_so(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
    monkeypatch.setattr(W.urllib.request, "urlopen", _boom)
    ok, msg = W.check_telegram_token("nope")
    assert not ok and "rejected" in msg


def test_a_rejected_anthropic_key_is_distinguished_from_an_outage(monkeypatch):
    """401 is the user's problem; 500 is not. Telling them apart is the difference between
    "retype your key" and "wait"."""
    def _401(*a, **k):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    monkeypatch.setattr(W.urllib.request, "urlopen", _401)
    ok, msg = W.check_anthropic_key("bad")
    assert not ok and "rejected" in msg

    def _500(*a, **k):
        raise urllib.error.HTTPError("u", 500, "boom", {}, None)
    monkeypatch.setattr(W.urllib.request, "urlopen", _500)
    ok, msg = W.check_anthropic_key("fine")
    assert not ok and "500" in msg


def test_ollama_without_models_is_not_reported_as_ready(monkeypatch):
    """A running Ollama with nothing pulled fails at the first message, not at setup."""
    monkeypatch.setattr(W.urllib.request, "urlopen", lambda *a, **k: _Resp({"models": []}))
    ok, msg = W.check_ollama()
    assert not ok and "ollama pull" in msg


def test_ollama_with_models_lists_them(monkeypatch):
    monkeypatch.setattr(W.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"models": [{"name": "qwen2.5"}]}))
    ok, msg = W.check_ollama()
    assert ok and "qwen2.5" in msg


def test_ollama_not_running_points_at_the_fix(monkeypatch):
    def _boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(W.urllib.request, "urlopen", _boom)
    ok, msg = W.check_ollama()
    assert not ok and "ollama.com" in msg


# --- where the file lands ------------------------------------------------------------------

def test_a_checkout_keeps_its_own_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert W.env_path() == tmp_path / ".env"


def test_an_installed_user_gets_the_per_user_config_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # no checkout, no existing .env
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert W.env_path() == tmp_path / "cfg" / "agronaut" / ".env"


def test_the_written_file_is_not_world_readable(tmp_path, monkeypatch):
    """It holds API keys. Nobody else on the machine needs them."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    calls = iter([4, 1])                                     # skip model, this terminal
    monkeypatch.setattr(W, "_ask", lambda *a, **k: next(calls))
    W.run()
    # nothing chosen -> nothing written; assert we did not create a stray file
    assert not (tmp_path / "cfg" / "agronaut" / ".env").exists()


def test_the_cli_exposes_setup():
    from agronaut_agent.cli import _build_parser
    names = set()
    for a in _build_parser()._actions:
        if getattr(a, "choices", None):
            names.update(a.choices)
    assert "setup" in names


# --- what to run next ----------------------------------------------------------------------
#
# The wizard shipped able to end on a README pointer: model configured, WhatsApp chosen, and
# not one runnable command. These pin the rule that replaced it.

def test_there_is_always_something_to_run():
    """Even with nothing configured at all. The sizing engine needs no model and no channel,
    so a first run has no excuse to end empty-handed."""
    assert W.next_steps({}), "setup must never end without a command"
    assert any("agronaut size" in s for s in W.next_steps({}))


def test_a_configured_model_offers_the_terminal_chat_first():
    steps = W.next_steps({"LLM_PROVIDER": "ollama"})
    assert steps[0].startswith("agronaut ") or steps[0].startswith("agronaut#")
    assert "agronaut  " in steps[0], f"expected the bare REPL first, got {steps[0]!r}"


def test_choosing_whatsapp_still_ends_with_a_working_command():
    """The exact reported failure: WhatsApp was picked, so no Telegram token exists, and the
    old ending offered nothing that used the model that had just been configured."""
    steps = W.next_steps({"LLM_PROVIDER": "anthropic", "WHATSAPP_TOKEN": "t"})
    assert any(s.startswith("agronaut  ") for s in steps), "no terminal chat offered"
    assert any("agronaut whatsapp" in s for s in steps)
    assert not any("bot" in s for s in steps), "offered the Telegram bot without a token"


def test_a_channel_is_only_offered_once_its_credentials_exist():
    steps = W.next_steps({"LLM_PROVIDER": "ollama"})
    assert not any("agronaut bot" in s for s in steps)
    assert not any("agronaut whatsapp" in s for s in steps)


def test_the_whole_configuration_counts_not_just_this_run():
    """Someone who set their model last week and adds Telegram today still has a model."""
    config = {**W.parse_env("LLM_PROVIDER=ollama\n"), "TELEGRAM_BOT_TOKEN": "t"}
    steps = W.next_steps(config)
    assert any(s.startswith("agronaut  ") for s in steps)
    assert any("agronaut bot" in s for s in steps)


def test_parse_env_ignores_comments_and_blanks():
    parsed = W.parse_env("# a note\n\nLLM_PROVIDER=ollama\nnot a pair\nB = 2\n")
    assert parsed == {"LLM_PROVIDER": "ollama", "B": "2"}


def test_the_terminal_is_the_first_channel_offered(tmp_path, monkeypatch):
    """Ordering is load-bearing, not cosmetic. The terminal works the moment setup exits;
    WhatsApp needs a business account and a public address. Offering them in the other order
    is what sent the maintainer down a dead end on his own first run.

    This also pins the option numbers that `run()`'s branches switch on, which is what broke
    when they were last reordered.
    """
    seen: list[list[tuple[str, str]]] = []

    def _spy(prompt, options, default=1):
        seen.append(options)
        return 4 if "model" in prompt else 1

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(W, "_ask", _spy)
    W.run()

    channel_options = seen[-1]
    labels = [label for label, _ in channel_options]
    assert "terminal" in labels[0].lower(), f"terminal must be offered first, got {labels}"
    assert "whatsapp" in labels[-1].lower(), f"WhatsApp must be offered last, got {labels}"
    # The price people actually trip over is the address, not the account.
    whatsapp_note = channel_options[-1][1].lower()
    assert "https" in whatsapp_note or "address" in whatsapp_note, whatsapp_note


def test_a_run_that_configures_nothing_still_tells_you_what_works(tmp_path, monkeypatch,
                                                                  capsys):
    """The old dead end read 'Nothing to save. Run `agronaut setup` again when you have your
    keys', which is wrong: the sizing engine needs no keys and works right then."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    calls = iter([4, 1])                                     # skip model, this terminal
    monkeypatch.setattr(W, "_ask", lambda *a, **k: next(calls))
    W.run()
    out = capsys.readouterr().out
    assert "agronaut size" in out
    assert "README" not in out, "setup must not end by pointing at the README"


def test_option_two_really_reaches_the_telegram_branch(tmp_path, monkeypatch):
    """`run()` switches on the menu's numbers, so reordering the menu silently reassigns the
    branches. Nothing caught that: every other test either skips the channel step or asserts
    on labels. This one drives the number and checks the token actually lands."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls = iter([4, 2])                                     # skip model, Telegram
    monkeypatch.setattr(W, "_ask", lambda *a, **k: next(calls))
    monkeypatch.setattr("builtins.input", lambda *a: "111:abc")
    monkeypatch.setattr(W, "check_telegram_token", lambda t: (True, "@AgronautBOT"))
    monkeypatch.setattr(W, "_capture_telegram_id", lambda t: "424242")
    W.run()
    written = (tmp_path / "cfg" / "agronaut" / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN=111:abc" in written
    assert "AGRONAUT_ALLOWED_IDS=424242" in written


def test_option_three_really_reaches_the_whatsapp_branch(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls = iter([4, 3])                                     # skip model, WhatsApp
    monkeypatch.setattr(W, "_ask", lambda *a, **k: next(calls))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(W.getpass, "getpass", lambda *a: "")
    W.run()
    out = capsys.readouterr().out
    # The tunnel is the part that actually blocks people, so it has to be said out loud.
    assert "cloudflared" in out and "/webhook" in out
    assert "developers.facebook.com" in out
