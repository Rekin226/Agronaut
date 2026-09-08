"""Slash commands as plain text, for channels with no native command menu.

Telegram registers commands with the platform: `python-telegram-bot` dispatches them, and
`setMyCommands` puts them in the app's own menu. WhatsApp has neither. Its Cloud API
delivers `/log ammonia 0.5` as an ordinary text message and offers no menu to register, so
before this module every WhatsApp message went to the LLM and the entire command layer was
missing on the channel most of Agronaut's users are actually on.

That gap mattered more than a missing convenience. The commands are the half that works
when the model does not: `/log`, `/forecast`, `/advise`, `/approve` and `/reject` reach the
deterministic twin with NO LLM in the path, which is exactly why they exist. `/export` and
`/delete_me` are the data-rights commands, so their absence on WhatsApp was a gap in what
an operator can do with their own data, not just in ergonomics.

The dispatch table lives here rather than in an adapter so a channel gains commands by
routing text through `dispatch()`. Telegram keeps its native handlers — they earn the
platform menu, and rewriting a working path to share code is a bad trade — but the two
lists are checked against each other by a test, so a command added to one and forgotten on
the other fails CI instead of quietly existing on one channel only.

Pure dispatch: parsing and routing only. Every command delegates to the same
`AgronautAgent` methods the Telegram handlers call, so behaviour cannot drift by channel.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reply:
    """What a command produced: words, and optionally a file to send alongside them."""

    text: str
    document: str | None = None          # local path, for channels that can send files
    document_mime: str = "application/json"


HELP = """🌱 *Agronaut* — your aquaponics assistant.

Just describe your system or ask a question. Or use a command:

/log — put readings into your LIVE twin (no AI in the path)
    /log ammonia 0.5 nitrate 40 temp 27
/forecast — your twin now + the week ahead (add shade/poly/heated)
/advise — what to do, numbered; then /approve 1 3 or /reject 2
/whoami — what I remember about you
/export — download everything I hold about you (open JSON)
/reset — clear this conversation (keeps long-term memory)
/forget — wipe everything I know about you
/delete_me — permanently erase all your data
/good, /bad — tell me if an answer helped

I never touch your equipment: /advise proposes, you decide."""


def looks_like_command(text: str) -> bool:
    """True for a leading slash followed by a word. Cheap and deliberately strict: a
    message that merely mentions a path ("check /var/log") is not a command."""
    t = (text or "").strip()
    return len(t) > 1 and t[0] == "/" and (t[1].isalpha() or t[1] == "_")


def _split(text: str) -> tuple[str, str]:
    body = (text or "").strip().lstrip("/")
    name, _, rest = body.partition(" ")
    # Telegram sends "/help@AgronautBOT" in groups; accept the same spelling anywhere.
    return name.split("@", 1)[0].strip().lower(), rest.strip()


# Readings syntax /log accepts, deliberately forgiving:
#   /log ammonia 0.5 nitrate 40 temp 27
#   /log ammonia=0.5, no2: 0.3, weight 210, count 58, shade
_LOG_KEYS = {
    "ammonia": "ammonia_mg_l", "nh3": "ammonia_mg_l", "nh4": "ammonia_mg_l",
    "nitrite": "nitrite_mg_l", "no2": "nitrite_mg_l",
    "nitrate": "nitrate_mg_l", "no3": "nitrate_mg_l",
    "temp": "water_temp_c", "temperature": "water_temp_c", "water": "water_temp_c",
    "weight": "fish_avg_weight_g", "count": "fish_count", "fish": "fish_count",
}


def parse_log_args(text: str) -> dict:
    """Parse a /log command's free-form readings into log_my_readings kwargs. Pure and
    unit-tested — this is a farmer's data-entry path and must not surprise anyone."""
    import re
    text = (text or "").lower()
    args: dict = {}
    for key, val in re.findall(r"([a-z][a-z0-9_]*)\s*[:=]?\s*(-?\d+(?:\.\d+)?)", text):
        field = _LOG_KEYS.get(key)
        if field:
            args[field] = int(float(val)) if field == "fish_count" else float(val)
    for mode in ("shade", "poly", "heated"):
        if mode in text:
            args["greenhouse"] = mode
    return args


def parse_item_numbers(text: str) -> list[int]:
    """Parse `/approve 1 3` or `/reject 2,4` into item numbers.

    Accepts the separators people actually type (spaces, commas, "and") and nothing else:
    no ranges, no "all". A range is one typo away from approving something unread, and this
    is the path where a mis-parse gets recorded as a human decision about live fish. If the
    operator wants four items they can type four numbers.

    Pure and unit-tested. Order is dropped and duplicates collapse, because the store keys
    by position and deciding the same item twice is not a second decision.
    """
    import re
    return sorted({int(n) for n in re.findall(r"\d+", text or "") if 0 < int(n) < 1000})

def _numbers(rest: str) -> list[int]:
    """Item numbers for /approve and /reject.

    Accepts the separators people type (spaces, commas, "and") and nothing else — no
    ranges, no "all". A range is one hyphen away from approving items nobody read, and
    this path records a human decision about live fish.
    """
    return parse_item_numbers(rest)


def _days_and_greenhouse(rest: str) -> tuple[int, str]:
    days, greenhouse = 7, "poly"
    for word in (rest or "").split():
        if word.isdigit():
            days = max(2, min(int(word), 15))
        elif word.lower() in ("shade", "poly", "heated"):
            greenhouse = word.lower()
    return days, greenhouse


def dispatch(agent, channel: str, identity: str, text: str) -> Reply | None:
    """Run the slash command in `text`, or return None when it is not one.

    None means "not a command, hand it to the agent" — an unknown slash word still returns
    a Reply, because a user who typed `/forcast` wants to be told, not to have their typo
    quietly answered by an LLM.
    """
    if not looks_like_command(text):
        return None
    name, rest = _split(text)

    if name in ("help", "start"):
        return Reply(HELP)

    if name == "log":
        args = parse_log_args(rest)
        if not args:
            return Reply("Give me readings, e.g.\n/log ammonia 0.5 nitrate 40 temp 27\n"
                         "(keys: ammonia, nitrite/no2, nitrate/no3, temp, weight, count; "
                         "add shade/poly/heated for your cover)")
        return Reply(agent.log_readings_direct(channel, identity, args))

    if name == "forecast":
        days, greenhouse = _days_and_greenhouse(rest)
        return Reply(agent.forecast_direct(channel, identity, days, greenhouse))

    if name == "advise":
        days, greenhouse = _days_and_greenhouse(rest)
        return Reply(agent.advise_direct(channel, identity, days, greenhouse))

    if name in ("approve", "reject"):
        nums = _numbers(rest)
        if not nums:
            return Reply(f"Which items? e.g. /{name} 1 3  (the numbers from /advise)")
        return Reply(agent.decide_direct(channel, identity, name == "approve", nums))

    if name in ("good", "bad"):
        return Reply(agent.record_feedback(channel, identity, name == "good"))

    if name == "whoami":
        return Reply("Here's what I remember:\n\n" + agent.profile_text(channel, identity))

    if name == "export":
        data = agent.export_user_data(channel, identity)
        # A data-rights export is a file, not a chat message: the JSON runs to thousands of
        # lines and WhatsApp would truncate it. Written to a temp file for the adapter to
        # upload; channels that cannot send files still get the explanation.
        path = Path(tempfile.gettempdir()) / "agronaut_my_data.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return Reply("Everything I hold about you, in open JSON. /delete_me erases it all.",
                     document=str(path))

    if name == "reset":
        agent.reset(channel, identity)
        return Reply("Cleared this conversation. I still remember your system — "
                     "/forget wipes that too.")

    if name == "forget":
        agent.forget_everything(channel, identity)
        return Reply("Done — I've wiped everything I knew about your system. Clean slate.")

    if name in ("delete_me", "deleteme"):
        agent.delete_me(channel, identity)
        return Reply("Erased. Conversation, profile, memories, readings and decisions are "
                     "all gone.")

    if name in ("design", "optimize", "troubleshoot"):
        # Telegram uses these as mode switches with their own prompts; here they are hints
        # that go to the agent as ordinary text rather than silently doing nothing.
        return None

    return Reply(f"I don't know /{name}. Try /help for the list.")
