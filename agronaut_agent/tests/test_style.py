"""House style: what the user actually sees on their phone.

The prompt asks for short, dash-free, plain messages; these tests pin the deterministic
half of that, so it holds on a small local model that ignores the request."""

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from agronaut_agent.style import polish_reply, to_bubbles

EM, EN = "—", "–"


# --- dashes ------------------------------------------------------------------------

@pytest.mark.parametrize("channel", ["telegram", "whatsapp", "web", "cli"])
def test_no_em_dash_survives_on_any_channel(channel):
    out = polish_reply(f"Tilapia {EM} hardy fish {EM} suit you.\nThe plan {EM}\n{EM} step one",
                       channel)
    assert EM not in out
    assert out == "Tilapia, hardy fish, suit you.\nThe plan:\n- step one"


def test_number_ranges_read_as_to():
    assert polish_reply(f"Keep pH 6.8{EN}7.2 and 24 {EN} 30 C.", "whatsapp") == \
        "Keep pH 6.8 to 7.2 and 24 to 30 C."


def test_flags_rules_and_hyphens_are_left_alone():
    text = "Run agronaut --help\n---\nA well-run system"
    assert polish_reply(text, "telegram") == text


def test_no_dangling_comma_before_punctuation():
    assert polish_reply(f"Change the water {EM}.", "telegram") == "Change the water."


def test_polish_is_idempotent():
    once = polish_reply(f"**Tip** {EM} test pH 6{EN}7", "whatsapp")
    assert polish_reply(once, "whatsapp") == once


def test_code_fences_are_untouched():
    text = f"Try this:\n```\nfoo {EM} **bar**\n```"
    assert f"foo {EM} **bar**" in polish_reply(text, "telegram")


# --- markup per channel ---------------------------------------------------------------

def test_telegram_gets_plain_text_because_it_is_sent_without_a_parse_mode():
    out = polish_reply("## Your plan\n**Tilapia** fits. See [FAO](https://fao.org). "
                       "Use `/log`.", "telegram")
    assert out == "Your plan\nTilapia fits. See FAO (https://fao.org). Use /log."


def test_whatsapp_gets_its_own_bold():
    out = polish_reply("## Your plan\n**Tilapia** fits.", "whatsapp")
    assert out == "*Your plan*\n*Tilapia* fits."


def test_web_keeps_markdown():
    assert polish_reply("**Tilapia** fits.", "web") == "**Tilapia** fits."


# --- bubbles ------------------------------------------------------------------------

def test_blank_lines_become_bubbles_on_chat_apps():
    reply = "Nice, love that.\n\nIs this for home food or to sell?"
    assert to_bubbles(reply, "whatsapp") == ["Nice, love that.",
                                             "Is this for home food or to sell?"]


def test_bubbles_are_capped_and_the_tail_stays_together():
    parts = to_bubbles("a\n\nb\n\nc\n\nd", "telegram")
    assert parts == ["a", "b", "c\n\nd"]


def test_web_and_code_are_never_split():
    assert to_bubbles("a\n\nb", "web") == ["a\n\nb"]
    assert to_bubbles("a\n\n```\nx\n\ny\n```", "telegram") == ["a\n\n```\nx\n\ny\n```"]


def test_empty_reply_sends_nothing():
    assert to_bubbles("   ", "whatsapp") == []


# --- the prompt and fixed texts practise what they preach ------------------------------

def test_system_prompt_has_no_dashes_to_imitate():
    from agronaut_agent.core import SYSTEM_PROMPT
    assert EM not in SYSTEM_PROMPT and EN not in SYSTEM_PROMPT


def test_system_prompt_asks_one_question_at_a_time():
    from agronaut_agent.core import SYSTEM_PROMPT
    low = SYSTEM_PROMPT.lower()
    assert "one question per message" in low
    assert "2–4 at once" not in SYSTEM_PROMPT and "2-4 at once" not in SYSTEM_PROMPT
    for stage in ("connect", "understand", "reflect", "recommend", "follow through"):
        assert stage in low


def test_fixed_texts_have_no_em_dash():
    from agronaut_agent import profile
    from agronaut_agent.channels import commands

    texts = [commands.HELP, *profile.GOAL_PROMPTS.values(),
             *(q for pair in profile._QUESTIONS.values() for q in pair)]
    assert all(EM not in t for t in texts)


# --- wiring: the reply the user gets, and the one the model sees next turn ------------

class _DashyFake:
    """Replies the way small models drift: em dashes and Markdown headers."""

    def __init__(self):
        self.seen = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))   # the loop appends to the same list later
        return AIMessage(content=f"## Plan\n**Tilapia** {EM} great choice.\n\nWhere are you?")


def test_reply_is_polished_before_it_is_stored(tmp_path):
    from agronaut_agent.core import AgronautAgent

    fake = _DashyFake()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=fake)
    reply = agent.handle_message("whatsapp", "886", "I want to start aquaponics")
    assert reply == "*Plan*\n*Tilapia*, great choice.\n\nWhere are you?"

    agent.handle_message("whatsapp", "886", "Ouagadougou")
    replayed = [m.content for m in fake.seen[-1] if isinstance(m, AIMessage)]
    assert replayed and all(EM not in c for c in replayed)


def test_the_model_is_told_which_app_it_is_texting_on(tmp_path):
    from agronaut_agent.core import AgronautAgent

    fake = _DashyFake()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=fake)
    agent.handle_message("telegram", "7", "hi")
    notes = " ".join(m.content for m in fake.seen[0] if isinstance(m, SystemMessage))
    assert "texting on Telegram" in notes


def test_recall_names_exactly_one_next_question(tmp_path):
    from agronaut_agent.core import AgronautAgent

    fake = _DashyFake()
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=fake)
    uid = agent._conv.get_or_create_user("cli", "one")
    agent._mem.set_facts(uid, {"goal": "design", "fish_species": "tilapia"})
    block = agent._recall_block(uid)
    ask = [line for line in block.splitlines() if line.startswith("ASK NEXT")]
    assert len(ask) == 1 and ask[0].count("?") == 1
