"""QLoRA fine-tune of the local model on an NVIDIA GPU, for when Apple silicon cannot.

Why this exists: Qwen3.5's linear-attention (gated delta) layers have no backward in mlx-lm's
fast kernel, so training falls back to one op per token and keeps the full recurrent state for
every token, about 2 MB per token per layer. Our examples are 12K to 17K tokens (the system
prompt and tool schemas alone are about 12K), which no Mac can hold. PyTorch's transformers
implementation runs the recurrence in chunks and keeps state only per chunk.

Written for a 24 GB Turing card (Quadro RTX 6000): no bf16 and no FlashAttention 2 there, so
the base is loaded in 4-bit (QLoRA) with fp16 compute, and attention uses PyTorch's
memory-efficient SDPA. On an Ampere or newer card pass --bf16.

Input is the split that train_lora.py writes (`--skip train,fuse,ollama`): DIR/data/train.jsonl
and valid.jsonl, tool-call arguments already objects, examples longer than the window dropped.

    pip install -r scripts/finetune/requirements-cuda.txt
    python -m scripts.finetune.train_lora_cuda --work data/finetune/run1 --smoke 3
    python -m scripts.finetune.train_lora_cuda --work data/finetune/run1
    python -m scripts.finetune.train_lora_cuda --work data/finetune/run1 --merge

Loss is on the final assistant turn only (the target); everything before it is context.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

BASE_MODEL = "Qwen/Qwen3.5-4B"      # the weights behind Ollama's qwen3.5:4b (Apache-2.0)
MAX_LEN = 17408                      # train_lora.MAX_SEQ_LENGTH
ASSISTANT_HEADER = "<|im_start|>assistant\n"
IGNORE = -100


def target_labels(ids: list[int], header: list[int]) -> list[int]:
    """Labels that train only the last assistant turn: everything up to and including the
    last assistant header is masked. Raises when there is no header, rather than training on
    the whole prompt by accident."""
    n = len(header)
    for start in range(len(ids) - n, -1, -1):
        if ids[start:start + n] == header:
            cut = start + n
            return [IGNORE] * cut + ids[cut:]
    raise ValueError("no assistant header in the example")


def encode(tokenizer, row: dict, max_len: int = MAX_LEN) -> dict | None:
    """One example as input_ids + labels, or None when it does not fit the window (never
    truncated: the target sits at the end, so truncation would cut exactly what is trained)."""
    text = tokenizer.apply_chat_template(row["messages"], tools=row.get("tools"),
                                         tokenize=False)
    ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    if len(ids) > max_len:
        return None
    header = tokenizer(ASSISTANT_HEADER, add_special_tokens=False)["input_ids"]
    return {"input_ids": ids, "labels": target_labels(ids, header)}


def lora_targets(model) -> list[str]:
    """Leaf names of the language model's linear layers (vision tower and lm_head left out).
    Read from the loaded model, because Qwen3.5's linear-attention projections are not named
    like ordinary attention ones."""
    import torch.nn as nn

    names = set()
    for full, module in model.named_modules():
        is_linear = isinstance(module, nn.Linear) or type(module).__name__ == "Linear4bit"
        if not is_linear or "visual" in full or "vision" in full or full.endswith("lm_head"):
            continue
        if ".layers." in full:
            names.add(full.rsplit(".", 1)[-1])
    return sorted(names)


def _load(model_id: str, bf16: bool, four_bit: bool = True):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    dtype = torch.bfloat16 if bf16 else torch.float16
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                               bnb_4bit_compute_dtype=dtype,
                               bnb_4bit_use_double_quant=True) if four_bit else None
    kw = {"dtype": dtype, "quantization_config": quant, "attn_implementation": "sdpa",
          "device_map": {"": 0} if four_bit else "auto"}
    try:
        model = AutoModelForCausalLM.from_pretrained(model_id, **kw)
    except (ValueError, KeyError):      # Qwen3.5 checkpoints are image-text-to-text
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(model_id, **kw)
    return AutoTokenizer.from_pretrained(model_id), model


def train(args) -> None:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import Trainer, TrainingArguments

    work = Path(args.work)
    tokenizer, model = _load(args.model, args.bf16)
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    targets = lora_targets(model)
    print("LoRA targets:", targets, flush=True)
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=2 * args.rank,
                                             lora_dropout=0.05, target_modules=targets,
                                             task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    def load(name):
        rows = [json.loads(ln) for ln in (work / "data" / f"{name}.jsonl").open() if ln.strip()]
        out = [e for e in (encode(tokenizer, r, args.max_len) for r in rows) if e]
        print(f"{name}: {len(out)} of {len(rows)} examples fit {args.max_len} tokens", flush=True)
        return out
    train_set, valid_set = load("train"), load("valid")

    def collate(batch):
        n = max(len(b["input_ids"]) for b in batch)
        pad = tokenizer.pad_token_id or 0
        ids = [b["input_ids"] + [pad] * (n - len(b["input_ids"])) for b in batch]
        lab = [b["labels"] + [IGNORE] * (n - len(b["labels"])) for b in batch]
        att = [[1] * len(b["input_ids"]) + [0] * (n - len(b["input_ids"])) for b in batch]
        return {"input_ids": torch.tensor(ids), "labels": torch.tensor(lab),
                "attention_mask": torch.tensor(att)}

    steps = math.ceil(len(train_set) * args.epochs / args.grad_accum)
    targs = TrainingArguments(
        output_dir=str(work / "cuda_checkpoints"), per_device_train_batch_size=1,
        gradient_accumulation_steps=args.grad_accum, learning_rate=args.lr,
        num_train_epochs=args.epochs, max_steps=args.smoke or -1, lr_scheduler_type="cosine",
        warmup_ratio=0.03, logging_steps=1 if args.smoke else 5,
        eval_strategy="no" if args.smoke else "steps", eval_steps=max(10, steps // 8),
        per_device_eval_batch_size=1, save_strategy="no" if args.smoke else "steps",
        save_steps=max(10, steps // 4), save_total_limit=2,
        fp16=not args.bf16, bf16=args.bf16, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, optim="paged_adamw_8bit",
        report_to=[], remove_unused_columns=False, dataloader_num_workers=0)
    trainer = Trainer(model=model, args=targs, train_dataset=train_set,
                      eval_dataset=valid_set[:args.eval_examples], data_collator=collate)
    trainer.train()
    if torch.cuda.is_available():
        print(f"peak GPU memory: {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB", flush=True)
    if not args.smoke:
        model.save_pretrained(str(work / "adapters_cuda"))
        print(f"saved {work / 'adapters_cuda'}", flush=True)


def merge(args) -> None:
    """Fold the adapter into full-precision weights, ready for llama.cpp's GGUF converter."""
    from peft import PeftModel

    work = Path(args.work)
    tokenizer, model = _load(args.model, args.bf16, four_bit=False)
    model = PeftModel.from_pretrained(model, str(work / "adapters_cuda")).merge_and_unload()
    out = work / "merged"
    model.save_pretrained(str(out), safe_serialization=True)
    tokenizer.save_pretrained(str(out))
    print(f"merged weights in {out}; next: llama.cpp convert_hf_to_gguf.py {out}", flush=True)


def main() -> int:  # pragma: no cover - CLI, needs a CUDA GPU
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, help="train_lora.py working dir (has data/)")
    ap.add_argument("--model", default=BASE_MODEL)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=MAX_LEN)
    ap.add_argument("--eval-examples", type=int, default=30,
                    help="validation examples per evaluation (each is ~13K tokens)")
    ap.add_argument("--bf16", action="store_true", help="Ampere or newer only")
    ap.add_argument("--smoke", type=int, default=0, help="run only N steps, save nothing")
    ap.add_argument("--merge", action="store_true", help="merge the saved adapter")
    args = ap.parse_args()
    merge(args) if args.merge else train(args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
