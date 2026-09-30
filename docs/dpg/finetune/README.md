# Fine-tuning the local model

The default local model (`qwen3.5:4b` on Ollama) follows the consultation prompt less
faithfully than a large hosted model. This pipeline teaches it the behaviour directly with a
LoRA adapter, trained only on synthetic dialogues.

## 1. Generate dialogues (pilot first)

```bash
AGRONAUT_FINETUNE_GEN=1 TEACHER_PROVIDER=anthropic TEACHER_MODEL=claude-sonnet-5 \
  python -m scripts.finetune.generate_dialogues --n 20 --out data/finetune/pilot.jsonl \
  --price-in <usd per M input tokens> --price-out <usd per M output tokens>
```

The pilot prints tokens per dialogue and, given prices, the cost per 1,000 dialogues. Decide
the full run size from that. Before a full run, confirm the teacher provider's terms allow its
outputs to be used as training data; an open-weights teacher (for example through
`TEACHER_PROVIDER=nvidia`) avoids the question.

What the data is:
- the teacher runs inside `AgronautAgent`, so tool calls hit the real tools;
- each teacher call is one example (full context in, the teacher's reply out), so the student
  learns when to call a tool, not only how to phrase an answer;
- text targets are passed through `style.polish_reply`;
- a dialogue is kept only if it passes `consult_eval`'s code metrics (no dashes, median reply
  under 80 words, at least 90% of replies asking one question or none, nothing re-asked);
- replies the loop would have corrected (fabricated `[earlier result ...]`, announced actions
  with no tool call) are dropped as targets.

Leave `--with-scenarios` off: the 20 `consult_eval` scenarios are the held-out test set.

## 2. Train

```bash
pip install mlx-lm
python -m scripts.finetune.train_lora --data data/finetune/dialogues.jsonl \
  --work data/finetune/run1
ollama create agronaut-consult:4b -f data/finetune/run1/Modelfile
```

## 3. Promotion gate

The tuned model is offered to users only if, against the base `qwen3.5:4b`:

| check | command | must |
|---|---|---|
| consultation style | `LLM_PROVIDER=ollama LLM_MODEL=agronaut-consult:4b AGRONAUT_CONSULT_EVAL=1 python -m scripts.consult_eval --compare <base report>` | improve, with no metric WORSE |
| tool calling | `python -m scripts.local_model_check` with the tuned tag | pass |
| safety | `python -m scripts.safety_eval` with the tuned tag | pass |
| grounding | `AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval` | no drop |

Record the numbers here, next to the date and the adapter's training data size. Only then add
it to `setup_wizard.py` as an option. Making it the default in `agent/llm.py` is a separate
decision.
