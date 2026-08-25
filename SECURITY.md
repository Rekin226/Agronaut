# Security & Safety Policy

Agronaut has two kinds of vulnerability, and the second one is the unusual one.

## 1. Software vulnerabilities

The usual: secrets exposure, injection, dependency issues, anything that compromises a
deployment or its users' data.

**Do not open a public issue.** Email **abdoulrachid03@gmail.com** with what you found, how to
reproduce it, and what an attacker could do with it. Expect an acknowledgement within a week.

Areas worth attention if you're looking:

- **Channel adapters** (`agronaut_agent/channels/`) accept untrusted webhook input. The
  WhatsApp adapter verifies Meta's `X-Hub-Signature-256`; an allowlist gates who can talk to a
  deployment at all.
- **Prompt injection.** Text inside a user's *photo* reaches the model. The observation guard
  strips fabricated readings and prescriptions, but **it is not a prompt-injection defence** and
  does not claim to be. A path that gets an injected instruction to call a tool with attacker
  values would be a real finding.
- **Secrets.** `.env` is git-ignored; API keys must never reach logs, analytics, or the model
  context.

## 2. Safety issues — advice that could hurt a system

This project produces engineering and husbandry advice. Advice can be dangerous even when the
code is flawless, and we treat that as a security-class problem:

- a sizing result that would **undersize aeration or biofiltration** (either can kill a stock
  overnight);
- a treatment suggestion that would **harm the fish, the plants, or the biofilter** — copper in
  a coupled system, antibiotics that wipe out nitrifying bacteria, a pesticide that can reach
  the water;
- a **coefficient whose cited source doesn't support it**, or a citation that doesn't say what
  we claim it says;
- a triage candidate presented as a **verdict** where the evidence can't support one.

**Please report these as public issues** with the `bug` label — unlike a software
vulnerability, other operators benefit from seeing the discussion immediately. If you'd rather
raise it privately, the email above works too.

Reporting a wrong number with its correct source is one of the most valuable contributions
anyone can make here.

## What we promise

- Every coefficient is auditable without trusting any model: value, range, unit, source.
- Every design states what it does not model.
- The advice-safety golden set (`python -m scripts.safety_eval`) runs in CI and fails the build
  on any CRITICAL regression, so a promise made once keeps being checked.

## Supported versions

Agronaut is pre-1.0 and moves fast. Fixes land on `main`; there are no backported release
branches yet.
