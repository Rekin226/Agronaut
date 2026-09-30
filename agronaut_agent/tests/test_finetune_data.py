"""The fine-tuning data pipeline, checked without a teacher model or a GPU."""

import json

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


def _conv(words=20, questions=1, dashes=0, reasked=()):
    return {"messages": [{"words": words, "questions": questions, "em_dashes": dashes,
                          "reasked": list(reasked)}] * 4}


def test_dialogue_filter_uses_the_consult_metrics():
    assert gd.dialogue_passes(_conv())
    assert not gd.dialogue_passes(_conv(words=200))
    assert not gd.dialogue_passes(_conv(questions=3))
    assert not gd.dialogue_passes(_conv(dashes=1))
    assert not gd.dialogue_passes(_conv(reasked=["crop"]))
    assert not gd.dialogue_passes({"messages": []})


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
