"""`agronaut setup` — an interactive first run, so nobody hand-writes a .env.

The old instructions were `mkdir -p ~/.config/agronaut` and a heredoc of five variables
copied from a README, with no feedback until something failed hours later. Every value in
that file is one someone can get wrong silently: a token that is really a phone number id,
a Telegram user id nobody knows how to find, a provider name that does not exist.

So this asks, validates each answer against the live service as it is given, and writes the
file itself. A wrong key is caught in the second it is typed rather than at the first
message.

The step worth calling out is the Telegram id. Telling someone to "find your numeric user
id" sends them to a third-party bot to get a number they do not understand, and the
allowlist silently refuses them if they get it wrong. Instead the wizard asks them to
message their own bot and reads the id off that update. It is the same fact, obtained by
doing the obvious thing.

Nothing here imports the agent or a model backend: setup must work before any of that is
configured, and must not be slow to start.
"""

from __future__ import annotations

import getpass
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 20


# --- pure helpers, so the awkward parts are testable without a terminal -------------------

def env_path() -> Path:
    """Where the wizard writes. A checkout keeps its own .env; anything else is per-user."""
    from agent.env import user_config_dir

    here = Path.cwd() / ".env"
    if here.is_file() or (Path.cwd() / "pyproject.toml").is_file():
        return here
    return user_config_dir() / ".env"


def merge_env(existing: str, updates: dict[str, str]) -> str:
    """Apply `updates` to a .env's text, replacing keys in place and appending new ones.

    In place matters: people put comments and their own variables in this file, and a
    wizard that rewrites it from scratch silently deletes them.
    """
    lines = existing.splitlines()
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# written by `agronaut setup`")
        out.extend(f"{k}={v}" for k, v in remaining.items())
    return "\n".join(out).rstrip("\n") + "\n"


def check_telegram_token(token: str) -> tuple[bool, str]:
    """Validate against Telegram and return the bot's @username, so the user sees the bot
    they just created rather than a bare 'ok'."""
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/getMe", timeout=TIMEOUT) as f:
            d = json.load(f)
        if not d.get("ok"):
            return False, "Telegram rejected that token"
        return True, "@" + d["result"]["username"]
    except urllib.error.HTTPError:
        return False, "Telegram rejected that token — check you copied all of it"
    except Exception as e:  # noqa: BLE001
        return False, f"could not reach Telegram: {e}"


def check_anthropic_key(key: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
            json.load(f)
        return True, "key accepted"
    except urllib.error.HTTPError as e:
        return False, ("that key was rejected" if e.code in (401, 403)
                       else f"Anthropic returned HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        return False, f"could not reach Anthropic: {e}"


def check_ollama() -> tuple[bool, str]:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=8) as f:
            models = [m["name"] for m in json.load(f).get("models", [])]
        if not models:
            return False, "Ollama is running but has no models — try `ollama pull qwen2.5`"
        return True, f"Ollama has {', '.join(models[:4])}"
    except Exception:  # noqa: BLE001
        return False, "Ollama is not running — install it from ollama.com, then `ollama pull qwen2.5`"


# --- the interactive parts ----------------------------------------------------------------

def _ask(prompt: str, options: list[tuple[str, str]], default: int = 1) -> int:
    print(f"\n{prompt}")
    for i, (label, note) in enumerate(options, 1):
        mark = " (recommended)" if i == default else ""
        print(f"  {i}) {label}{mark}")
        if note:
            print(f"     {note}")
    while True:
        raw = input(f"  choose 1-{len(options)} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        print("  not one of the options")


def _capture_telegram_id(token: str) -> str | None:
    """Read the user's numeric id off a message they send to their own bot.

    The alternative is telling someone to find their user id, which means sending them to
    a third-party bot for a number they cannot verify — and if they get it wrong the
    allowlist refuses them with no explanation.
    """
    base = f"https://api.telegram.org/bot{token}"
    try:                      # drain anything already queued, so we read a fresh message
        with urllib.request.urlopen(f"{base}/getUpdates?offset=-1&timeout=0", timeout=TIMEOUT) as f:
            prior = json.load(f).get("result", [])
        offset = (prior[-1]["update_id"] + 1) if prior else None
    except Exception:  # noqa: BLE001
        offset = None

    print("\n  Now open Telegram and send your bot any message.")
    print("  Waiting (Ctrl-C to skip)...", end="", flush=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            url = f"{base}/getUpdates?timeout=10" + (f"&offset={offset}" if offset else "")
            with urllib.request.urlopen(url, timeout=25) as f:
                for upd in json.load(f).get("result", []):
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    user = msg.get("from") or {}
                    if user.get("id"):
                        name = user.get("first_name") or user.get("username") or "you"
                        print(f"\r  Got it — {name}, id {user['id']}" + " " * 20)
                        return str(user["id"])
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001
            time.sleep(2)
        print(".", end="", flush=True)
    print("\r  No message arrived. You can set AGRONAUT_ALLOWED_IDS later." + " " * 10)
    return None


def run() -> int:
    print("\n  Agronaut setup\n  " + "-" * 40)
    updates: dict[str, str] = {}

    # 1. the model
    choice = _ask("Which model should power the chat?", [
        ("Claude", "best tool calling; needs an API key from console.anthropic.com"),
        ("Local, via Ollama", "no key, no internet, runs on this machine"),
        ("NVIDIA", "free key from build.nvidia.com"),
        ("Skip", "the calculator works with no model at all"),
    ])
    if choice == 1:
        while True:
            key = getpass.getpass("  Paste your Anthropic API key (hidden): ").strip()
            if not key:
                break
            ok, msg = check_anthropic_key(key)
            print(f"  {'OK' if ok else 'x'} {msg}")
            if ok:
                updates.update(LLM_PROVIDER="anthropic", LLM_MODEL="claude-sonnet-5",
                               ANTHROPIC_API_KEY=key)
                break
    elif choice == 2:
        ok, msg = check_ollama()
        print(f"  {'OK' if ok else 'x'} {msg}")
        updates.update(LLM_PROVIDER="ollama", LLM_MODEL="qwen2.5")
    elif choice == 3:
        key = getpass.getpass("  Paste your NVIDIA API key (hidden): ").strip()
        if key:
            updates.update(LLM_PROVIDER="nvidia", NVIDIA_API_KEY=key,
                           LLM_MODEL="mistralai/mistral-nemotron")

    # 2. the channel
    channel = _ask("Where do you want to talk to Agronaut?", [
        ("Telegram", "about two minutes, nothing else needed"),
        ("WhatsApp", "needs a Meta business account; run `agronaut whatsapp --check` after"),
        ("Terminal only", "just `agronaut` on this machine"),
    ])
    if channel == 1:
        print("\n  Open https://t.me/BotFather, send /newbot, and follow the prompts.")
        while True:
            token = input("  Paste the bot token it gives you: ").strip()
            if not token:
                break
            ok, msg = check_telegram_token(token)
            print(f"  {'OK' if ok else 'x'} {msg}")
            if ok:
                updates["TELEGRAM_BOT_TOKEN"] = token
                try:
                    uid = _capture_telegram_id(token)
                except KeyboardInterrupt:
                    uid = None
                    print("\r  Skipped." + " " * 30)
                if uid:
                    updates["AGRONAUT_ALLOWED_IDS"] = uid
                break
    elif channel == 2:
        print("\n  WhatsApp needs a Meta app, a business portfolio and a test number.")
        print("  Follow the README's 'Run on WhatsApp', then: agronaut whatsapp --check")

    if not updates:
        print("\n  Nothing to save. Run `agronaut setup` again when you have your keys.\n")
        return 0

    dest = env_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.read_text() if dest.is_file() else ""
    dest.write_text(merge_env(existing, updates))
    try:
        dest.chmod(0o600)          # it holds API keys; nobody else on the box needs them
    except OSError:
        pass

    print(f"\n  Saved to {dest}")
    print("  " + ", ".join(sorted(updates)))
    print("\n  Try it:")
    print("    agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000")
    if "TELEGRAM_BOT_TOKEN" in updates:
        print("    agronaut bot        # then message your bot")
    print()
    return 0
