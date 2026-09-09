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
import secrets
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


def parse_env(text: str) -> dict[str, str]:
    """The KEY=value pairs in a .env, ignoring comments and blanks.

    Used to answer "what is configured overall", which is not the same question as "what did
    this run change". Someone who set their model last week and adds a channel today has a
    working model, and the closing advice has to know that.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def next_steps(config: dict[str, str]) -> list[str]:
    """The commands to print at the end of setup. Never empty.

    This function exists because the wizard used to be able to end on a README pointer. A
    first run that finishes with nothing to type is a failed first run, however much it
    successfully configured, and that is exactly what happened to the maintainer on the day
    after 1.0.0 shipped: model configured, WhatsApp chosen, and not one runnable command.

    So the rule is encoded here rather than left to the flow: if a model is configured the
    REPL is offered first, because `agronaut` with no arguments is the one channel that
    needs no token, no account and no public address. The calculator is always offered,
    because it needs no model either. Channels are added only once their credentials exist.
    """
    steps: list[str] = []
    if config.get("LLM_PROVIDER"):
        steps.append("agronaut            # chat with the agent, right here in this terminal")
    steps.append(
        "agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000")
    if config.get("TELEGRAM_BOT_TOKEN"):
        steps.append("agronaut bot        # then message your bot")
    if config.get("WHATSAPP_TOKEN"):
        steps.append("agronaut whatsapp   # with your tunnel running, see above")
    return steps


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


def _prompt_whatsapp() -> dict[str, str]:
    """Collect the WhatsApp Cloud API values, in the order Meta's dashboard shows them.

    The branch this replaces printed two lines and pointed at the README, so it collected
    nothing at all: someone could choose WhatsApp, finish setup, and have a .env with no
    WhatsApp variable in it. Every value here is one the adapter reads at startup, and
    `whatsapp_doctor` already knows how to check each one, so the wizard asks and then lets
    the doctor answer.

    Empty input skips a value. A half-filled WhatsApp config is a normal state, because
    these come from three different screens and people genuinely do stop halfway.
    """
    print("\n  WhatsApp needs a Meta app with WhatsApp added to it. From")
    # flush: getpass writes straight to the terminal, while stdout is block-buffered as soon
    # as this is piped or logged. Without the flush the explanation lands after the prompt it
    # explains, which is exactly the kind of small confusion this wizard exists to remove.
    print("  developers.facebook.com > your app > WhatsApp > API Setup:", flush=True)
    out: dict[str, str] = {}

    token = getpass.getpass("\n  Temporary access token (hidden, blank to skip): ").strip()
    if token:
        out["WHATSAPP_TOKEN"] = token

    phone_id = input("  Phone number ID (a long number, NOT the phone number): ").strip()
    if phone_id:
        out["WHATSAPP_PHONE_NUMBER_ID"] = phone_id

    # Invented by you, not issued by Meta. People routinely hunt for it in the dashboard,
    # so generate one and say plainly where it goes.
    verify = secrets.token_urlsafe(16)
    print(f"\n  Verify token (you invent this one, so here is one): {verify}")
    print("  Paste that same string into Meta's webhook form when it asks.", flush=True)
    out["WHATSAPP_VERIFY_TOKEN"] = verify

    secret = getpass.getpass(
        "\n  App secret from Settings > Basic (hidden, blank to skip): ").strip()
    if secret:
        out["WHATSAPP_APP_SECRET"] = secret

    print("\n  Which numbers may talk to the bot? Without this, anyone who finds your")
    print("  webhook URL can. Use the full international form, e.g. 22670000000.")
    allowed = input("  Allowed numbers, comma separated (blank to skip): ").strip()
    if allowed:
        out["AGRONAUT_ALLOWED_IDS"] = allowed
    return out


def _whatsapp_reachability_help(port: int = 8080) -> None:
    """The step the README buries and the wizard used to skip entirely.

    Telegram long-polls, so a laptop behind NAT works. WhatsApp is the other way round:
    Meta POSTs to you, over HTTPS, at an address it can resolve. That single difference is
    why one channel takes two minutes and the other defeats people, and not saying it out
    loud is what left the maintainer stuck after a successful install.
    """
    print("\n  One more thing, and it is the part that actually blocks people.")
    print("\n  Meta delivers messages by POSTing to YOUR machine over HTTPS. A laptop")
    print("  has no public address, so you need a tunnel running beside the bot:")
    print(f"\n    cloudflared tunnel --url http://localhost:{port}")
    print(f"    ngrok http {port}")
    print("\n  Either prints an https:// address. Your webhook URL is that address with")
    print("  /webhook on the end. Paste it, and the verify token above, into")
    print("  Meta > WhatsApp > Configuration, then subscribe to the 'messages' field.")
    print("\n  The tunnel address changes each restart on the free plans, so for anything")
    print("  lasting, run this on a host with a real certificate instead of a laptop.")


def _run_whatsapp_doctor(collected: dict[str, str]) -> None:
    """Check what was just entered, instead of telling the operator to check it later.

    `whatsapp_doctor` reads its inputs from the environment, so the values have to be put
    there before it runs: this is the same process that just collected them, and nothing
    has reloaded the .env yet.
    """
    for key, value in collected.items():
        os.environ[key] = value
    print("\n  Checking what you entered...\n")
    try:
        from . import whatsapp_doctor

        text, _ = whatsapp_doctor.report(whatsapp_doctor.run_checks())
    except Exception as err:  # noqa: BLE001 — a failed check must not lose the .env write
        print(f"  Could not run the checks ({err}).")
        print("  Run `agronaut whatsapp --check` once you are online.")
        return
    print("\n".join("  " + line for line in text.splitlines()))


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
    #
    # Ordered by what it costs the person answering, not by how impressive it sounds. The
    # terminal is first because it is the only one that works the second this wizard exits,
    # and WhatsApp is last with its real price on the label: the Meta account is the part
    # people expect, and the public HTTPS address is the part that actually stops them.
    channel = _ask("Where do you want to talk to Agronaut?", [
        ("This terminal", "works as soon as setup finishes, nothing else needed"),
        ("Telegram", "about two minutes, one token from BotFather"),
        ("WhatsApp", "a Meta business account AND a public HTTPS address; "
                     "best on a server, not a laptop"),
    ])
    if channel == 2:
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
    elif channel == 3:
        wa = _prompt_whatsapp()
        updates.update(wa)
        if wa.get("WHATSAPP_TOKEN"):
            _run_whatsapp_doctor(wa)
        _whatsapp_reachability_help()

    dest = env_path()
    existing = dest.read_text() if dest.is_file() else ""

    if updates:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(merge_env(existing, updates))
        try:
            dest.chmod(0o600)      # it holds API keys; nobody else on the box needs them
        except OSError:
            pass
        print(f"\n  Saved to {dest}")
        print("  " + ", ".join(sorted(updates)))
    else:
        print("\n  Nothing new to save.")

    # Always ends on something runnable. `next_steps` reads the whole configuration rather
    # than this run's changes, so someone who set their model last week and only added a
    # channel today still gets told about the chat they already have.
    config = {**parse_env(existing), **updates}
    print("\n  Try it:")
    for step in next_steps(config):
        print(f"    {step}")
    if not config.get("LLM_PROVIDER"):
        print("\n  No model configured, so chat is off, but the sizing engine above needs")
        print("  none. Run `agronaut setup` again when you have a key, or pick Ollama to")
        print("  run one on this machine with no key at all.")
    print()
    return 0
