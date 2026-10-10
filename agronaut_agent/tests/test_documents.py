"""PDFs (a water-lab report, a feed label): read on Claude, values unconfirmed until the user says so."""

from langchain_core.messages import AIMessage, HumanMessage

from agent import documents
from agronaut_agent.core import AgronautAgent

PDF = b"%PDF-1.4 fake lab report"
READING = "1. Water lab test, Labo Eau Ouaga.\n3. pH: 7.8\nAmmonia (NH3-N): 0.5 mg/L"


class _Chat:
    def __init__(self):
        self.seen = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        return AIMessage(content="Your report shows pH 7.8. Is that right before I log it?")


def _agent(tmp_path, reader):
    chat = _Chat()
    a = AgronautAgent(chat_model=chat, db_path=str(tmp_path / "db.sqlite"), read_pdf_fn=reader)
    return a, chat


def test_a_pdf_reading_runs_as_a_normal_turn_marked_unconfirmed(tmp_path):
    a, chat = _agent(tmp_path, lambda data, caption: READING)
    reply = a.handle_document("test", "u1", PDF, "application/pdf", caption="my water test")
    sent = [m for m in chat.seen[0] if isinstance(m, HumanMessage)][-1].content
    assert "NOT yet confirmed" in sent and "pH: 7.8" in sent
    assert "ask the user to confirm" in sent and sent.rstrip().endswith("my water test")
    assert reply.startswith("Your report shows")


def test_memory_takes_facts_from_the_caption_never_from_the_reading(tmp_path):
    a, _ = _agent(tmp_path, lambda data, caption: "Grow area: 40 m2\npH: 7.8")
    a.handle_document("test", "u1", PDF, "application/pdf", caption=None)
    facts = a._mem.get_facts(a._conv.get_or_create_user("test", "u1"))
    assert "grow_area_m2" not in facts


def test_what_cannot_be_read_is_declined_honestly(tmp_path):
    a, chat = _agent(tmp_path, lambda data, caption: READING)
    assert "can't read that kind of file" in a.handle_document("test", "u1", b"PK\x03\x04", "application/zip")
    assert "too large" in a.handle_document("test", "u1", b"%PDF-" + b"0" * documents.MAX_PDF_BYTES)
    assert "couldn't find any text" in _agent(tmp_path / "e", lambda d, c: "  ")[0].handle_document(
        "test", "u1", PDF, "application/pdf")
    no_reader, _ = _agent(tmp_path / "n", None)
    assert "can't read PDFs on this setup" in no_reader.handle_document("test", "u1", PDF)
    assert chat.seen == []                        # none of those reached the model


def test_the_reader_sends_the_pdf_as_a_native_document_block():
    chat = _Chat()
    out = documents.read_pdf(PDF, caption="is my ammonia ok?", chat=chat)
    content = chat.seen[0][0].content
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert "copied exactly as" in content[1]["text"] and "is my ammonia ok?" in content[1]["text"]
    assert out.startswith("Your report shows")
    assert documents.is_pdf(PDF) and documents.is_pdf(b"x", "application/pdf")
    assert not documents.is_pdf(b"PK", "application/zip")
