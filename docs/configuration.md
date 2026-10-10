# Configuration

`agronaut setup` writes all of this for you. This page is the reference for doing it by hand.

Config lives in `~/.config/agronaut/.env` for an installed copy, or `./.env` in a checkout.

## Pluggable LLM backend (open models)

The chat layer is model-agnostic — pick a backend with one env var, no code change:

| Provider | `LLM_PROVIDER` | Notes |
|---|---|---|
| Ollama (local) | `ollama` | **Offline, default (`qwen3.5:4b`), drives the full tool-calling agent.** The shortest path for a grower self-hosting with no API key: `ollama pull qwen3.5:4b` and go. Pick a tool-capable tag: older ones (`llama3`, `mistral`) bind tools and then never call any. Photos too: `VLM_PROVIDER=ollama` with `ollama pull llama3.2-vision`. |
| Claude (hosted) | `anthropic` | Default `claude-sonnet-5-5`, native tool calling, plus prompt caching, native citations, web search, strict tool schemas and PDF reading (see [On Claude](#on-claude)). Needs `ANTHROPIC_API_KEY`. |
| NVIDIA (hosted) | `nvidia` | OpenAI-compatible open models; free tier. Needs `NVIDIA_API_KEY`. |
| Hugging Face | `hf` | Default `Qwen/Qwen2.5-7B-Instruct` (Apache-2.0, strong at JSON). Needs `HUGGINGFACEHUB_API_TOKEN`. |
| Self-hosted (OpenAI-compatible) | `openai_compat` | Zero proprietary API — point `OPENAI_COMPAT_BASE_URL` at your own vLLM / llama.cpp / LM Studio / TGI server. Drives the full tool-calling agent with an open-weights model you host. |

### On Claude

Claude runs the same agent as every other provider, with these on top. Each is on by default
and can be switched off:

| Setting | Default | What it does |
|---|---|---|
| `AGRONAUT_PROMPT_CACHE` | on | Caches the fixed prompt and tool definitions (about 18K tokens) for an hour, and each turn's earlier calls for the rest of the turn. A cached read costs about a tenth of fresh input. |
| `AGRONAUT_NATIVE_CITATIONS` | on | Sends knowledge passages as search results, so the API ties each cited passage to the sentence that used it. Cited sources the reply does not already name are added as one short "Sources:" line. |
| `AGRONAUT_WEB_SEARCH` / `AGRONAUT_WEB_SEARCH_MAX` | on / 3 | Web search for current, local facts (prices, suppliers, rules). Billed at $10 per 1,000 searches; the cap is per request. Web sources are always shown. |
| `AGRONAUT_WEB_FETCH` / `AGRONAUT_WEB_FETCH_MAX` | on / 2 | Reads a page the grower sends or a search found. No fee beyond tokens. |
| `AGRONAUT_STRICT_TOOLS` | on | Strict schemas on the eight tools whose arguments become numbers (sizing, design, readings, cost), so those arguments always match. The first call compiles the schemas (about 30 s), then they are cached for 24 hours after last use. |
| `AGRONAUT_CLAUDE_EFFORT` | low | How hard the model thinks before answering: `low`, `medium`, `high`, `xhigh` or `max`. |

PDFs (a water-lab report, a feed label) sent on Telegram or WhatsApp are read on Claude only;
their values are shown back to the grower to confirm before anything is logged.

### Self-hosted, no vendor (the open-weights path)

Agronaut is meant to be run by the grower, on their own machine, so the no-vendor path is
the one that matters most. The shortest version is Ollama:

```bash
ollama pull qwen3.5:4b              # the brain
ollama pull llama3.2-vision         # optional: photo understanding
export LLM_PROVIDER=ollama          # this is already the default
export VLM_PROVIDER=ollama          # only needed if you pulled the vision model
python bot.py
```

**Context window.** Agronaut asks Ollama for a 32K-token context. Left to itself, Ollama
gives any machine under 24 GiB of memory 4K, and Agronaut's instructions and tool
definitions alone are about 11K tokens. Ollama does not refuse a prompt that is too long: it
silently cuts the middle out, which is where the instructions live (measured in #181). A
larger context uses more memory, so on a small machine you can lower it with
`AGRONAUT_OLLAMA_NUM_CTX` (`agronaut doctor` warns below 16K). qwen3.5 can also "think" before
answering; that is off by default because on a laptop it costs minutes, and
`AGRONAUT_OLLAMA_THINK=on` turns it back on.

That is the whole setup: no API key, no account, no connectivity after the pull. Every
subsystem a grower touches runs on their own machine — the tool-calling agent, the
deterministic twin, retrieval, and photo understanding.

A note on vision tags: a text-only model will accept an image, ignore it, and describe
something plausible that is not in your photograph. Agronaut asks Ollama whether the model
can see and refuses to start the vision path if it positively says no, but an old Ollama
that reports nothing cannot be checked — so pull a tag you know does vision
(`llama3.2-vision`, `qwen2.5vl`, `llava`, or `moondream` on a small machine).

For more control over serving (batching, quantisation, a shared box), use any
OpenAI-compatible server instead:

```bash
# example: vLLM serving a tool-calling-capable open model
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct
# then:
export LLM_PROVIDER=openai_compat
export OPENAI_COMPAT_BASE_URL=http://localhost:8000/v1
python bot.py
```

This runs the deterministic core **and** the tool-calling assistant with no proprietary
dependency — the configuration Agronaut submits for [Digital Public Good](dpg/)
platform-independence.

Override the model with `LLM_MODEL`. Provider libraries are imported lazily — install only
the one you use. The design/optimizer modes run with **no LLM dependency at all.**

## Environment variables

### Telegram

| Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from [@BotFather](https://t.me/BotFather) |
| `AGRONAUT_ALLOWED_IDS` | comma-separated Telegram user IDs allowed to use the bot (empty = open to anyone, discouraged) |

### Optional settings

| Var | Purpose |
|---|---|
| `AGRONAUT_RELEVANCE_MAX_DISTANCE` | how far a passage may be and still be used as context (default 1.50, `off` disables). Calibrated against the golden set; **not portable** — re-run `python -m scripts.retrieval_sweep --all` after any corpus or embedding-model change |
| `AGRONAUT_HYBRID` / `AGRONAUT_HYBRID_BETA` | keyword+semantic fusion, on by default at β=0.90 (β is the semantic weight) |
| `AGRONAUT_MAX_PER_SOURCE` | how many passages one source may contribute to a single answer (default 2; `1` favours breadth, `0` disables) |
| `AGRONAUT_INDEX_CACHE` | the built index is cached under `data/.index_cache/`, keyed by a corpus fingerprint; `off` rebuilds every time |
| `AGRONAUT_RERANK` / `AGRONAUT_MD_HEADERS` / `AGRONAUT_PDF_SECTIONS` | techniques that measured *worse* on this corpus and ship disabled — kept because the verdict is corpus-dependent (see `docs/dpg/retrieval_eval/techniques.json`) |
| `LLM_PROVIDER` / `NVIDIA_API_KEY` | the tool-calling brain. Defaults to `ollama` (local, no key); `nvidia` is free at [build.nvidia.com](https://build.nvidia.com) |
| `LLM_MODEL` | optional. Local default `qwen3.5:4b`. On NVIDIA, `mistralai/mistral-nemotron` measured ~20x faster than `llama-3.3-70b` with correct tool calls ([docs/telegram_twin_testing.md](telegram_twin_testing.md)) |
| `AGRONAUT_OLLAMA_NUM_CTX` | context window requested from Ollama (default 32768). Lower only if memory runs out; below 16384 the prompt gets cut and `agronaut doctor` warns |
| `AGRONAUT_OLLAMA_THINK` | `on` lets a thinking model (qwen3.5) reason before answering. Off by default: slow on a laptop |
| `VLM_PROVIDER` / `VLM_MODEL` | optional photo understanding — send a picture of a sick fish or yellowing leaf and the bot describes it, then diagnoses through the same cited flow. Defaults to a hosted NVIDIA vision model (`VLM_PROVIDER=nvidia`, needs `NVIDIA_API_KEY`); set `VLM_PROVIDER=ollama` and `ollama pull llama3.2-vision` to run it locally with no key and no connectivity. `AGRONAUT_VISION=off` disables. The vision model only *observes*: a deterministic guard strips any reading or prescription out of its description, and the diagnosis itself comes from a fixed, cited triage table (`aqua_model/triage.py`) that returns a ranked differential — never a single verdict. Photos work on Telegram, WhatsApp, and the web chat. |
| `ASR_PROVIDER` / `ASR_MODEL` | optional voice notes — a spoken message is transcribed then answered in the same language. Defaults to a **local** faster-whisper model (works offline — best for low-connectivity field use; needs `pip install faster-whisper`). Set `ASR_PROVIDER=nvidia` for a hosted endpoint; `AGRONAUT_VOICE=off` disables. |

### WhatsApp

See [whatsapp_setup.md](whatsapp_setup.md) for where each value comes from.

| Var | Purpose |
|---|---|
| `WHATSAPP_TOKEN` | the **access** token (long, `EAA…`), Meta Step 1 > Generate token |
| `WHATSAPP_PHONE_NUMBER_ID` | the sender phone-number id, Meta Step 1 |
| `WHATSAPP_WABA_ID` | WhatsApp Business account id, Meta Step 1 (used by `--check`) |
| `WHATSAPP_VERIFY_TOKEN` | the **verify** token (short); setup makes it, you paste it into Meta's webhook form |
| `WHATSAPP_APP_SECRET` | App settings > Basic; verifies that messages really come from Meta |
| `AGRONAUT_ALLOWED_IDS` | your own number, country code first, no leading 0 (Taiwan 0912… is 886912…) |

## Where it keeps things

In a checkout, the knowledge base, the reference tables, the
fetched-page cache and the SQLite memory DB all sit beside the source, as before. Installed
non-editably, the cited corpus and the reference tables (price book, growth calibration, the
twin's validation record) are read from `<prefix>/share/agronaut` and state goes to your XDG
directories rather than into `site-packages` — override any of it with `AGRONAUT_CORPUS_DIR`,
`AGRONAUT_REFERENCE_DIR`, `AGRONAUT_CACHE_DIR`, or `AGRONAUT_DATA_DIR`.
