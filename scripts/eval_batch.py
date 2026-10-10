"""Run an evaluation's judge calls through Anthropic's Message Batches API.

The evals ask their judge one prompt at a time (`ask(prompt) -> str`), and some prompts
depend on earlier answers: the faithfulness judge splits an answer into claims, then rules
on each claim. The Batch API is asynchronous and costs half the standard price, so it wants
all the prompts of a stage at once.

`batched` bridges the two without touching the scoring code. It runs the scoring with an `ask`
that records every prompt it has no answer for (and returns "" so the code moves on), sends
those as one batch, then runs the scoring again with the answers in hand. Each pass gets one
stage further; it stops when a pass asks nothing new. Scoring is deterministic given the
judge's answers, so the final pass is exactly the run a live judge would have produced, and
the work redone in early passes is local (parsing, embeddings), never a model call.

    from scripts.eval_batch import batched, claude_batch
    result = batched(lambda ask: score_everything(ask), claude_batch("claude-opus-5-5"))
"""

from __future__ import annotations

import hashlib
import time


def batched(fn, submit, max_passes: int = 6):
    """`fn(ask)` evaluated with every judge prompt answered by `submit(prompts) -> {prompt:
    answer}`, one batch per stage. Raises if `fn` still asks new prompts after `max_passes`."""
    answers: dict[str, str] = {}
    for _ in range(max_passes):
        missing: list[str] = []

        def ask(prompt: str) -> str:
            if prompt in answers:
                return answers[prompt]
            if prompt not in missing:
                missing.append(prompt)
            return ""

        result = fn(ask)
        if not missing:
            return result
        answers.update(submit(missing))
    raise RuntimeError(f"still asking new judge prompts after {max_passes} batches")


def _custom_id(i: int, prompt: str) -> str:
    return f"p{i}-{hashlib.sha1(prompt.encode()).hexdigest()[:12]}"


def text_of(message) -> str:
    """The text blocks of a Messages API reply (thinking blocks are not the answer)."""
    return "".join(getattr(b, "text", "") or "" for b in getattr(message, "content", []) or []
                   if getattr(b, "type", None) == "text")


def claude_batch(model: str, max_tokens: int = 4096, poll_seconds: float = 30.0,
                 client=None, log=print):
    """A `submit` for `batched` that sends prompts to `model` as one Message Batch and waits.

    A prompt whose request errored or expired comes back as "" so the eval counts it as
    unjudged, the same as a failed live judge call, rather than failing the whole run.
    """
    def submit(prompts: list[str]) -> dict[str, str]:
        import anthropic

        c = client or anthropic.Anthropic()
        ids = {_custom_id(i, p): p for i, p in enumerate(prompts)}
        batch = c.messages.batches.create(requests=[
            {"custom_id": cid, "params": {"model": model, "max_tokens": max_tokens,
                                          "messages": [{"role": "user", "content": p}]}}
            for cid, p in ids.items()])
        log(f"  batch {batch.id}: {len(prompts)} judge prompts to {model}")
        while batch.processing_status != "ended":
            time.sleep(poll_seconds)
            batch = c.messages.batches.retrieve(batch.id)
            counts = batch.request_counts
            log(f"    {counts.succeeded + counts.errored + counts.expired + counts.canceled}"
                f"/{len(prompts)} done")
        out = {p: "" for p in prompts}
        failed = 0
        for item in c.messages.batches.results(batch.id):
            prompt = ids.get(item.custom_id)
            if prompt is None:
                continue
            if item.result.type == "succeeded":
                out[prompt] = text_of(item.result.message)
            else:
                failed += 1
        if failed:
            log(f"    {failed} requests did not succeed; counted as unjudged")
        return out
    return submit
