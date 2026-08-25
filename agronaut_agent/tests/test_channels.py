"""Channel abstraction: the agent brain is platform-agnostic (no python-telegram-bot
import anywhere in agronaut_agent except the Telegram adapter), a group chat gives each
member their own memory, and the REPL is a second ChannelAdapter instance proving the
contract isn't Telegram-shaped.
"""

import ast
import pathlib

import pytest

from agronaut_agent.channels import base


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _imports(pyfile: pathlib.Path):
    tree = ast.parse(pyfile.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_agent_brain_imports_no_telegram():
    # Every module under agronaut_agent EXCEPT the telegram adapter must be free of
    # python-telegram-bot — the brain never depends on a channel.
    offenders = []
    for py in (ROOT / "agronaut_agent").rglob("*.py"):
        if py.name == "telegram_adapter.py" or "/tests/" in py.as_posix():
            continue
        if any(m == "telegram" or m.startswith("telegram.") for m in _imports(py)):
            offenders.append(py.relative_to(ROOT).as_posix())
    assert offenders == [], f"telegram imported in brain modules: {offenders}"


def test_private_chat_identity_unchanged():
    # private chat: chat.id == user.id -> identity is just the chat id (byte-identical
    # to the pre-abstraction behaviour, so existing memory keys are preserved)
    assert base.room_identity(chat_id=555, chat_type="private", user_id=555) == "555"
    assert base.room_identity(chat_id=555, chat_type="private", user_id=555) == str(555)


def test_group_chat_gives_each_member_distinct_identity():
    a = base.room_identity(chat_id=-100, chat_type="supergroup", user_id=111)
    b = base.room_identity(chat_id=-100, chat_type="supergroup", user_id=222)
    assert a != b, "two members of one group must not share a profile"
    assert a == "-100:111" and b == "-100:222"


def test_delivery_chat_id_handles_plain_and_composite():
    # follow-up delivery target: plain private id, or the room part of a composite key
    assert base.delivery_chat_id("555") == 555
    assert base.delivery_chat_id("-100:222") == -100   # deliver to the room it happened in


def test_repl_channel_is_a_channel_adapter():
    from agronaut_agent.channels.repl import ReplChannel
    assert issubclass(ReplChannel, base.ChannelAdapter)
    import inspect
    assert not any("telegram" in m for m in _imports(
        ROOT / "agronaut_agent" / "channels" / "repl.py"))
