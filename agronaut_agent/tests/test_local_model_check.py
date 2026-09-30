"""The judging logic of scripts/local_model_check.py, without Ollama.

The live check runs by hand on a CPU runner and takes minutes, so a bug in how it decides
PASS would go unnoticed for a long time. These pin the decision: a prompt that was cut, or a
fluent reply with no computed design behind it, must never come out as a pass.
"""

from scripts.local_model_check import EXPECTED_ARGS, args_match, truncation_events, verdict

GOOD_CALL = {"name": "size_aquaponics_system", "args": dict(EXPECTED_ARGS), "ok": True}
CUT = ('time=2026-09-30T08:57:05 level=WARN source=llama_server.go:320 '
       'msg="truncating input prompt" limit=2060 prompt=11287 keep=24 new=2060')


def test_truncation_lines_are_found_in_an_ollama_log():
    log = "level=INFO msg=starting\n" + CUT + "\nlevel=INFO msg=done"
    assert truncation_events(log) == [CUT]
    assert truncation_events("") == []


def test_a_whole_prompt_and_a_correct_sizing_call_pass():
    v = verdict([], [GOOD_CALL], "Here is your design.")
    assert v["passed"] and v["reasons"] == []


def test_a_truncated_prompt_fails_even_when_everything_else_worked():
    v = verdict([CUT], [GOOD_CALL], "Here is your design.")
    assert not v["passed"] and "truncated" in v["reasons"][0]


def test_a_fluent_reply_with_no_tool_call_fails():
    """The failure this project cares most about: a design nobody computed."""
    v = verdict([], [], "A 12 m2 system needs about 2 m3 of fish tank.")
    assert not v["passed"] and "never called the sizing tool" in v["reasons"][0]


def test_a_sizing_call_that_errored_fails():
    v = verdict([], [dict(GOOD_CALL, ok=False)], "Sorry.")
    assert not v["passed"] and "returned an error" in v["reasons"][0]


def test_wrong_numbers_are_named():
    bad = dict(GOOD_CALL, args=dict(EXPECTED_ARGS, grow_area_m2=120))
    v = verdict([], [bad], "Here is your design.")
    assert not v["passed"] and "grow_area_m2" in v["reasons"][0]


def test_arguments_tolerate_case_and_float_formatting():
    got = {"fish_species": "Tilapia", "crop": "LETTUCE", "grow_area_m2": 12.0,
           "temperature_c": "27", "water_budget_lpd": 3000.0}
    assert all(args_match(got).values())


def test_a_missing_argument_does_not_match():
    got = dict(EXPECTED_ARGS)
    del got["temperature_c"]
    assert args_match(got)["temperature_c"] is False


def test_a_turn_that_raised_is_a_fail_with_the_reason_not_a_crash():
    """Found by running it: a model that refuses tools made the whole script crash, which on
    CI would have left no report behind at all."""
    v = verdict([], [], "", error="ResponseError: llama3 does not support tools")
    assert not v["passed"]
    assert v["reasons"][0].startswith("the turn failed: ResponseError")
    assert not any("empty" in r for r in v["reasons"])


def test_a_reply_that_misquotes_the_engine_fails_even_with_a_correct_tool_call():
    """qwen3.5:2b passed this check with "6.7 L/h" for a 6,667 L/h pump (#180)."""
    v = verdict([], [GOOD_CALL], "Pump: ~6.7 L/h.", ungrounded=["6.7 L/h"])
    assert not v["passed"] and "6.7 L/h" in v["reasons"][0]
    assert v["ungrounded"] == ["6.7 L/h"]
