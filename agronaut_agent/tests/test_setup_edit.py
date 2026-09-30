"""`agronaut setup` on a setup that already works: change one part, keep the rest.

Reported by the maintainer on 2026-09-30: with Agronaut running on Claude and Telegram,
running setup again walked through everything from the start, and there was no way to
switch Claude for a local model, or skip a channel that was already connected. And because
Telegram ids and WhatsApp numbers share AGRONAUT_ALLOWED_IDS, setting up one channel
silently locked the other one out. No network here: every service check is stubbed.
"""

import pytest

from agronaut_agent import setup_wizard as W

WORKING = ("LLM_PROVIDER=anthropic\nLLM_MODEL=claude-sonnet-5\nANTHROPIC_API_KEY=sk-ant-SECRET\n"
           "TELEGRAM_BOT_TOKEN=123:ABC\nAGRONAUT_ALLOWED_IDS=5551234\n# my own note\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text(WORKING)
    monkeypatch.setattr(W, "env_path", lambda: f)
    monkeypatch.setattr(W, "check_anthropic_key", lambda k: (True, "key accepted"))
    monkeypatch.setattr(W, "check_ollama", lambda: (True, "Ollama has qwen3.5:4b"))
    monkeypatch.setattr(W, "check_telegram_token", lambda t: (True, "@AgronautBOT"))
    return f


def _script(monkeypatch, asks, inputs=(), hidden=()):
    a, i, h = iter(asks), iter(inputs), iter(hidden)
    monkeypatch.setattr(W, "_ask", lambda *args, **kw: next(a))
    monkeypatch.setattr("builtins.input", lambda *args: next(i))
    monkeypatch.setattr(W.getpass, "getpass", lambda *args: next(h))


# --- the summary ---------------------------------------------------------------------------

def test_the_summary_shows_what_is_set_and_never_a_secret():
    lines = W.summarize(W.parse_env(WORKING))
    text = "\n".join(lines)
    assert "Claude (claude-sonnet-5), key saved" in text
    assert "Telegram   token saved" in text
    assert "WhatsApp   not set up" in text
    assert "SECRET" not in text and "123:ABC" not in text


def test_a_provider_without_its_key_is_flagged():
    assert "KEY MISSING" in W.summarize({"LLM_PROVIDER": "anthropic"})[0]


# --- the menu ------------------------------------------------------------------------------

def test_an_existing_setup_gets_the_menu_and_nothing_changes_on_nothing(env, monkeypatch, capsys):
    _script(monkeypatch, asks=[1])                       # "Nothing"
    W.run()
    out = capsys.readouterr().out
    assert "Your current setup" in out and "Nothing changed." in out
    assert env.read_text() == WORKING


def test_switching_to_a_local_model_keeps_the_claude_key(env, monkeypatch):
    _script(monkeypatch, asks=[2, 2, 1])                 # The model > Local > Done
    W.run()
    cfg = W.parse_env(env.read_text())
    assert cfg["LLM_PROVIDER"] == "ollama" and cfg["LLM_MODEL"].startswith("qwen")
    assert cfg["ANTHROPIC_API_KEY"] == "sk-ant-SECRET", "the key must survive a switch"
    assert "# my own note" in env.read_text()


def test_switching_back_to_claude_needs_no_key_retyped(env, monkeypatch):
    env.write_text(WORKING.replace("LLM_PROVIDER=anthropic", "LLM_PROVIDER=ollama"))
    _script(monkeypatch, asks=[1], hidden=[""])          # Claude, press Enter at the key
    W.run("model")
    cfg = W.parse_env(env.read_text())
    assert cfg["LLM_PROVIDER"] == "anthropic"
    assert cfg["ANTHROPIC_API_KEY"] == "sk-ant-SECRET"


def test_a_connected_telegram_can_simply_be_kept(env, monkeypatch):
    _script(monkeypatch, asks=[3, 1, 1])                 # Telegram > Keep it > Done
    W.run()
    assert W.parse_env(env.read_text())["TELEGRAM_BOT_TOKEN"] == "123:ABC"


def test_adding_whatsapp_does_not_lock_the_telegram_user_out(env, monkeypatch):
    monkeypatch.setattr(W, "_run_whatsapp_doctor", lambda collected: None)
    _script(monkeypatch, asks=[],
            inputs=["1345793778613167", "4799714900248979", "886912345678"],
            hidden=["EAAtoken", "appsecret"])
    W.run("whatsapp")
    allowed = W.parse_env(env.read_text())["AGRONAUT_ALLOWED_IDS"].split(",")
    assert allowed == ["5551234", "886912345678"]


def test_whatsapp_values_already_saved_are_kept_on_enter(env, monkeypatch):
    env.write_text(WORKING + "WHATSAPP_TOKEN=EAAold\nWHATSAPP_PHONE_NUMBER_ID=999\n"
                             "WHATSAPP_VERIFY_TOKEN=keepme\n")
    monkeypatch.setattr(W, "_run_whatsapp_doctor", lambda collected: None)
    _script(monkeypatch, asks=[], inputs=["", "", ""], hidden=["", ""])
    W.run("whatsapp")
    cfg = W.parse_env(env.read_text())
    assert cfg["WHATSAPP_TOKEN"] == "EAAold" and cfg["WHATSAPP_PHONE_NUMBER_ID"] == "999"
    assert cfg["WHATSAPP_VERIFY_TOKEN"] == "keepme"


def test_merge_allowed_adds_without_duplicates():
    assert W.merge_allowed("5551234,886912345678", "886912345678, 42") == \
        "5551234,886912345678,42"
    assert W.merge_allowed("", "42") == "42"


# --- the shortcuts -------------------------------------------------------------------------

@pytest.mark.parametrize("section", ["model", "telegram", "whatsapp"])
def test_setup_takes_a_section_shortcut(section):
    from agronaut_agent.cli import _build_parser

    assert _build_parser().parse_args(["setup", section]).section == section
    assert _build_parser().parse_args(["setup"]).section is None


def test_a_fresh_install_still_gets_the_full_walkthrough(tmp_path, monkeypatch, capsys):
    f = tmp_path / ".env"
    monkeypatch.setattr(W, "env_path", lambda: f)
    asked, answers = [], iter([4, 1])                    # skip model, this terminal
    monkeypatch.setattr(W, "_ask", lambda prompt, *a, **k: asked.append(prompt) or next(answers))
    W.run()
    assert "Your current setup" not in capsys.readouterr().out
    assert asked == ["Which model should power the chat?",
                     "Where do you want to talk to Agronaut?"]
