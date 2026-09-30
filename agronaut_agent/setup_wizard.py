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
        steps.append("agronaut whatsapp --tunnel   # WhatsApp + a public address; paste what it prints into Meta")
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


def _ollama_default() -> str:
    """One source for the model name, so setup cannot drift from agent/llm.py again."""
    from agent.llm import DEFAULT_MODELS
    return DEFAULT_MODELS["ollama"]


def check_ollama() -> tuple[bool, str]:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=8) as f:
            models = [m["name"] for m in json.load(f).get("models", [])]
        if not models:
            return False, f"Ollama is running but has no models, try `ollama pull {_ollama_default()}`"
        return True, f"Ollama has {', '.join(models[:4])}"
    except Exception:  # noqa: BLE001
        return False, ("Ollama is not running: install it from ollama.com, then "
                       f"`ollama pull {_ollama_default()}`")


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


def _calling_codes() -> set[str]:
    """ITU country calling codes, as ranges. Used only to decide whether a "0" sits right
    after a country code; a few unassigned codes inside the ranges cost nothing here."""
    codes = {"1", "7", "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41",
             "43", "44", "45", "46", "47", "48", "49", "81", "82", "84", "86", "98",
             "850", "852", "853", "855", "856", "880", "886", "420", "421", "423"}
    for lo, hi in ((51, 58), (60, 66), (90, 95), (211, 299), (350, 359), (370, 389),
                   (500, 509), (590, 599), (670, 692), (960, 968), (970, 977), (992, 998)):
        codes |= {str(n) for n in range(lo, hi + 1)}
    return codes


def normalize_wa_numbers(raw: str) -> tuple[str, list[str]]:
    """Allowed numbers in the form Meta sends them, plus warnings about likely mistakes.

    Meta writes a sender as digits only, country code first, with no local trunk "0"
    (Taiwan 0912 345 678 arrives as 886912345678). People type "+886 0912-345-678". Both
    the "+" and the kept "0" silently dropped every message on 2026-09-30, so the "+",
    spaces and dashes are removed here, and a "0" right after a likely country code is
    flagged. It is only flagged, not removed: Italy keeps it, and a number's shape is not
    proof. The 0 counts only right after a real calling code, so a US 1-202 number is fine.
    """
    out, warnings = [], []
    for part in (raw or "").split(","):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            continue
        codes = _calling_codes()
        if any(digits[:i] in codes and digits[:i] != "39" and digits[i] == "0"
               for i in (1, 2, 3) if i < len(digits)):
            warnings.append(
                f"{digits}: there is a 0 near the start. If it is your local trunk 0 (the "
                "one you drop when dialling from abroad), remove it: Taiwan 0912... is "
                "886912..., Burkina Faso 70... is 22670.... The bot tolerates this mistake, "
                "but Meta's own tools will not.")
        out.append(digits)
    return ",".join(out), warnings


def _prompt_whatsapp(existing: dict[str, str] | None = None) -> dict[str, str]:
    """Collect the WhatsApp Cloud API values, in the order Meta's dashboard shows them.

    Every value here is one the adapter reads at startup, and `whatsapp_doctor` checks each
    one right after, so a typo shows up now rather than as a bot that never answers.

    Empty input skips a value. A half-filled WhatsApp config is a normal state, because
    these come from different screens and people genuinely do stop halfway.

    The verify token is KEPT when one exists. It used to be regenerated on every run, which
    silently broke the copy already saved in Meta's form.
    """
    existing = existing or {}
    # flush: getpass writes straight to the terminal, while stdout is block-buffered as soon
    # as this is piped or logged. Without the flush the explanation lands after the prompt it
    # explains, which is exactly the kind of small confusion this wizard exists to remove.
    print("\n  WhatsApp needs a Meta app with the 'Connect on WhatsApp' use case. Open:")
    print("    developers.facebook.com > My Apps > your app > Use cases > Connect on WhatsApp")
    print("    > Customize > Step 1. Try it out", flush=True)
    print("  Every screen, step by step: github.com/Rekin226/Agronaut/blob/main/docs/whatsapp_setup.md", flush=True)
    out: dict[str, str] = {}

    print("\n  1. ACCESS token: the long one (starts EAA...) under 'Access token' >")
    print("     'Generate token'. Meta's test token expires daily; replace it any time with")
    print("     `agronaut whatsapp --token`.", flush=True)
    token = getpass.getpass("     Access token (hidden, blank to skip): ").strip()
    if token:
        out["WHATSAPP_TOKEN"] = token

    print("\n  2. The two IDs printed beside the test number on the same screen.")
    phone_id = input("     Phone Number ID (a long id, NOT the phone number): ").strip()
    if phone_id:
        out["WHATSAPP_PHONE_NUMBER_ID"] = phone_id
    waba = input("     WhatsApp Business account ID: ").strip()
    if waba:
        out["WHATSAPP_WABA_ID"] = waba

    # Invented by you, not issued by Meta. Kept across runs so Meta's saved copy stays valid.
    verify = (existing.get("WHATSAPP_VERIFY_TOKEN") or "").strip()
    if verify:
        print(f"\n  3. VERIFY token: keeping the one you already have: {verify}")
    else:
        verify = secrets.token_urlsafe(16)
        print(f"\n  3. VERIFY token: you invent this one, so here is one: {verify}")
    print("     It goes into Meta's webhook form (Step 2), NOT the access token.", flush=True)
    out["WHATSAPP_VERIFY_TOKEN"] = verify

    secret = getpass.getpass(
        "\n  4. App secret, from App settings > Basic > App secret > Show\n"
        "     (hidden, blank to skip): ").strip()
    if secret:
        out["WHATSAPP_APP_SECRET"] = secret

    print("\n  5. Your own WhatsApp number, the phone you will message the bot FROM.")
    print("     Also add it on the Step 1 screen under 'To' (recipient list) and verify it;")
    print("     Meta's test number only talks to numbers listed there.")
    print("     Country code first, no leading 0: Taiwan 0912 345 678 -> 886912345678.")
    allowed = input("     Allowed number(s), comma separated (blank to skip): ").strip()
    if allowed:
        digits, warns = normalize_wa_numbers(allowed)
        for w in warns:
            print(f"     ! {w}")
        if digits:
            out["AGRONAUT_ALLOWED_IDS"] = digits
    return out


def _whatsapp_reachability_help(port: int = 8080) -> None:
    """The step the README buries and the wizard used to skip entirely.

    Telegram long-polls, so a laptop behind NAT works. WhatsApp is the other way round:
    Meta POSTs to you, over HTTPS, at an address it can resolve. That single difference is
    why one channel takes two minutes and the other defeats people.
    """
    print("\n  Last step, and the one that actually blocks people: Meta has to reach YOUR")
    print("  machine over HTTPS. On a laptop, one command does it:")
    print("\n    agronaut whatsapp --tunnel")
    print("\n  It starts the bot and a tunnel together and prints the exact Callback URL and")
    print("  verify token to paste into Meta (Step 2. Production setup > Configure")
    print("  Webhooks). It needs cloudflared (`brew install cloudflared` on a Mac).")
    print("\n  The address changes every time it starts, and Meta's test token expires daily,")
    print("  so for a bot that stays up, run it on a server. Telegram has neither problem.")


def replace_whatsapp_token() -> int:
    """`agronaut whatsapp --token`: swap in a new access token without editing .env by hand.

    Meta's test token expires daily, and pasting a 300-character secret into a file with
    sed was the step that failed on 2026-09-30 (run from a chat prompt, `read` got nothing).
    The token is checked against the Graph API before anything is written, so a bad paste
    cannot replace a working token.
    """
    import agent  # noqa: F401 (loads .env so the phone number id is known)

    from . import whatsapp_doctor as doc

    phone_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    if not phone_id:
        print("WHATSAPP_PHONE_NUMBER_ID is not set yet; run `agronaut setup` first.")
        return 2
    print("Meta: Use cases > Connect on WhatsApp > Customize > Step 1. Try it out >")
    print("Access token > Generate token, then copy it.", flush=True)
    token = getpass.getpass("Paste the new access token (hidden): ").strip()
    if not token:
        print("Nothing pasted; the current token is unchanged.")
        return 1
    status, body = doc._get(f"{doc.GRAPH}/{phone_id}?fields=display_phone_number", token)
    if status != 200:
        print(f"Meta rejected that token {doc._err(body)}; the current one is unchanged.")
        return 1
    dest = env_path()
    existing = dest.read_text() if dest.is_file() else ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(merge_env(existing, {"WHATSAPP_TOKEN": token}))
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    print(f"Token valid for {body.get('display_phone_number', 'your number')}; saved to "
          f"{dest}.\nRestart `agronaut whatsapp` so the bot uses it.")
    return 0


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
        updates.update(LLM_PROVIDER="ollama", LLM_MODEL=_ollama_default())
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
        ("Telegram (recommended for a phone)", "about two minutes, one token from "
                     "BotFather, nothing expires"),
        ("WhatsApp", "more setup: a Meta developer app and a public address; "
                     "`agronaut whatsapp --tunnel` handles the address on a laptop"),
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
        current = env_path()
        wa = _prompt_whatsapp(parse_env(current.read_text()) if current.is_file() else {})
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
