"""House style for replies, applied once after the model answers.

The prompt asks for short, plain, dash-free messages, but a prompt is a request, not a
guarantee: small local models drift back to em dashes and Markdown headers within a few
turns. This module makes the parts of the house style that CAN be deterministic actually
deterministic, so they hold on every model and every channel.

Two jobs:
- `polish_reply` rewrites text: no em dashes anywhere, en dashes in number ranges become
  "to", and Markdown is converted to what each chat app actually renders (Telegram is sent
  with no parse mode, so `**bold**` would show its asterisks; WhatsApp bolds with `*x*`).
- `to_bubbles` splits a reply into a few chat bubbles on blank lines, so a phone shows
  "Nice, love that." and "Is this for home food or to sell?" as two messages, the way a
  person texts, instead of one block.

Fenced code blocks are left untouched by both.
"""

from __future__ import annotations

import re

CHAT_CHANNELS = frozenset({"telegram", "whatsapp"})
MAX_BUBBLES = 3

_FENCE = re.compile(r"(```.*?```)", re.S)

# Em dash (U+2014), plus the double-hyphen some models type in its place.
_EM = "—"
_EN = "–"
# A bare "--" counts only when it stands alone, so "---" rules and "--help" flags survive.
_DD = r"(?<![-\w])--(?![-\w])"
_EM_AT_LINE_END = re.compile(rf"[ \t]*(?:{_EM}|{_DD})[ \t]*$", re.M)
_EM_AT_LINE_START = re.compile(rf"^([ \t]*)(?:{_EM}|{_DD})[ \t]*", re.M)
_EM_INLINE = re.compile(rf"[ \t]*(?:{_EM}|{_DD})[ \t]*")
_EN_RANGE = re.compile(rf"(\d)[ \t]*{_EN}[ \t]*(?=\d)")
_EN_INLINE = re.compile(rf"[ \t]+{_EN}[ \t]+")
_DOUBLE_COMMA = re.compile(r",\s*,")
_COMMA_BEFORE_STOP = re.compile(r",\s*([.!?:;)])")

_BOLD_STARS = re.compile(r"\*\*(.+?)\*\*", re.S)
_BOLD_UNDERSCORE = re.compile(r"__(.+?)__", re.S)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _dedash(text: str) -> str:
    text = _EM_AT_LINE_END.sub(":", text)
    text = _EM_AT_LINE_START.sub(r"\1- ", text)
    text = _EM_INLINE.sub(", ", text)
    text = _EN_RANGE.sub(r"\1 to ", text)
    text = _EN_INLINE.sub(", ", text)
    text = text.replace(_EN, "-")
    text = _DOUBLE_COMMA.sub(",", text)
    text = _COMMA_BEFORE_STOP.sub(r"\1", text)
    return text


def _telegram_markup(text: str) -> str:
    text = _HEADING.sub(r"\1", text)
    text = _BOLD_STARS.sub(r"\1", text)
    text = _BOLD_UNDERSCORE.sub(r"\1", text)
    text = _LINK.sub(r"\1 (\2)", text)
    text = _INLINE_CODE.sub(r"\1", text)
    return text


def _whatsapp_markup(text: str) -> str:
    text = _HEADING.sub(r"*\1*", text)
    text = _BOLD_STARS.sub(r"*\1*", text)
    text = _BOLD_UNDERSCORE.sub(r"_\1_", text)
    text = _LINK.sub(r"\1 (\2)", text)
    return text


def _outside_fences(text: str, fn) -> str:
    parts = _FENCE.split(text)
    return "".join(p if p.startswith("```") else fn(p) for p in parts)


def polish_reply(text: str, channel: str) -> str:
    """Apply the house style to a model reply for `channel`. Idempotent."""
    if not text:
        return text
    ch = (channel or "").lower()

    def _fix(segment: str) -> str:
        segment = _dedash(segment)
        if ch == "telegram":
            segment = _telegram_markup(segment)
        elif ch == "whatsapp":
            segment = _whatsapp_markup(segment)
        return segment

    return _outside_fences(text, _fix).strip()


def to_bubbles(text: str, channel: str, max_bubbles: int = MAX_BUBBLES) -> list[str]:
    """Split a reply into at most `max_bubbles` chat messages on blank lines.

    Only chat channels are split; a web page or terminal keeps one block. When a reply has
    more paragraphs than bubbles, the tail stays together in the last bubble, so a list or a
    closing question is never scattered across several notifications.
    """
    text = (text or "").strip()
    if not text:
        return []
    if (channel or "").lower() not in CHAT_CHANNELS or "```" in text:
        return [text]
    paras = [p.strip() for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    if len(paras) <= max_bubbles:
        return paras
    return paras[: max_bubbles - 1] + ["\n\n".join(paras[max_bubbles - 1:])]
