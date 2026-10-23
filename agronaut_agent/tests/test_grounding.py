"""Every figure in a reply must come from a tool or the user (#180). No model, no network.

The motivating case is real: on 2026-09-30 qwen3.5:2b relayed a 6,666.7 L/h pump as
"~6.7 L/h". It had converted the system volume to m³ correctly ("6.7 m³") and then kept the
old unit on the pump. The check has to flag that and pass the correct conversion, which is
why it compares physical amounts, not digits.
"""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent import grounding as G
from agronaut_agent import runtime
from agronaut_agent.analytics import Analytics
from agronaut_agent.core import AgronautAgent

# The relevant lines of the real sizing output for 12 m², tilapia + lettuce, 27 °C, 3000 L/day.
TOOL = ("Sizing: feed=720 g/day, fish=96 head, biomass=48 kg, system_volume=6666.7 L, "
        "rearing_tank=2400 L, pump=6666.7 L/h against 0.52 m head (~27 W), makeup_water=58 L\n"
        "Nitrogen consistency check: agrees=True (disagreement_fraction=0.084)\n"
        "Operating envelope: pH target 6.0-7.0; temperature target 27-30 °C; DO >= 5 mg/L\n"
        "biofilter surface ~84.6 m2")
USER = ("I have a 12 m2 greenhouse and want tilapia with lettuce. The water sits around 27 C "
        "and I can use 3000 litres a day. Size the system for me.")


# --- the real replies ----------------------------------------------------------------------

def test_the_small_models_unit_slip_is_caught_and_its_correct_conversion_is_not():
    reply = ("System volume: 6.7 m³ (rearing tank: 2.4 m³). Pump: ~6.7 L/h against 0.52 m "
             "head (~27 W). Feed 720 g/day, biomass 48 kg, makeup 58 L/day. Nitrogen check: "
             "agrees (8.4% disagreement).")
    assert G.ungrounded(reply, [TOOL, USER]) == ["6.7 L/h"]


def test_a_faithful_rounded_reply_passes():
    reply = ("For 12 m² at 27°C with 3000 L/day: 48 kg of fish on 720 g/day of feed, a "
             "2,400 L tank, a pump of about 6,700 L/h (~27 W), about 85 m² of biofilter "
             "surface, and 58 L/day of makeup water. Keep DO above 5 mg/L.")
    assert G.ungrounded(reply, [TOOL, USER]) == []


# --- what counts as the same amount --------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "a 6.67 m³ system",            # L -> m³
    "about 6.7 m³/h of flow",      # L/h -> m³/h
    "0.72 kg/day of feed",         # g/day -> kg/day
    "8.4% disagreement",           # fraction -> percent
    "a 2 400 L tank",              # French thousands
    "a 2.400 L tank",              # ambiguous grouping: read as 2400 too
])
def test_unit_conversions_and_number_styles_are_understood(reply):
    assert G.ungrounded(reply, [TOOL]) == []


@pytest.mark.parametrize("reply, flagged", [
    ("a 15 m³ system", ["15 m³"]),               # altered
    ("add 2 kg of salt", ["2 kg"]),              # invented
    ("a pump of 6.7 L/h", ["6.7 L/h"]),          # right digits, wrong unit
    ("feed 720 kg/day", ["720 kg/day"]),         # 1000x in the other direction
])
def test_altered_invented_and_misunited_figures_are_flagged(reply, flagged):
    assert G.ungrounded(reply, [TOOL]) == flagged


def test_a_unitless_source_number_cannot_ground_a_different_amount():
    """pH 7.0 sits in the tool output; 5% tolerance would have let it ground "6.7 L/h"."""
    assert G.ungrounded("6.7 L/h", ["pH 7.0"]) == ["6.7 L/h"]
    assert G.ungrounded("12 m²", ["your area is 12"]) == []   # exact, so it may


def test_what_the_user_said_counts_as_a_source():
    assert G.ungrounded("with your 3,000 L/day budget", [USER]) == []


@pytest.mark.parametrize("text", [
    "Step 2: wait 3 weeks, then add 96 fish.", "pH 6.8 is fine.", "Since 2026 ...",
    "0 W on standby, 0%.",
])
def test_numbers_that_are_not_quantities_or_are_zero_are_left_alone(text):
    assert G.ungrounded(text, []) == []


def test_readings_of_written_numbers():
    assert G.readings("6,700") == (6700.0, 6.7)
    assert G.readings("2 070") == (2070.0,)
    assert G.readings("0.052") == (0.052,)
    assert G.readings("1,5") == (1.5,)
    assert G.readings("1.234,5") == (1234.5,)


# --- a scripted turn through the real agent and the real sizing tool -----------------------

class _Relay:
    """Calls the sizing tool, then answers with `write(tool_result)`."""

    def __init__(self, write):
        self.write = write

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        results = [m for m in messages if isinstance(m, ToolMessage)]
        if results:
            return AIMessage(content=self.write(results[-1].content))
        return AIMessage(content="", tool_calls=[{
            "name": "size_aquaponics_system", "id": "c1",
            "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                     "temperature_c": 27, "water_budget_lpd": 3000}}])


def _pump(result: str) -> float:
    import re
    return float(re.search(r"pump=([\d.]+) L/h", result).group(1))


@pytest.fixture(autouse=True)
def _clean_turn():
    yield
    runtime.end_turn()


def _turn(tmp_path, write):
    a = AgronautAgent(chat_model=_Relay(write), db_path=str(tmp_path / "db.sqlite"))
    a._analytics = Analytics(path=tmp_path / "a.jsonl")
    a.handle_message("test", "u1", USER)
    rows = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
    return next(r for r in rows if r["event"] == "turn")


def test_a_faithful_relay_records_zero_ungrounded_figures(tmp_path):
    turn = _turn(tmp_path, lambda res: f"Your pump needs about {_pump(res):,.0f} L/h.")
    assert turn["ungrounded_numbers"] == 0


def test_a_misrelayed_figure_is_counted_on_the_turn_and_logged(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        turn = _turn(tmp_path, lambda res: f"Your pump needs about {_pump(res) / 1000:.1f} L/h.")
    assert turn["ungrounded_numbers"] == 1
    assert "no tool or user gave" in caplog.text


def test_the_analytics_summary_reports_grounding(tmp_path):
    a = Analytics(path=tmp_path / "a.jsonl")
    a.record("turn", ungrounded_numbers=0)
    a.record("turn", ungrounded_numbers=2)
    a.record("turn")                                   # a turn from before the check existed
    assert a.summarize()["grounding"] == {"turns_checked": 2, "turns_with_ungrounded": 1,
                                          "ungrounded_numbers": 2}


@pytest.mark.parametrize("text", [
    'How much water per day? A rough estimate is fine (e.g. "200 L/day").',
    "A rough estimate works (e.g. 100L, 300L, 500L), then I will size it.",
    "Pick a tank size, for example 1000 L?",
])
def test_sample_answers_inside_a_question_are_not_claims(text):
    """Four of seven replies flagged on the maintainer's real history were these."""
    assert G.ungrounded(text, []) == []


def test_a_claim_after_an_example_is_still_checked():
    assert G.ungrounded("(e.g. 200 L/day). Your tank is 3000 L.", []) == ["3000 L"]
