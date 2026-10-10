# Agronaut — Privacy & Data Policy

_Last updated: 2026-10-10_

Agronaut is a personal agronomy assistant. This policy describes exactly what it collects,
why, how long it keeps it, and how you control it. It is written to satisfy the
[Digital Public Goods Standard](https://digitalpublicgoods.net/standard/) privacy and
do-no-harm indicators.

## What is collected

Agronaut only stores what you tell it, so it can give advice anchored to *your* system and
remember it across chats:

| Data | Example | Why |
|---|---|---|
| Identity | your channel + native id (e.g. `telegram:12345`), display name | to route your conversation and keep your memory separate from others' |
| System profile | fish species, crop, grow area, water temperature, water budget, location if you give it | to size/optimize/troubleshoot *your* system |
| Conversation | the messages you send and the assistant's replies | continuity within and across sessions |
| Notes & outcomes | "had an ammonia spike in June", "the water change fixed it" | to improve future advice |
| Measurements | FCR, harvest weight, crop yield you report | to calibrate your future sizings to reality |

Agronaut does **not** collect device identifiers, contacts, location beyond what you type,
or any special-category data. Photos and voice notes you send are processed to produce a
text observation/transcript and are **not retained** as media. Embedded photo metadata —
EXIF GPS above all — is stripped before an image is sent to a vision model, so a
geotagged camera file does not leak a location you did not type.
Stripping is best-effort: an image the software cannot decode is sent as received rather
than discarded, so an unusual or malformed file may still carry its metadata.

## When the operator runs Agronaut on Claude

Running on a local model keeps every message on the operator's machine. An operator who
chooses Claude (`LLM_PROVIDER=anthropic`) sends the conversation to Anthropic to be answered,
and on that provider two more things can leave the machine:

- **Web search and web fetch.** To answer what the curated knowledge cannot (today's prices,
  suppliers, regulations), Claude may run a web search, which sends a short search query
  through Anthropic to a search provider, or read a public web page. Queries are written by
  the model from your question, for example "prix aliment tilapia Ouagadougou"; they are not
  your messages. The operator can switch either off (`AGRONAUT_WEB_SEARCH=off`,
  `AGRONAUT_WEB_FETCH=off`).
- Sources found on the web are shown with the reply, and a figure from a web page is never
  used as an input to a sizing or design calculation.
- **PDFs.** A PDF you send (a water test, a feed label) is read by Claude into plain text and
  then handled like a message; the file itself is not kept. Values read from it are shown back
  to you to confirm before anything is logged, and nothing from it is saved to your profile.
  On a local model PDFs are declined, and a photo of the page works instead.

## How it is used

- Only to serve *you*, within your own conversation.
- Numbers you provide are validated at a trust gate before any engineering calculation; a
  rejected value is never stored.
- Your data is **never** used to train any model. The language model is used only to route
  and phrase; the engineering answers come from a deterministic, cited model.
- Community knowledge sharing is **opt-in and human-gated**: a lesson only becomes visible
  to other operators if it is generalized, stripped of personal detail, and explicitly
  approved by the operator in a local review tool. Your identity and original wording never
  leave that review screen.

## Your controls

Reachable directly from chat at any time:

- **`/whoami`** — see what Agronaut remembers about you.
- **`/export`** — download everything Agronaut holds about you as open JSON
  (portable, non-proprietary format).
- **`/reset`** — clear the current conversation (keeps your long-term profile).
- **`/forget`** — wipe your profile and notes.
- **`/delete_me`** — permanently erase *all* your data: conversation, profile, notes, and
  measurements. Nothing about you remains.

## Storage & retention

- Data is stored in a single local SQLite database on the operator's own machine/server.
  There is no central Agronaut server and no third-party analytics on your content.
- The operator may keep **local usage analytics** — event counts, funnels and timings only
  (e.g. how many messages, how many designs, how long a turn took, how many tokens it cost).
  These are content-free by construction: no message text is recorded, and user ids are stored
  only as a truncated one-way hash, so distinct users can be counted without identifying
  anyone. Disable with `AGRONAUT_ANALYTICS=off`.
- Each turn's events share a **trace id** so one turn can be read back as one path through the
  pipeline (`agronaut traces`). The id is a fresh random token minted per turn: it is never
  derived from your identity, never reused across turns, and links only the shape of a single
  turn — which tools ran, how many passages came back, where the time went. It cannot be used
  to follow you between turns.
- A **thumbs up/down** (`/good`, `/bad`) is stored as a bare rating, 1 or -1. There is
  deliberately no comment field, so this signal cannot carry anything you wrote.
- **Optional full traces (off by default).** An operator debugging their own bot can turn on
  local tracing to Arize Phoenix (`AGRONAUT_PHOENIX=on`). Unlike the analytics above, these
  traces DO contain message text: your messages, the replies, and what the tools and the
  knowledge search returned. They are sent only to a Phoenix running on the operator's own
  machine (Agronaut refuses another host unless the operator overrides it), Phoenix's own web
  analytics are switched off when it is started with `agronaut phoenix`, and nothing is sent to
  Arize. If you message someone else's Agronaut bot, ask them whether this is on.
- Data is retained until you delete it. An operator deployment may set its own retention
  window; this reference build retains until erasure is requested.
- Access control: on Telegram/WhatsApp an allowlist restricts who can use a given
  deployment; each user can only ever read or delete their own data.

## Do no harm

- Every quantitative answer lists its cited source coefficients and an explicit
  "what is NOT modeled" list, so a confidently-wrong design cannot masquerade as complete.
- The assistant is instructed never to state a sizing number that did not come from the
  validated engineering model.
- Advice quality is tracked against a fixed safety/accuracy question set each release (see
  `docs/dpg/AI_TRANSPARENCY.md`).

## Contact

Agronaut is open source (MIT). Raise a concern at the project repository's issue tracker.
