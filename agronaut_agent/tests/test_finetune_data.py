"""The fine-tuning data pipeline, checked without a teacher model or a GPU."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from scripts.finetune import generate_dialogues as gd
from scripts.finetune import train_lora as tl

EM = "—"


def test_persona_grid_is_varied_and_reproducible():
    a, b = gd.persona_grid(40, seed=3), gd.persona_grid(40, seed=3)
    assert a == b
    assert len({p["id"] for p in a}) == 40
    assert {p["language"] for p in a} == {"English", "French", "Traditional Chinese"}
    assert {p["experience"] for p in a} >= {"beginner", "intermediate"}
    assert all(p["opening"] and p["facts"]["location"] for p in a)


def test_system_messages_fold_into_one_leading_system_and_operator_notes():
    msgs = [SystemMessage(content="PROMPT"), SystemMessage(content="RECALL"),
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "update_profile", "id": "c1",
                                               "args": {"updates": {"crop": "kale"}}}]),
            ToolMessage(content="Saved", tool_call_id="c1"),
            SystemMessage(content="Now reply in plain text.")]
    out = gd.to_chat_messages(msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "user"]
    assert out[0]["content"] == "PROMPT\n\nRECALL"
    call = out[2]["tool_calls"][0]["function"]
    assert call["name"] == "update_profile" and json.loads(call["arguments"]) == {
        "updates": {"crop": "kale"}}
    assert out[4]["content"].startswith("[operator note] ")


def test_anthropic_block_content_becomes_text():
    out = gd.to_chat_messages([AIMessage(content=[{"type": "text", "text": "Hi"},
                                                  {"type": "tool_use", "id": "x"}])])
    assert out[0]["content"] == "Hi"


def test_targets_are_polished_and_corrected_replies_dropped():
    ctx = [SystemMessage(content="P"), HumanMessage(content="hi")]
    calls = [(ctx, AIMessage(content=f"Tilapia {EM} easy.\n\nWhere are you?")),
             (ctx, AIMessage(content="[earlier result from size] 3 tanks")),
             (ctx, AIMessage(content="Let me check your forecast.")),
             (ctx, AIMessage(content="", tool_calls=[{"name": "my_system_forecast",
                                                      "id": "c", "args": {}}]))]
    ex = gd.examples_from_calls(calls, "whatsapp", tools=[{"t": 1}])
    assert len(ex) == 2
    assert ex[0]["messages"][-1]["content"] == "Tilapia, easy.\n\nWhere are you?"
    assert ex[1]["messages"][-1]["tool_calls"][0]["function"]["name"] == "my_system_forecast"
    assert ex[0]["tools"] == [{"t": 1}]


def _conv(words=20, questions=1, dashes=0, reasked=(), untraced=()):
    return {"messages": [{"words": words, "questions": questions, "em_dashes": dashes,
                          "reasked": list(reasked), "untraced": list(untraced)}] * 4}


def test_dialogue_filter_uses_the_consult_metrics():
    assert gd.dialogue_passes(_conv())
    assert not gd.dialogue_passes(_conv(words=200))
    assert not gd.dialogue_passes(_conv(questions=3))
    assert not gd.dialogue_passes(_conv(dashes=1))
    assert not gd.dialogue_passes(_conv(reasked=["crop"]))
    assert not gd.dialogue_passes({"messages": []})
    assert not gd.dialogue_passes(_conv(untraced=["400 W"]))
    assert gd.rejection_reasons({"messages": _conv()["messages"][:1]}) == ["only 1 assistant replies"]


class _Teacher:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Nice.\n\nWhere are you based?",
                         usage_metadata={"input_tokens": 100, "output_tokens": 10,
                                         "total_tokens": 110})


def test_generate_writes_examples_and_counts_tokens(tmp_path):
    out = tmp_path / "d.jsonl"
    personas = gd.persona_grid(2)
    stats = gd.generate(personas, _Teacher,
                        lambda s, turns: gd.ce.DONE if len(turns) >= 4 else "Ouaga", out)
    assert stats["dialogues"] == 2 and stats["kept"] == 2
    log = [json.loads(x) for x in out.with_suffix(".dialogues.jsonl").read_text().splitlines()]
    assert [r["kept"] for r in log] == [True, True] and "Nice." in log[0]["transcript"]
    assert stats["tokens_in"] == 100 * 4                 # two turns per dialogue, two dialogues
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(rows) == stats["examples"] == 4
    assert rows[0]["messages"][0]["role"] == "system" and rows[0]["tools"]
    assert rows[0]["messages"][-1] == {"role": "assistant",
                                       "content": "Nice.\n\nWhere are you based?"}


def test_split_keeps_each_persona_on_one_side():
    lines = [{"persona": f"p{i % 10}", "messages": [i]} for i in range(50)]
    train, valid = tl.split_by_persona(lines, valid_share=0.2)
    assert len(train) + len(valid) == 50 and valid
    tv = {m["messages"][0] % 10 for m in valid}
    tt = {m["messages"][0] % 10 for m in train}
    assert not tv & tt
    assert all("persona" not in ex for ex in train + valid)


def test_lora_config_and_modelfile(tmp_path):
    cfg = tl.lora_config("Qwen/Qwen3.5-4B", tmp_path, tmp_path / "a", n_train=400)
    assert cfg["iters"] == 800 and cfg["mask_prompt"] is True
    mf = tl.modelfile(tmp_path / "fused")
    assert mf.startswith("FROM /") and "num_ctx 32768" in mf


def test_transient_provider_errors_are_retried_and_others_are_not():
    import pytest
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("[503] Service temporarily overloaded")
        return "ok"
    assert gd.with_retries(flaky, sleep=lambda s: None) == "ok" and len(calls) == 3

    def broken():
        raise ValueError("bad request: unknown model")
    with pytest.raises(ValueError):
        gd.with_retries(broken, sleep=lambda s: None)


def test_nvidia_teachers_answer_without_thinking(monkeypatch):
    monkeypatch.delenv("TEACHER_THINKING", raising=False)
    assert gd.teacher_invoke_kwargs("nvidia") == {
        "chat_template_kwargs": {"enable_thinking": False}}
    assert gd.teacher_invoke_kwargs("anthropic") == {}
    monkeypatch.setenv("TEACHER_THINKING", "on")
    assert gd.teacher_invoke_kwargs("nvidia") == {}


def test_invoke_kwargs_reach_the_teacher():
    seen = {}

    class _Inner:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, **kw):
            seen.update(kw)
            return AIMessage(content="hi")
    rec = gd.RecordingChat(_Inner(), invoke_kwargs={"chat_template_kwargs": {"x": 1}})
    rec.bind_tools([]).invoke([HumanMessage(content="q")])
    assert seen == {"chat_template_kwargs": {"x": 1}} and len(rec.calls) == 1


def test_teacher_must_be_named_and_never_claude(monkeypatch):
    with pytest.raises(SystemExit):
        gd.resolve_teacher(None, None)
    with pytest.raises(SystemExit):
        gd.resolve_teacher("ollama", None)
    for provider, model in [("anthropic", "claude-sonnet-5"), ("ANTHROPIC", "x"),
                            ("openai_compat", "anthropic/claude-haiku-4-5")]:
        with pytest.raises(SystemExit):
            gd.resolve_teacher(provider, model)
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://api.anthropic.com/v1/")
    with pytest.raises(SystemExit):
        gd.resolve_teacher("openai_compat", "some-model")
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL")
    assert gd.resolve_teacher(" Ollama ", "qwen3.5:27b") == ("ollama", "qwen3.5:27b")


def test_side_calls_resolve_to_the_teacher(monkeypatch):
    from agent.llm import resolve

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    gd.pin_models_to_teacher("ollama", "qwen3.5:27b")
    assert resolve() == ("ollama", "qwen3.5:27b")


def test_strict_figures_cover_prices_counts_and_months():
    text = "每月約 15-20 顆，每顆 15 元，回本 5.6 個月。PVC 管 11cm"
    assert gd.strict_untraced(text, []) == ["15", "20 顆", "15 元", "5.6 個月", "11cm"]
    assert gd.strict_untraced(text, [15, 20, 5.6, 11]) == []
    # 2% tolerance: rounding passes, a nearby wrong figure does not
    assert gd.strict_untraced("about 570 TWD", [574.0]) == []
    assert gd.strict_untraced("a loss of 2,800", [2700.0]) == ["2,800"]
    # list markers, small integers and numbers inside words are not figures
    assert gd.strict_untraced("1. Add 2 airstones\n2. Check CO2 and NO3 in 1 week", []) == []
    assert gd.strict_untraced("Tilapia need 28 °C", [28.0]) == []
    # a small number with a unit is a figure, as consult_eval's dialogue check reads it
    assert gd.strict_untraced("cut 2 cm off the roots", []) == ["2 cm"]
    assert gd.strict_untraced("add 1 air stone", []) == []


def _ctx(*user):
    return [SystemMessage(content="prompt with 999 in it")] + [HumanMessage(content=u)
                                                               for u in user]


def test_turn_problems_name_what_to_fix():
    assert gd.turn_problems(_ctx("hi"), AIMessage(content="", tool_calls=[
        {"name": "x", "args": {}, "id": "1"}])) == []
    assert gd.turn_problems(_ctx("I have 2 m2"), AIMessage(content="Nice. Where are you?")) == []
    bad = gd.turn_problems(_ctx("hi"), AIMessage(
        content="Lettuce grows in 30 days. Where are you? Which fish?"))
    assert any("2 question marks" in p for p in bad) and any("(30 days)" in p for p in bad)
    # the system prompt is not a source
    assert gd.turn_problems(_ctx("hi"), AIMessage(content="It costs 999 FCFA."))
    assert any("without doing it" in p for p in
               gd.turn_problems(_ctx("hi"), AIMessage(content="稍等我一下，正在計算中")))
    assert any("words" in p for p in
               gd.turn_problems(_ctx("hi"), AIMessage(content="word " * 101)))
    assert gd.turn_problems(_ctx("hi"), AIMessage(content="  ")) == ["it was empty"]


def test_a_failing_reply_is_redrafted_and_only_the_accepted_one_recorded():
    seen = []

    class _Inner:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, **kw):
            seen.append(list(messages))
            return AIMessage(content="Where? Which?" if len(seen) == 1 else "Where are you?")
    rec = gd.RecordingChat(_Inner(), check=gd.turn_problems, max_drafts=4)
    ctx = _ctx("hi")
    reply = rec.bind_tools([]).invoke(ctx)
    assert reply.content == "Where are you?" and len(seen) == 2
    assert seen[1][-1].content.startswith(gd._OPERATOR) and "2 question marks" in seen[1][-1].content
    assert seen[1][-2].content == "Where? Which?"
    assert rec.calls == [(ctx, reply)] and len(rec.discarded) == 1 and rec.failures == []


def test_a_reply_out_of_drafts_fails_the_dialogue():
    class _Stubborn:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, **kw):
            return AIMessage(content="Where? Which?")
    rec = gd.RecordingChat(_Stubborn(), check=gd.turn_problems, max_drafts=3)
    rec.invoke(_ctx("hi"))
    assert len(rec.discarded) == 2 and len(rec.failures) == 1
    conv = _conv()
    conv["failed_turns"] = 1
    assert "1 replies still failing after the last draft" in gd.rejection_reasons(conv)


def test_the_question_note_quotes_the_question_sentences():
    text = "Payback takes 24 years. The hard truth? It stays a hobby. Want me to check 10 m2?"
    assert gd.question_sentences(text) == ["The hard truth?", "Want me to check 10 m2?"]
    assert gd.question_sentences("你有多大空間呢？比如說幾坪？") == ["你有多大空間呢？", "比如說幾坪？"]
    note = gd.turn_problems(_ctx("I have 10 m2"), AIMessage(content=text))[0]
    assert "\u00abThe hard truth?\u00bb" in note and "ONE question mark" in note


def test_a_failed_dialogue_keeps_its_clean_prefix():
    fails = [{"call": 3, "problems": ["x"]}, {"call": 5, "problems": ["y"]}]
    assert gd.salvage_cut(8, [], []) == 8
    assert gd.salvage_cut(8, fails, ["2 replies still failing after the last draft"]) == 3
    assert gd.salvage_cut(8, [], ["median 82 words"]) == 8
    assert gd.salvage_cut(8, fails, ["re-asked a known fact"]) == 0
    assert gd.salvage_cut(8, fails, ["untraced numbers: 2m²", "1 replies still failing"]) == 0
    assert gd.salvage_cut(1, [], ["only 1 assistant replies"]) == 0


def test_generate_writes_the_clean_prefix_of_a_failed_dialogue(tmp_path):
    replies = iter(["Nice. Where are you?", "Where? Which?", "Where? Which?", "Fine."] * 4)

    class _T:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, **kw):
            return AIMessage(content=next(replies))
    out = tmp_path / "d.jsonl"
    stats = gd.generate(gd.persona_grid(1), _T,
                        lambda s, turns: gd.ce.DONE if len(turns) >= 6 else "Ouaga", out,
                        check=gd.turn_problems, max_drafts=2)
    log = json.loads(out.with_suffix(".dialogues.jsonl").read_text())
    assert not log["kept"] and log["examples"] == 1 and log["turn_failures"]
    assert stats["salvaged"] == 1 and stats["examples"] == 1
    assert json.loads(out.read_text())["messages"][-1]["content"] == "Nice. Where are you?"


def test_tool_arguments_become_objects_for_qwen():
    ex = {"messages": [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c0", "type": "function",
         "function": {"name": "size", "arguments": "{\"area_m2\": 2}"}}]}], "tools": []}
    out = tl.for_qwen_template(ex)
    assert out["messages"][0]["tool_calls"][0]["function"]["arguments"] == {"area_m2": 2}
    assert isinstance(ex["messages"][0]["tool_calls"][0]["function"]["arguments"], str)


def test_examples_too_long_for_the_window_are_dropped_not_truncated():
    rows = [{"n": 100}, {"n": 17408}, {"n": 17409}]
    kept, dropped = tl.drop_too_long(rows, lambda r: r["n"])
    assert kept == rows[:2] and dropped == 1
    assert tl.lora_config("m", Path("d"), Path("a"), 10)["max_seq_length"] == tl.MAX_SEQ_LENGTH
