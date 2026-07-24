# Evidence Checklist

The minimum package funders (Gates/GIZ AIEP, AIM for Scale, CGIAR, FAO/DPGA) converge on
before serious money moves. Status as of 2026-07-25.

| Requirement | Status | Where / next step |
|---|---|---|
| **Approved open licence** | ✅ Done | MIT. |
| **Privacy / do-no-harm policy** | ✅ Done | [PRIVACY.md](../dpg/PRIVACY.md); in-chat `/export` + `/delete_me`. |
| **Documented advice-safety evaluation** | ✅ Done | [safety_eval](../dpg/safety_eval/); 191 probes, scored each release. |
| **Platform independence** | ✅ Done | Self-hostable open-weights (`openai_compat`); DPG indicator 4. |
| **DPG registration** | ⏳ Ready to submit | [Application draft](../dpg/APPLICATION_DRAFT.md) — owner submits. |
| **A named deployment partner** | ❌ Not yet | Approach WorldFish ([brief](WORLDFISH_BRIEF.md)). |
| **Real users (target 100–1,000)** | ❌ Not yet | Start as **user zero**; analytics already measures this (`python -m agronaut_agent.analytics`). |
| **Outcome survey (60 Decibels-style)** | ❌ Not yet | After first cohort of users; a short structured survey of measured benefit. |
| **≥1 local language** | ⏳ Partial | Voice + LLM already reply in the user's language; report/menu localization awaits a partner-chosen language. |
| **A documented case study** | ❌ Not yet | Fill [CASE_STUDY_TEMPLATE.md](CASE_STUDY_TEMPLATE.md) with your own system. |

## How "real users" gets measured

Usage analytics is already content-free and running (counts + funnels, hashed ids). To see
where you stand at any time:

```bash
python -m agronaut_agent.analytics
# Distinct users, users who sized a system, and event counts.
```

That number — distinct users, and how many reached a real design — is the metric a funder
asks for. Everything above the line is done; everything below it is real-world work that
starts with **submit the DPG application** and **become user zero**.
