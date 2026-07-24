"""The agent's image seam: a photo becomes a visual observation that runs through the
NORMAL text turn — so memory, the trust-gated tools, and cited RAG all still apply. The
vision model observes; it never feeds numbers into tools.
"""

from langchain_core.messages import AIMessage

from agronaut_agent.core import AgronautAgent


class _EchoContext:
    """Records the composed text the agent turn receives, and replies plainly."""

    def __init__(self):
        self.last_human = None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import HumanMessage
        humans = [m for m in messages if isinstance(m, HumanMessage)]
        if humans:
            self.last_human = humans[-1].content
        return AIMessage(content="Looks like a nitrogen issue — tell me your water readings.")


def _describer(observation="Older leaves show interveinal chlorosis; new growth is green."):
    def _d(image_bytes, prompt):
        return observation
    return _d


def test_handle_image_feeds_visual_observation_into_the_turn(tmp_path):
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat,
                          describe_fn=_describer())
    reply = agent.handle_image("telegram", "img1", b"fakebytes", caption="what's wrong?")

    assert "nitrogen issue" in reply
    # the composed user turn carried BOTH the caption and the model's visual observation
    assert "what's wrong?" in chat.last_human
    assert "interveinal chlorosis" in chat.last_human
    # and it was persisted as a normal user turn (audit trail + memory continuity)
    roles = [m["role"] for m in agent._conv.recent_messages("telegram:img1")]
    assert roles[0] == "user" and roles[-1] == "assistant"


def test_handle_image_without_caption_still_works(tmp_path):
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat,
                          describe_fn=_describer())
    agent.handle_image("telegram", "img2", b"fakebytes", caption=None)
    assert "interveinal chlorosis" in chat.last_human


def test_handle_image_degrades_when_no_describer(tmp_path):
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat, describe_fn=None)
    reply = agent.handle_image("telegram", "img3", b"fakebytes", caption="help")
    assert "can't look at images" in reply.lower() or "can't see images" in reply.lower()
    # nothing about the image was fabricated into the turn
    assert chat.last_human is None


def test_handle_image_survives_a_describer_error(tmp_path):
    def _boom(image_bytes, prompt):
        raise RuntimeError("vlm timeout")

    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat, describe_fn=_boom)
    reply = agent.handle_image("telegram", "img4", b"fakebytes", caption="help")
    assert "couldn't" in reply.lower() or "try again" in reply.lower()
