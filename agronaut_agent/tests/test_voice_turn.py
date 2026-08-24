"""The agent's voice seam: a voice note is transcribed, then run through the NORMAL text
turn — so memory, trust-gated tools, and cited RAG apply, and the existing "reply in the
user's language" prompt rule already answers a French note in French.
"""

from langchain_core.messages import AIMessage, HumanMessage

from agronaut_agent.core import AgronautAgent


class _EchoContext:
    def __init__(self):
        self.last_human = None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        humans = [m for m in messages if isinstance(m, HumanMessage)]
        if humans:
            self.last_human = humans[-1].content
        return AIMessage(content="Depuis quand sont-ils malades ?")


def _transcriber(text="mes tilapias sont malades"):
    def _t(audio_bytes, mime):
        return text
    return _t


def test_handle_voice_feeds_transcript_into_the_turn(tmp_path):
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat,
                          transcribe_fn=_transcriber())
    reply = agent.handle_voice("telegram", "v1", b"oggbytes", "audio/ogg")
    assert "malades" in reply
    assert chat.last_human == "mes tilapias sont malades"   # transcript IS the user turn
    roles = [m["role"] for m in agent._conv.recent_messages("telegram:v1")]
    assert roles[0] == "user" and roles[-1] == "assistant"


def test_handle_voice_degrades_when_no_transcriber(tmp_path):
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat, transcribe_fn=None)
    reply = agent.handle_voice("telegram", "v2", b"oggbytes", "audio/ogg")
    assert "voice" in reply.lower() and ("type" in reply.lower() or "text" in reply.lower())
    assert chat.last_human is None


def test_handle_voice_survives_a_transcriber_error(tmp_path):
    def _boom(audio_bytes, mime):
        raise RuntimeError("asr timeout")

    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat, transcribe_fn=_boom)
    reply = agent.handle_voice("telegram", "v3", b"oggbytes", "audio/ogg")
    assert "couldn't" in reply.lower() or "try again" in reply.lower()


def test_handle_voice_handles_empty_transcript(tmp_path):
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat,
                          transcribe_fn=_transcriber(text="   "))
    reply = agent.handle_voice("telegram", "v4", b"oggbytes", "audio/ogg")
    assert "didn't catch" in reply.lower() or "couldn't make out" in reply.lower()
    assert chat.last_human is None


def test_voice_transcript_still_yields_facts(tmp_path):
    # A transcript IS the user's own words, so deterministic extraction stays ON for voice —
    # only model-DERIVED text (the vision observation) is excluded.
    chat = _EchoContext()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=chat,
                          transcribe_fn=lambda b, m: "my pH is 6.4 and the fish are slow")
    agent.handle_voice("telegram", "v9", b"oggbytes", "audio/ogg")
    user_id = agent._conv.get_or_create_user("telegram", "v9")
    assert agent._mem.get_facts(user_id).get("ph") == "6.4"
