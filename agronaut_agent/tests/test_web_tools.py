"""Web search and web fetch: Claude only, capped, cited, and never a sizing input."""

from langchain_core.messages import AIMessage, HumanMessage

from agent import llm as L
from agronaut_agent.core import AgronautAgent


def test_server_tools_are_capped_and_each_can_be_switched_off(monkeypatch):
    for k in ("AGRONAUT_WEB_SEARCH", "AGRONAUT_WEB_FETCH", "AGRONAUT_WEB_SEARCH_MAX",
              "AGRONAUT_WEB_FETCH_MAX"):
        monkeypatch.delenv(k, raising=False)
    search, fetch = L.claude_server_tools()
    assert search == {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    assert fetch["type"] == "web_fetch_20250910" and fetch["max_uses"] == 2
    assert fetch["citations"] == {"enabled": True} and fetch["max_content_tokens"] == 8000
    monkeypatch.setenv("AGRONAUT_WEB_SEARCH_MAX", "5")
    monkeypatch.setenv("AGRONAUT_WEB_FETCH", "off")
    assert L.claude_server_tools() == [{"type": "web_search_20250305", "name": "web_search",
                                        "max_uses": 5}]
    monkeypatch.setenv("AGRONAUT_WEB_SEARCH", "off")
    assert L.claude_server_tools() == []


class _Chat:
    def __init__(self, replies):
        self.replies, self.bound, self.seen = list(replies), None, []

    def bind_tools(self, tools):
        self.bound = list(tools)
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        return self.replies.pop(0)


def _agent(tmp_path, chat, provider):
    a = AgronautAgent(chat_model=chat, db_path=str(tmp_path / "db.sqlite"))
    # An injected model has no provider, so the constructor bound no server tools; set what
    # the real anthropic path sets (the fake chat ignores the tool list it is bound with).
    a._provider = provider
    a._server_tools = L.claude_server_tools() if provider == "anthropic" else []
    return a


def test_only_claude_is_told_about_the_web_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("AGRONAUT_WEB_SEARCH", raising=False)
    for provider, expect in (("anthropic", True), ("ollama", False)):
        chat = _Chat([AIMessage(content="Hello.")])
        a = _agent(tmp_path / provider, chat, provider)
        a.handle_message("test", "u1", "hi")
        prompt = chat.seen[0][0].content
        assert ("WEB SEARCH AND WEB FETCH" in prompt) is expect


def test_a_paused_web_turn_is_sent_back_to_continue(tmp_path):
    paused = AIMessage(content=[{"type": "server_tool_use", "id": "s1", "name": "web_search",
                                 "input": {"query": "prix tilapia Ouagadougou"}}],
                       response_metadata={"stop_reason": "pause_turn"})
    done = AIMessage(content=[
        {"type": "text", "text": "The official cap is 2,000 FCFA per kg", "citations": [
            {"type": "web_search_result_location", "title": "Prix du tilapia plafonné",
             "url": "https://example.bf/tilapia",
             "cited_text": "le prix du tilapia local plafonné à 2 000 FCFA le kilo"}]},
        {"type": "text", "text": "."}], response_metadata={"stop_reason": "end_turn"})
    chat = _Chat([paused, done])
    a = _agent(tmp_path, chat, "anthropic")
    reply = a.handle_message("test", "u1", "what does tilapia sell for in Ouaga?")
    assert len(chat.seen) == 2 and chat.seen[1][-1] is paused
    # the web source is shown to the grower, as Anthropic requires for web results
    assert reply.endswith("Sources: Prix du tilapia plafonné (https://example.bf/tilapia)")


def test_a_figure_quoted_from_a_web_citation_counts_as_sourced(monkeypatch):
    from agronaut_agent import core

    seen = {}
    monkeypatch.setattr(core.grounding, "ungrounded",
                        lambda reply, sources: seen.setdefault("sources", sources) and [])
    ai = AIMessage(content=[{"type": "text", "text": "2,000 FCFA per kg", "citations": [
        {"type": "web_search_result_location", "cited_text": "plafonné à 2 000 FCFA le kilo"}]}])
    AgronautAgent._check_grounding("2,000 FCFA per kg", [HumanMessage(content="sys"),
                                                         HumanMessage(content="price?"), ai])
    assert "plafonné à 2 000 FCFA le kilo" in seen["sources"]


def test_web_searches_are_counted_where_the_tokens_are():
    ai = AIMessage(content="x", response_metadata={"usage": {"server_tool_use": {
        "web_search_requests": 2, "web_fetch_requests": 1}}})
    assert AgronautAgent._server_tool_usage(ai) == {"web_searches": 2, "web_fetches": 1}
    assert AgronautAgent._server_tool_usage(AIMessage(content="x")) == {}


def test_the_claude_path_binds_the_web_tools_beside_agronauts_own(tmp_path, monkeypatch):
    from agronaut_agent import core
    from agronaut_agent.tools import AGRONAUT_TOOLS

    chat = _Chat([])
    monkeypatch.setattr(core, "get_chat_model", lambda p=None, m=None: chat)
    monkeypatch.setattr(core, "build_fallback_chat", lambda p=None, m=None: None)
    monkeypatch.delenv("AGRONAUT_WEB_SEARCH", raising=False)
    off = lambda *a, **k: None  # noqa: E731
    AgronautAgent(llm_provider="anthropic", db_path=str(tmp_path / "db.sqlite"),
                  embed_fn=off, describe_fn=off, transcribe_fn=off, classify_fn=off)
    names = [getattr(t, "name", None) or t.get("name") for t in chat.bound]
    assert names[:len(AGRONAUT_TOOLS)] == [t.name for t in AGRONAUT_TOOLS]
    assert names[len(AGRONAUT_TOOLS):] == ["web_search", "web_fetch"]
