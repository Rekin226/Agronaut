"""Read a PDF a grower sends (a water-lab report, a feed label, an invoice) on Claude.

Claude reads PDFs natively, text and page images both, so a scanned lab report works as well
as a generated one. Like a photo, a document is read into plain text and then runs through
the NORMAL turn, so memory, the trust-gated tools and cited knowledge all still apply.

Unlike a photo observation, the reading keeps its numbers: a lab report is its numbers. They
are passed on as UNCONFIRMED values read from a document, and the turn is told to show them to
the grower and get a yes before logging or using any of them. Memory takes facts from the
caption only, never from the reading, the same rule as photos (core.handle_message's
fact_text). Every number that reaches a tool still meets validate_design_input there.

Only on Claude: no other provider here takes a PDF, and an operator on a local model gets an
honest "send a photo of the page instead".
"""

from __future__ import annotations

import base64
import logging

log = logging.getLogger(__name__)

# A water report or a feed label is a few pages. The cap bounds cost: each page is read as
# text and as an image, roughly 1,500 to 3,000 tokens a page.
MAX_PDF_BYTES = 5 * 1024 * 1024

_READ = """Read this document for an aquaponics or hydroponics grower. Report only what it says.

1. What it is (water lab test, feed label, invoice, extension leaflet, other) and who issued it.
2. Its date, if printed.
3. Every measured or stated value, one per line, as "parameter: value unit", copied exactly as
   printed (for example "pH: 7.8", "Ammonia (NH3-N): 0.5 mg/L"). Keep the printed unit. If a
   value is unreadable, write "parameter: unreadable".
4. Any limit or reference range the document itself prints next to a value.

Do not interpret, diagnose, recommend or convert anything. Plain text, no tables."""


def is_pdf(data: bytes, mime: str | None = None) -> bool:
    return (mime or "").lower() == "application/pdf" or (data or b"")[:5] == b"%PDF-"


def read_pdf(data: bytes, caption: str | None = None, chat=None) -> str:
    """The document's contents as plain text, from Claude. `chat` is injectable for tests;
    by default it is the configured Claude model."""
    from langchain_core.messages import HumanMessage

    from agent.llm import get_chat_model, normalize

    chat = chat or get_chat_model("anthropic")
    ask = _READ + (f"\n\nThe grower wrote with it: {caption.strip()}" if caption and caption.strip()
                   else "")
    block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.standard_b64encode(data).decode()}}
    return normalize(chat.invoke([HumanMessage(content=[block, {"type": "text", "text": ask}])]))
