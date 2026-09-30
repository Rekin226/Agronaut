"""LoRA fine-tune of the local model on the synthetic consultant dialogues, for Ollama.

Apple silicon route (the maintainer's machine): mlx-lm trains a LoRA adapter on the Hugging
Face weights that match the Ollama tag, fuses it, and Ollama imports the fused model. On a GPU
box the same data works with any trainer that reads OpenAI-shaped chat JSONL with tools
(TRL's SFTTrainer, Unsloth, axolotl); only this wrapper is Mac-specific.

Steps, each skippable so a failed step can be resumed:
    split   dialogues.jsonl -> DIR/train.jsonl + DIR/valid.jsonl, split BY PERSONA so no
            conversation has turns on both sides (a leaked dialogue flatters valid loss)
    train   python -m mlx_lm lora ... -> DIR/adapters/
    fuse    python -m mlx_lm fuse ... -> DIR/fused/
    ollama  DIR/Modelfile, then `ollama create agronaut-consult:4b -f DIR/Modelfile`

Run it:
    pip install mlx-lm            # separate from Agronaut's own requirements, on purpose
    python -m scripts.finetune.train_lora --data data/finetune/dialogues.jsonl \\
        --work data/finetune/run1

Nothing here changes Agronaut's default model. A tuned model is offered only after it passes
the promotion gate in docs/dpg/finetune/README.md.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

BASE_MODEL = "Qwen/Qwen3.5-4B"      # the weights behind Ollama's qwen3.5:4b (Apache-2.0)
OLLAMA_NAME = "agronaut-consult:4b"
NUM_CTX = 32768                      # match agent.llm.DEFAULT_OLLAMA_NUM_CTX (#181)


def split_by_persona(lines: list[dict], valid_share: float = 0.1,
                     seed: int = 7) -> tuple[list[dict], list[dict]]:
    """Train/valid split where every persona lands wholly on one side."""
    personas = sorted({ex.get("persona", "") for ex in lines})
    rng = random.Random(seed)
    rng.shuffle(personas)
    n_valid = max(1, round(len(personas) * valid_share)) if len(personas) > 1 else 0
    valid_ids = set(personas[:n_valid])
    strip = lambda ex: {k: v for k, v in ex.items() if k != "persona"}  # noqa: E731
    train = [strip(ex) for ex in lines if ex.get("persona", "") not in valid_ids]
    valid = [strip(ex) for ex in lines if ex.get("persona", "") in valid_ids]
    return train, valid


def lora_config(model: str, data_dir: Path, adapter_dir: Path, n_train: int,
                epochs: float = 2.0, batch_size: int = 1, rank: int = 16) -> dict:
    """mlx-lm LoRA settings. Conservative: a small rank and few epochs teach STYLE without
    overwriting what the base model knows about tool calling and language."""
    iters = max(50, int(n_train * epochs / batch_size))
    return {
        "model": model, "train": True, "data": str(data_dir),
        "adapter_path": str(adapter_dir), "iters": iters, "batch_size": batch_size,
        "learning_rate": 1e-4, "num_layers": 16, "max_seq_length": 12288,
        "grad_checkpoint": True, "mask_prompt": True,
        "lora_parameters": {"rank": rank, "scale": 20.0, "dropout": 0.05},
        "steps_per_eval": max(25, iters // 10), "save_every": max(50, iters // 5),
    }


def modelfile(fused_dir: Path) -> str:
    return (f"FROM {fused_dir.resolve()}\n"
            f"PARAMETER num_ctx {NUM_CTX}\n"
            "PARAMETER temperature 0.3\n")


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="JSONL from generate_dialogues")
    ap.add_argument("--work", required=True, help="working directory for this run")
    ap.add_argument("--model", default=BASE_MODEL)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--skip", default="", help="comma-separated steps to skip: "
                                               "split,train,fuse,ollama")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    work = Path(args.work)
    data_dir, adapters, fused = work / "data", work / "adapters", work / "fused"

    if "split" not in skip:
        lines = [json.loads(ln) for ln in Path(args.data).read_text().splitlines() if ln.strip()]
        train, valid = split_by_persona(lines)
        data_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in (("train", train), ("valid", valid)):
            (data_dir / f"{name}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        print(f"split: {len(train)} train / {len(valid)} valid examples")

    if "train" not in skip:
        n_train = sum(1 for _ in (data_dir / "train.jsonl").open())
        cfg = lora_config(args.model, data_dir, adapters, n_train, args.epochs, rank=args.rank)
        cfg_path = work / "lora.yaml"
        import yaml
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
        _run([sys.executable, "-m", "mlx_lm", "lora", "--config", str(cfg_path)])

    if "fuse" not in skip:
        _run([sys.executable, "-m", "mlx_lm", "fuse", "--model", args.model,
              "--adapter-path", str(adapters), "--save-path", str(fused)])

    if "ollama" not in skip:
        (work / "Modelfile").write_text(modelfile(fused))
        print(f"\nNext:\n  ollama create {OLLAMA_NAME} -f {work / 'Modelfile'}\n"
              "  then run the promotion gate in docs/dpg/finetune/README.md")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
