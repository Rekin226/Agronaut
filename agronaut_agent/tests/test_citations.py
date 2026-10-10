"""Native citations on Claude: knowledge passages in as search results, cited sources out."""

from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent import citations as C
from agronaut_agent.core import AgronautAgent, _text_of

KB = ("[source: knowledge/dissolved_oxygen_and_aeration.md]\n# DO\nKeep DO at 5 mg/L or more."
      "\n\n[source: FAO 589 Small-scale aquaponic food production]\nAerate at night.")


def test_labeled_passages_become_search_results():
    blocks = C.search_result_blocks(KB)
    assert [b["source"] for b in blocks] == ["knowledge/dissolved_oxygen_and_aeration.md",
                                              "FAO 589 Small-scale aquaponic food production"]
    assert blocks[0]["title"] == "Agronaut guide: dissolved oxygen and aeration"
    assert blocks[0]["content"] == [{"type": "text", "text": "# DO\nKeep DO at 5 mg/L or more."}]
    assert all(b["type"] == "search_result" and b["citations"] == {"enabled": True}
               for b in blocks)


def test_notes_without_passages_stay_text():
    assert C.search_result_blocks("No matching passages in the knowledge base for that query.") is None
    assert C.search_result_blocks("") is None


def test_cited_sources_cover_knowledge_web_and_documents_once_each():
    content = [
        {"type": "text", "text": "Keep DO up.", "citations": [
            {"type": "search_result_location", "source": "knowledge/dissolved_oxygen_and_aeration.md"},
            {"type": "search_result_location", "source": "knowledge/dissolved_oxygen_and_aeration.md"}]},
        {"type": "text", "text": "Feed costs 900 FCFA.", "citations": [
            {"type": "web_search_result_location", "title": "Prix", "url": "https://x.bf/p"}]},
        {"type": "text", "text": "Per the report.", "citations": [
            {"type": "char_location", "document_title": "Lab report"}]},
        {"type": "tool_use", "name": "x"},
    ]
    assert C.cited_sources(content) == ["Agronaut guide: dissolved oxygen and aeration",
                                        "Prix (https://x.bf/p)", "Lab report"]
    assert C.cited_sources("plain text") == []


def test_sources_line_adds_only_what_the_reply_does_not_name():
    text = "Per FAO 589, aerate at night."
    out = C.with_sources(text, ["FAO 589 Small-scale aquaponic food production",
                                "Agronaut guide: dissolved oxygen and aeration"])
    assert out == text + "\n\nSources: Agronaut guide: dissolved oxygen and aeration"
    assert C.with_sources(text, ["FAO 589 Small-scale aquaponic food production"]) == text
    assert C.with_sources("保持溶氧。", ["Prix (https://x.bf/p)"]).endswith("來源：Prix (https://x.bf/p)")
    assert C.with_sources("", ["a"]) == ""


def test_grounding_reads_the_passages_inside_search_results():
    assert "5 mg/L" in _text_of(C.search_result_blocks(KB))


class _Claude:
    """Calls the knowledge tool once, then answers citing it. Records what it was sent."""

    def __init__(self):
        self.seen = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        if not any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="", tool_calls=[{"name": "search_knowledge_base",
                                                      "args": {"query": "low oxygen"}, "id": "c1"}])
        return AIMessage(content=[{"type": "text", "text": "Run an air pump at night.",
                                   "citations": [{"type": "search_result_location",
                                                  "source": "knowledge/dissolved_oxygen_and_aeration.md"}]}])


def _agent(tmp_path, monkeypatch, provider):
    from agronaut_agent import tools
    monkeypatch.setattr(tools.rag, "search_with_stats", lambda q: (KB, {"outcome": "hit"}))
    chat = _Claude()
    a = AgronautAgent(chat_model=chat, db_path=str(tmp_path / "db.sqlite"))
    a._provider = provider
    return a, chat


def test_on_claude_the_knowledge_tool_answers_with_search_results(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRONAUT_NATIVE_CITATIONS", raising=False)
    a, chat = _agent(tmp_path, monkeypatch, "anthropic")
    reply = a.handle_message("test", "u1", "fish gasping at dawn")
    tool_msg = [m for m in chat.seen[-1] if isinstance(m, ToolMessage)][0]
    assert tool_msg.content[0]["type"] == "search_result"
    assert "Sources: Agronaut guide: dissolved oxygen and aeration" in reply
    # history keeps the labeled text, so replays and other providers are unchanged
    stored = a._conv.recent_context_messages(a._conv.get_or_create_user("test", "u1"), limit=10)
    assert any(m["role"] == "tool" and m["content"].startswith("[source:") for m in stored)


def test_other_providers_and_the_off_switch_keep_labeled_text(tmp_path, monkeypatch):
    for provider, env in (("ollama", None), ("anthropic", "off")):
        if env:
            monkeypatch.setenv("AGRONAUT_NATIVE_CITATIONS", env)
        a, chat = _agent(tmp_path / provider, monkeypatch, provider)
        a.handle_message("test", "u1", "fish gasping at dawn")
        tool_msg = [m for m in chat.seen[-1] if isinstance(m, ToolMessage)][0]
        assert tool_msg.content.startswith("[source:")
