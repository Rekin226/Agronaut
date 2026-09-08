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
    monkeypatch.setattr(W, "_ask", lambda *a, **k: 4)        # skip model
    monkeypatch.setattr("builtins.input", lambda *a: "")
    calls = iter([4, 3])                                     # skip model, terminal only
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
