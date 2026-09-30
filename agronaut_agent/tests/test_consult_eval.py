"""The arithmetic behind scripts/consult_eval.py, checked without a model."""

import json

from scripts import consult_eval as ce


def test_questions_are_counted_per_sentence_in_every_script():
    assert ce.count_questions("Nice. Home food or to sell?") == 1
    assert ce.count_questions("Where are you? How big is it?? What fish?") == 3
    assert ce.count_questions("你在哪裡？") == 1
    assert ce.count_questions("Got it.") == 0


def test_chinese_characters_count_as_words():
    assert ce.count_words("Nice, love that.") == 3
    assert ce.count_words("你好嗎") == 3


def test_reask_only_counts_questions_about_known_facts():
    known = {"fish_species": "tilapia", "location": ""}
    assert ce.reasked_keys("Which fish would you like?", known) == ["fish_species"]
    assert ce.reasked_keys("Where are you based?", known) == []      # not known yet
    assert ce.reasked_keys("Tilapia is a good fish choice.", known) == []   # not a question


def test_turns_to_advice_counts_user_turns_before_the_first_recommending_tool():
    rows = [{"role": "user"}, {"role": "assistant"},
            {"role": "user"}, {"role": "tool", "tool_name": "update_profile"},
            {"role": "assistant"},
            {"role": "user"}, {"role": "tool", "tool_name": "size_aquaponics_system"}]
    assert ce.turns_to_advice(rows) == 3
    assert ce.turns_to_advice(rows[:5]) is None


def test_judge_labels_last_one_wins_and_unknown_is_unjudged():
    assert ce.parse_label("1: REFLECTED", "REFLECTED", "NOT_REFLECTED") is True
    assert ce.parse_label("NOT_REFLECTED", "REFLECTED", "NOT_REFLECTED") is False
    assert ce.parse_label("it kind of did", "REFLECTED", "NOT_REFLECTED") is None


def test_compare_names_direction():
    lines = ce.compare({"em_dash_messages": 0, "actionable": 0.9},
                       {"em_dash_messages": 4, "actionable": 0.5})
    assert any("em_dash_messages" in ln and "better" in ln for ln in lines)
    assert any("actionable" in ln and "better" in ln for ln in lines)


def test_scenarios_file_is_well_formed():
    data = json.loads(ce._SCENARIOS.read_text())
    ids = [s["id"] for s in data["scenarios"]]
    assert len(ids) == len(set(ids)) >= 15
    for s in data["scenarios"]:
        assert s["opening"] and s["persona"] and isinstance(s["facts"], dict)
        assert s["channel"] in ("telegram", "whatsapp", "web", "cli")


class _OneQuestionFake:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content="Nice, love that.\n\nIs this for home food or to sell?")


def test_a_whole_run_scores_with_fakes(tmp_path):
    from agronaut_agent.core import AgronautAgent

    counter = iter(range(100))
    backends = (
        lambda: AgronautAgent(db_path=tmp_path / f"{next(counter)}.sqlite3",
                              chat_model=_OneQuestionFake()),
        lambda scenario, turns: ce.DONE if len(turns) >= 4 else "to sell",
        lambda prompt: "1: REFLECTED\n2: PLAIN\n3: VAGUE",
    )
    report = ce.run(limit=2, backends=backends)
    s = report["summary"]
    assert s["n_conversations"] == 2 and s["n_messages"] == 4
    assert s["em_dash_messages"] == 0
    assert s["share_one_question_or_less"] == 1.0
    assert s["reflected"] == 1.0 and s["actionable"] == 0.0
    assert s["reached_advice"] == "0/2"


def test_judge_verdicts_parse_on_one_line_or_three():
    for raw in ("1: REFLECTED / 2: PLAIN / 3: VAGUE", "1: REFLECTED\n2: PLAIN\n3: VAGUE",
                "**1:** REFLECTED\n**2:** PLAIN\n**3:** VAGUE"):
        got = ce.judge_conversation(lambda _p, r=raw: r, [("User", "hi")], "beginner")
        assert (got["reflected"], got["beginner_level"], got["actionable"]) == (True, True, False)


def test_rejudge_replaces_verdicts_and_summary():
    report = {"conversations": [{"id": "burkina-first-timer", "transcript": "User: hi",
                                 "messages": [{"em_dashes": 0, "words": 2, "questions": 1,
                                               "reasked": []}],
                                 "turns_to_advice": 2, "judge": {}}]}
    out = ce.rejudge(report, lambda _p: "1: REFLECTED\n2: PLAIN\n3: ACTIONABLE")
    assert out["summary"]["actionable"] == 1.0
    assert out["conversations"][0]["judge"]["reflected"] is True


def test_a_run_leaves_the_climate_folder_as_it_found_it(tmp_path):
    (tmp_path / "ouaga.json").write_text("original")
    with ce._RestoreClimateFiles(tmp_path):
        (tmp_path / "ouaga.json").write_text("refetched")
        (tmp_path / "koudougou.json").write_text("new")
    assert (tmp_path / "ouaga.json").read_text() == "original"
    assert not (tmp_path / "koudougou.json").exists()


def test_numbers_are_read_the_way_people_write_them():
    assert ce.numbers_in("1 170 000 FCFA, 2,070 L, pH 6.8") == [1170000.0, 2070.0, 6.8]
    assert ce.numbers_in("800\u202f000 et 0,5 mg/L") == [800000.0, 0.5]


def test_a_rounded_tool_number_is_traced_and_an_invented_one_is_not():
    sources = ce.numbers_in("rearing tank 2068.4 L; biomass 41.2 kg; makeup 48 L/day")
    reply = "About 2,070 L of tank, ~41 kg of fish, 48L a day, and a 900 W heater."
    assert ce.untraced_quantities(reply, sources) == ["900 W"]


def test_unitless_numbers_and_words_are_ignored():
    assert ce.untraced_quantities("Step 2: wait 3 weeks, then add 10 fish.", []) == []
    assert ce.untraced_quantities("Use a 1000 L IBC and 20 kg of fish.", [1000.0]) == ["20 kg"]
