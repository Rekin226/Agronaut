"""The tool-loop, exercised with a fake chat model (no network).

Asserts the agent invokes the requested tool, feeds the result back as a ToolMessage,
returns a final answer that carries the tool's numbers, persists an audit trail, and
extracts user facts deterministically.
"""

from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent.core import AgronautAgent


class _FakeChat:
    """Turn 1 -> a tool call; turn 2 (after a ToolMessage) -> a final answer."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if tool_msgs:
            saw_numbers = "fish=" in tool_msgs[-1].content
            return AIMessage(content=f"Sized it (numbers_from_tool={saw_numbers}).")
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "size_aquaponics_system",
                "id": "call_1",
                "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                         "temperature_c": 27, "water_budget_lpd": 300},
            }],
        )


class _ChattyFake:
    """Never calls a tool — just replies. Verifies the no-tool path."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Hello! Tell me about your system.")


def test_tool_loop_calls_tool_and_returns_numbers(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_FakeChat())
    reply = agent.handle_message("cli", "tester", "size a 12 m2 tilapia + lettuce at 27C, 300 L/day")
    assert "numbers_from_tool=True" in reply

    roles = [m["role"] for m in agent._conv.recent_messages("cli:tester", limit=10)]
    assert roles == ["user", "tool", "assistant"]  # audit trail persisted
    # temperature_c now comes from validated tool args (27) instead of parsed text (27.0)
    assert agent._mem.get_facts("cli:tester")["temperature_c"] == "27"


def test_no_tool_path_returns_plain_reply(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    reply = agent.handle_message("cli", "u2", "hi there")
    assert "Hello" in reply
    assert [m["role"] for m in agent._conv.recent_messages("cli:u2")] == ["user", "assistant"]


def test_reset_clears_history(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    agent.handle_message("cli", "u3", "hi")
    agent.reset("cli", "u3")
    assert agent._conv.recent_messages("cli:u3") == []


class _RememberFake:
    """Turn 1 -> call remember_about_user; then -> final text."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Noted your setup.")
        return AIMessage(content="", tool_calls=[{
            "name": "remember_about_user", "id": "c1",
            "args": {"note": "Runs a 3000 L IBC system in Burkina Faso", "category": "profile"}}])


def test_agent_curates_and_recalls_memory(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_RememberFake())
    agent.handle_message("telegram", "9", "I run a 3000L IBC setup in Burkina")
    # the memory tool persisted the note
    assert agent._mem.memory_count("telegram:9") == 1
    # and it surfaces in the recall block injected on the next turn (cross-session)
    assert "3000 L IBC" in agent._recall_block("telegram:9")


class _LoopForeverFake:
    """Always calls a tool — until told (via a system message) to reply in plain text.
    Exercises the iteration-cap -> forced-final-answer path."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import SystemMessage
        if any(isinstance(m, SystemMessage) and "Do not call any more tools" in m.content
               for m in messages):
            return AIMessage(content="Here's the summary you asked for.")
        return AIMessage(content="", tool_calls=[{
            "name": "list_supported_species_and_crops", "id": "x", "args": {}}])


def test_iteration_cap_forces_a_final_text_answer(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_LoopForeverFake())
    reply = agent.handle_message("cli", "loop", "tell me everything")
    # never returns the give-up fallback; forces a real reply with tools disabled
    assert "Here's the summary you asked for." in reply


def test_reset_keeps_memory_forget_wipes_it(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_RememberFake())
    agent.handle_message("telegram", "9", "I run a 3000L IBC setup")
    agent.reset("telegram", "9")
    assert agent._mem.memory_count("telegram:9") == 1   # memory survives a conversation reset
    agent.forget_everything("telegram", "9")
    assert agent._mem.memory_count("telegram:9") == 0   # forget wipes it


def test_recall_renders_profile_and_missing_essentials(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    uid = agent._conv.get_or_create_user("cli", "recall")
    agent._mem.set_facts(uid, {"goal": "design", "fish_species": "tilapia"})

    block = agent._recall_block(uid)
    assert "YOUR SYSTEM" in block
    assert "tilapia" in block
    # the deterministic nudge lists exactly the still-blank design essentials
    assert "Still need for design:" in block
    for key in ("crop", "grow_area_m2", "temperature_c", "water_budget_lpd"):
        assert key in block


class _ConsultFake:
    """Turn 1 -> call update_profile with what the user revealed; then -> a question."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Got it. What's your daily water budget?")
        return AIMessage(content="", tool_calls=[{
            "name": "update_profile", "id": "u1",
            "args": {"updates": {"goal": "design", "fish_species": "tilapia",
                                 "crop": "lettuce"}}}])


def test_consultation_persists_profile_via_tool(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ConsultFake())
    reply = agent.handle_message("telegram", "c1", "I want to set up tilapia and lettuce")
    assert "water budget" in reply
    facts = agent._mem.get_facts("telegram:c1")
    assert facts["goal"] == "design"
    assert facts["fish_species"] == "tilapia"
    assert facts["crop"] == "lettuce"


def test_system_prompt_is_consultative():
    from agronaut_agent.core import SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    assert "goal" in lowered
    assert "update_profile" in lowered
    assert "essential" in lowered
    # the old answer-dump instruction is gone
    assert "answer directly" not in lowered


def test_tool_args_persist_to_profile_without_update_profile(tmp_path):
    # _FakeChat sizes a system but never calls update_profile; the profile must still fill.
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_FakeChat())
    agent.handle_message("cli", "cap", "size a 12 m2 tilapia + lettuce at 27C, 300 L/day")
    facts = agent._mem.get_facts("cli:cap")
    assert facts["crop"] == "lettuce"
    assert facts["grow_area_m2"] == "12"
    assert facts["water_budget_lpd"] == "300"


class _BoomChat:
    """A primary model that always fails — exercises the resilience fallback."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise RuntimeError("primary model is down (timeout/starved)")


def test_falls_back_when_primary_errors(tmp_path):
    # primary raises on every call; the injected fallback (_ChattyFake) answers instead.
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3",
                          chat_model=_BoomChat(), fallback_model=_ChattyFake())
    reply = agent.handle_message("cli", "fb", "hi there")
    assert "Hello" in reply  # the fallback produced the answer; the turn was not lost


def test_primary_error_propagates_without_a_fallback(tmp_path):
    import pytest
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_BoomChat())
    with pytest.raises(RuntimeError):
        agent.handle_message("cli", "nofb", "hi")


def test_set_goal_persists_and_confirms(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    msg = agent.set_goal("cli", "mode", "design")
    assert "Design mode" in msg
    assert agent._mem.get_facts("cli:mode")["goal"] == "design"


def test_set_goal_does_not_reset_conversation(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    agent.handle_message("cli", "mode2", "hi")          # one turn of history
    agent.set_goal("cli", "mode2", "troubleshoot")
    # history survives a mode switch (mode only refocuses the goal)
    assert len(agent._conv.recent_messages("cli:mode2")) >= 1


def test_set_goal_rejects_unknown_goal(tmp_path):
    import pytest
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    with pytest.raises(ValueError):
        agent.set_goal("cli", "mode3", "frobnicate")


def test_agent_exposes_followup_delivery_api(tmp_path):
    from agronaut_agent.store import _now
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    # schedule a past-due follow-up directly via the agent's store
    agent._followups.schedule("telegram:5", "telegram", "5", "did it work?", "x",
                              "2000-01-01T00:00:00+00:00")
    due = agent.due_followups("telegram")
    assert len(due) == 1 and due[0]["question"] == "did it work?"
    fid = due[0]["id"]
    agent.mark_followup_sent(fid)
    assert agent.due_followups("telegram") == []          # sent -> not due again

    # send-failure path: 3 strikes -> failed
    agent._followups.schedule("telegram:6", "telegram", "6", "q", "x",
                              "2000-01-01T00:00:00+00:00")
    fid2 = agent.due_followups("telegram")[0]["id"]
    agent.followup_send_failed(fid2)
    agent.followup_send_failed(fid2)
    agent.followup_send_failed(fid2)
    assert agent._followups.open_for("telegram:6") is None  # failed after 3 attempts


class _LearningFake:
    """Turn 1 -> save a learning memory; then -> final text. Mimics the model capturing
    an outcome when it sees the capture note."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Glad it worked!")
        return AIMessage(content="", tool_calls=[{
            "name": "remember_about_user", "id": "c1",
            "args": {"note": "30% water change fixed the ammonia spike", "category": "learning"}}])


def test_sent_followup_is_answered_on_next_reply(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_LearningFake())
    # a follow-up was delivered and is awaiting the user's answer
    agent._followups.schedule("telegram:9", "telegram", "9", "did it work?", "ammonia",
                              "2000-01-01T00:00:00+00:00")
    agent._followups.mark_sent(agent._followups.open_for("telegram:9")["id"])
    agent.handle_message("telegram", "9", "yes it worked great")
    assert agent._followups.open_for("telegram:9") is None        # answered -> slot freed
    assert agent._mem.memory_count("telegram:9") == 1             # outcome saved as learning


def test_pending_followup_cancelled_when_user_messages_first(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    agent._followups.schedule("telegram:10", "telegram", "10", "did it work?", "x",
                              "2999-01-01T00:00:00+00:00")  # not yet due/sent
    agent.handle_message("telegram", "10", "hey, new question")
    assert agent._followups.open_for("telegram:10") is None       # cancelled, not asked


def test_system_prompt_mentions_followups_and_outcomes():
    from agronaut_agent.core import SYSTEM_PROMPT
    low = SYSTEM_PROMPT.lower()
    assert "schedule_followup" in low
    assert "worked" in low  # capture outcomes as learnings


class _NominateFake:
    """Turn 1 -> nominate a shared insight; then -> final text."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Shared for review.")
        return AIMessage(content="", tool_calls=[{
            "name": "nominate_shared_insight", "id": "n1",
            "args": {"insight": "a partial water change commonly clears an acute ammonia spike",
                     "topic": "ammonia"}}])


def test_nomination_reaches_the_community_store(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_NominateFake())
    agent.handle_message("telegram", "1", "the 30% water change fixed my ammonia")
    pend = agent._community.pending()
    assert len(pend) == 1
    assert pend[0]["insight"].startswith("a partial water change")


def test_system_prompt_mentions_community_sharing():
    from agronaut_agent.core import SYSTEM_PROMPT
    low = SYSTEM_PROMPT.lower()
    assert "nominate_shared_insight" in low
    assert "search_community_knowledge" in low
