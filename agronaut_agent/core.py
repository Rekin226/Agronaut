"""AgronautAgent — the channel-agnostic, tool-calling brain.

handle_message(channel, channel_user, text) is the single seam every channel adapter
calls. The LLM orchestrates Agronaut's deterministic tools and explains their results; a
bounded tool-loop runs the calls. The system prompt forbids inventing numbers — every
figure must come from a tool result, with its cited coefficients and caveats passed through.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.llm import ResilientChat, build_fallback_chat, get_chat_model, get_llm
from agent.vision import sanitize_observation

from . import memory_extract, profile, runtime, semantic, twin_view
from .store import (
    CalibrationStore,
    CommunityStore,
    ConversationStore,
    FollowupStore,
    MemoryStore,
    ProposalStore,
    ReadingStore,
    _Db,
    _now,
)
from .tools import AGRONAUT_TOOLS

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Agronaut, a personal aquaponics design and troubleshooting assistant.

You speak with operators and farmers — be concrete, warm, and brief. Reply in the user's language.
Keep replies short and scannable for a phone: lead with the point, use short bullets for numbers or steps.

YOU RUN A CONSULTATION, NOT A Q&A. Your job is to understand the person before you advise them.

1. FIND THE GOAL. Every conversation has one of three goals — figure out which:
   - design: size a new system from scratch. Agronaut sizes two kinds: AQUAPONIC (fish +
     plants — use size_aquaponics_system) and HYDROPONIC (plants only, nutrients dosed as
     salts, NO fish — use size_hydroponic_system_tool). If the user mentions fish, pick
     aquaponics; if they say plants-only / hydroponic / no fish, pick hydroponics; if
     unclear, ask which they want. If the user wants SEVERAL crops in one system (a mixed
     bed — e.g. "lettuce and basil and some tomato"), use size_mixed_bed_aquaponics with a
     crop_plan of {crop, area_m2} entries instead of forcing a single crop; it sizes the
     shared system and warns if the crops can't share one water chemistry.
   - optimize: find the best fish/crop ratio for an existing or planned system.
   - troubleshoot: diagnose a problem (sick fish, bad water, failing plants).
   If the goal is unclear, ask — briefly — what they're trying to do. Do not guess.

2. GATHER THE ESSENTIALS, THEN GIVE A FIRST CUT. Each goal needs a few facts before you
   can help well:
   - design needs: fish species, crop, grow area (m²), water temperature, daily water budget.
   - optimize needs: grow area (m²), water temperature, daily water budget, objective
     (food / protein / water_efficiency).
   - troubleshoot needs: the symptom, plus relevant water readings (temperature, pH,
     dissolved oxygen, ammonia). When the user describes something VISIBLE — leaf colour and
     WHERE on the plant (older vs newer leaves), root appearance, water colour, fish behaviour
     or marks, visible pests — call triage_visual_symptoms. It returns a ranked, cited
     differential plus the checks that discriminate between the candidates. Present it AS a
     differential: lead with the cheapest environmental check, never collapse it to one
     confident diagnosis, and keep each candidate's source. A photo turn already carries this
     differential; use it rather than re-deriving one.
   The system note above tells you what is still missing ("Still need for ..."). Ask for the
   missing essentials — at most 2–4 at once, conversationally, never as a long form. Once you
   have them, ACT: call the right tool and give a useful first recommendation. Then offer to refine.
   Do NOT re-ask anything already in YOUR SYSTEM above.

3. ANCHOR EVERY RECOMMENDATION to their stated goal and their system. Generic advice is a
   failure — tie the answer to what they told you (their species, area, budget, constraints).

RESPECT THEIR PREFERENCES — the design is not one fixed template. The GROWING METHOD is
theirs to choose: raft/deep-water culture (default, forgiving, more water), NFT (light, low
water, needs reliable power), media bed (robust, also biofilters), or vertical towers (stacked
— pack ~3x the growing area onto the floor, for land-scarce sites; leafy/herbs only). If the
user expresses a preference or their situation points to one (e.g. unreliable power → not NFT;
wants low water → NFT or towers; short on floor space → vertical towers), pass system_type to
the sizing/schematic tools. If they haven't said and it matters, briefly ask which they'd
prefer rather than assuming.

If the user asks to SEE, DRAW, or picture their system (a diagram/schematic), call
render_system_schematic — it draws a labeled diagram and sends it to them as an image.
For a full interactive 3D model (greenhouse, tanks, beds, plumbing, fish), call
design_system_3d — it sends an HTML file that opens in any browser, offline.

THE DIGITAL TWIN — after the design, or for a running system. Sizing says how big;
the twin says WHAT HAPPENS: harvests, seasons, money. Offer it, don't wait to be asked:
- Weather first, once per site: if no climate slug is known (profile climate_site, or an
  error listing available slugs), call fetch_site_climate with their town — it geocodes,
  pulls last year's real weather, and saves the slug. Never ask the user to run commands.
- "How much will it produce / will it work here / do I need a heater?" -> simulate_season.
  After a design conversation leave fish_count/volume_l UNSET and pass water_budget_lpd +
  system_type, so the twin stocks the agreed design's own numbers. Compare scenarios by
  calling it twice with ONE change (greenhouse='shade' vs 'poly', heated, another crop) —
  the RELATIVE difference is the trustworthy part, and the summaries say so.
- A user with a RUNNING system: save their real setup via update_profile (tank_volume_l,
  fish_count, fish_avg_weight_g, species, crop, grow_area_m2), then simulate_my_system —
  it mirrors THEIR farm and tells you exactly what is still missing.
- "Can I double the feed / add fish / what does a cold week do?" on a live system ->
  what_if_nitrogen. Its verdicts are ratios, not absolutes, on purpose.
- THE LIVE MIRROR, for a user with a running system and a fetched site: their twin
  persists between chats and advances through their site's REAL weather. THREE MUSTS —
  these are not answerable from memory, because the answer lives in stored state your
  context cannot see:
  * the user states their running system's facts (tank litres, fish count, weights) ->
    you MUST call update_profile before replying, or the facts are lost when this chat ends;
  * the user reports a MEASUREMENT (ammonia/nitrite/nitrate/pH/temperature values, a fish
    weighing, deaths) -> you MUST call log_my_readings BEFORE replying — an unlogged
    reading never reaches the twin, and a reply without the call is a guess dressed as an
    update. Share the drift notes it returns ("model was 30% low on nitrate");
  * "how's my system / what will this week/heatwave do" -> you MUST call
    my_system_forecast — only it knows the persisted state and the real forecast.
  Pass the envelope they actually run (greenhouse='shade'/'poly'/'heated') to both.
- For the COMPLETE component design — which tanks, settling, biofilter, degasser,
  mineralization, coupled or decoupled, each with its reason — design_full_system is the
  design conversation's closing move (it also sends the 3D). It adapts to needs: ask about
  power reliability and their experience before calling it.
- "What will it cost?" -> estimate_system_cost. "Will it MAKE money / when do I get my
  money back?" -> business_case (offer labour_hours_per_week — pricing their own time
  usually decides hobby vs business — and channel='direct' when they sell at market).
  Pick the price-book region nearest them and SAY which you used.
- Surface the honesty lines these tools return (NOT modelled, unpriced items, "projection
  from literature seeds") — never trim them to make the answer look more certain.

REMEMBER AS YOU GO:
- The moment the user reveals a durable structured fact (species, area, temperature, tank
  volume, water readings, location, their goal/objective, experience level), call
  update_profile to save it. Do not wait until the end.
- For episodic things that happened or fixes that worked, call remember_about_user
  (category event / learning / preference). Honour "forget that".
- After you give an ACTIONABLE fix (a water change, a pH/temperature adjustment, a dosing
  change), call schedule_followup to check back later whether it worked — pick the delay to
  match how long the fix takes to show. Don't schedule for plans, sizing, or trivia.
- When the user reports whether something worked (now or in answer to a check-in), save it
  with remember_about_user(category='learning') so it improves your future advice.
- If a learning you saved would help other operators in general (not tied to one person's
  system), also call nominate_shared_insight with a generalized, PII-stripped one-sentence
  version — no locations, names, or personal details. The owner approves before anything is shared.
- When the operator reports a REAL measured result from their own system — the weight their
  fish reached, their measured FCR (feed used vs weight gained), or their crop yield — call
  record_measurement (metric fcr / harvest_weight / yield). Never for an estimate or a number
  you produced; only their real measurement. It calibrates their future sizings to reality.

HARD RULES (these are your credibility):
- NEVER state a sizing number, bill-of-materials quantity, or coefficient that did not come
  from a tool result. For any sizing/optimization question, CALL the tool — do not estimate.
- When a tool returns coefficients and "not modeled" caveats, surface them: cite the source of
  key numbers and remind the user these are calibration seeds, not guarantees.
- If the trust gate rejects an input (VALIDATION_FAILED), ask the user for a corrected value.
  Never guess or work around it.
- For qualitative troubleshooting, use the knowledge tool and your general knowledge; say when
  you are reasoning from general knowledge. Knowledge passages arrive labeled "[source: ...]" —
  when your advice uses one, NAME that source in your reply (e.g. "per FAO 589..."). Never
  strip the attribution.
- JUDGE EACH RETRIEVED PASSAGE BEFORE YOU USE IT. Retrieval returns the closest passages it has,
  which is not the same as passages that answer the question. If a passage is not actually about
  what the user asked, IGNORE it — do not stretch it to fit, and do not cite it. If none of them
  fit, say plainly that the knowledge base has nothing specific on this and answer from general
  husbandry knowledge, flagged as such. A confident citation attached to an irrelevant passage is
  worse than no citation, because the source makes it look verified.
  Also check search_community_knowledge for real-world operator tips, and present anything it
  returns as "reported by other operators", never as verified fact or a number.

ANSWERING FOLLOW-UPS: reuse earlier tool results ONLY when the result literally appears
earlier in this conversation — reread it there. If the number the user needs was never
computed in this conversation, CALL the tool now. NEVER write "[earlier result from ...]",
never reconstruct, paraphrase-from-memory, or imagine what a tool would have returned:
a fabricated tool result is the worst failure this assistant can produce, worse than no
answer. To judge whether a value is safe (temperature, pH, DO), read the operating_envelope
from the prior sizing result — if there is no prior sizing result, run the sizing tool."""

# Attached when the vision model names a condition. Its observation enters the turn as a
# user-provided fact, which the agent has no reason to distrust — so the doubt has to be
# stated explicitly. This routes VLM-derived claims into the same citation discipline that
# PLAN 1.3 established for KB-derived ones.
_VERDICT_INSTRUCTION = (
    "[Note: the vision model named a possible condition. That is an UNVERIFIED visual guess, "
    "not a diagnosis. Do not repeat it as a conclusion unless the knowledge base supports it "
    "and you cite the source. Otherwise, hedge it and confirm the details with the user.]"
)

_MAX_ITERS = 6
_TOOL_REPLAY_MAX_CHARS = 2000

# A reply that ANNOUNCES an action — "I'll log your readings", "Let me check your
# forecast" — and then stops, the promise substituting for the tool call. Measured on the
# six-step live validation: three consecutive turns of narration with no effect. The
# corrective nudge is safe for the ask-a-question case (see the nudge text).
_PROMISE = re.compile(r"\b(i['’]ll|i will|let me|i am going to|i'm going to)\b", re.I)


class AgronautAgent:
    def __init__(self, llm_provider=None, llm_model=None, db_path=None, chat_model=None,
                 fallback_model=None, embed_fn=None, describe_fn=None, transcribe_fn=None,
                 classify_fn=None, require_tools: bool = True):
        """`require_tools=False` builds an agent whose DETERMINISTIC surfaces work even when
        no tool-calling provider is configured — the live twin (/log, /forecast, the twin
        dashboard) reaches no model, so a missing NVIDIA_API_KEY must not deny an operator
        their own system. Chat then reports `chat_error` instead of answering."""
        self._chat_error: str | None = None
        try:
            # chat_model injectable for tests (a fake bindable model); else build from config.
            base = chat_model if chat_model is not None else get_chat_model(llm_provider, llm_model)
            # Resilience: if the primary errors/times out, fall back to a fast model so a turn is
            # never lost. Only auto-built for the real config path; injectable for tests.
            fb = fallback_model
            if fb is None and chat_model is None:
                fb = build_fallback_chat(llm_provider, llm_model)
            if fb is not None:
                base = ResilientChat(base, fb)
            self._base = base                   # unbound: used to force a final text answer
            self._bound = base.bind_tools(AGRONAUT_TOOLS)
        except Exception as exc:  # noqa: BLE001 — surfaced as chat_error, or re-raised
            if require_tools:
                raise
            self._base = self._bound = None
            self._chat_error = str(exc)
        self._tools_by_name = {t.name: t for t in AGRONAUT_TOOLS}
        db = _Db(db_path)
        self._conv = ConversationStore(db)
        self._mem = MemoryStore(db)
        self._followups = FollowupStore(db)
        self._community = CommunityStore(db)
        self._calibration = CalibrationStore(db)
        self._readings = ReadingStore(db)
        self._proposals = ProposalStore(db)
        # Semantic recall over memories — injectable for tests; lazily built for the real
        # path (model loads on first search). None -> recency fallback, the old behaviour.
        if embed_fn is None and chat_model is None:
            embed_fn = semantic.default_embedder()
        self._semantic = semantic.SemanticMemory(db, embed_fn)
        # Vision: turn a photo into a visual observation that feeds the normal turn. The VLM
        # only observes — diagnosis stays with the agent + cited KB. None -> images declined.
        if describe_fn is None and chat_model is None:
            from agent import vision
            describe_fn = vision.default_describer()
        self._describe = describe_fn
        # Optional specialist image classifier. It is an extra FEATURE source, never a verdict
        # source (see agent/classifier.py). No backend ships yet, so this is normally None and
        # the whole path is inert.
        if classify_fn is None and chat_model is None:
            from agent import classifier
            classify_fn = classifier.default_classifier()
        self._classify = classify_fn
        # Voice: transcribe a note, then run it as a normal turn. The system prompt's
        # "reply in the user's language" rule answers in the note's language. None -> declined.
        if transcribe_fn is None and chat_model is None:
            from agent import transcribe
            transcribe_fn = transcribe.default_transcriber()
        self._transcribe = transcribe_fn
        # Privacy-preserving usage analytics (counts/funnels, no content). Local-only;
        # AGRONAUT_ANALYTICS=off disables. Injectable path for tests via env.
        from .analytics import Analytics
        self._analytics = Analytics()
        # Per-user files a tool produced this turn (e.g. a rendered schematic), for the
        # channel adapter to deliver alongside the text reply. Keyed by user_id.
        self._pending_attachments: dict[str, list] = {}

    # --- context assembly -------------------------------------------------
    def _build_context(self, user_id: str, query: str | None = None) -> list:
        messages: list = [SystemMessage(content=SYSTEM_PROMPT)]

        recall = self._recall_block(user_id, query=query)
        if recall:
            messages.append(SystemMessage(content=recall))

        # Two passes over history. Past tool results go into ONE leading system-side
        # reference block; the visible transcript stays strictly user/assistant.
        #
        # Both halves of that sentence were paid for with a measured failure each:
        # - Tool results were once replayed as AIMessages prefixed "[earlier result from
        #   X]", and a validation run caught the model IMITATING that apparent house
        #   style — minting fabricated "[earlier result …]" answers for tools that never
        #   ran. The scaffold was teaching the fabrication. (A bare tool role without a
        #   matching tool_call is rejected by OpenAI-compatible APIs, so real
        #   ToolMessages cannot carry history either.)
        # - Moved to SystemMessages interleaved in the transcript, the strict chat
        #   templates of some hosted models (mistral on NVIDIA NIM) returned EMPTY
        #   replies. Mid-conversation system messages are not portable.
        history = self._conv.recent_context_messages(user_id, limit=20)
        replayed = []
        for m in history:
            if m["role"] == "tool":
                content = m["content"] or ""
                if len(content) > _TOOL_REPLAY_MAX_CHARS:
                    content = content[:_TOOL_REPLAY_MAX_CHARS] + " …[truncated]"
                replayed.append(f"--- {m['tool_name']} (earlier turn) ---\n{content}")
        if replayed:
            messages.append(SystemMessage(content=(
                "REFERENCE — tool outputs computed in earlier turns of this conversation. "
                "Reuse these numbers plainly when they answer a follow-up; anything not "
                "here has NOT been computed, so call the tool.\n\n" + "\n\n".join(replayed))))

        for m in history:
            if m["role"] == "user":
                messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                messages.append(AIMessage(content=m["content"]))
        return messages

    def _recall_block(self, user_id: str, query: str | None = None) -> str:
        """Assemble cross-session recall: goal-aware profile + missing essentials,
        episodic memories (semantically ranked against `query` when an embedder is
        available, else most-recent), and the rolling summary."""
        parts: list[str] = []
        facts = self._mem.get_facts(user_id)
        goal = facts.get("goal")

        rendered = profile.render_profile(facts, goal=goal)
        if rendered:
            parts.append(rendered)
        missing = profile.missing_essentials(goal, facts)
        if missing:
            parts.append(f"Still need for {goal}: " + ", ".join(missing))

        memories = []
        if query and self._semantic.available:
            memories = self._semantic.search(user_id, query, k=12)
        if not memories:
            memories = self._mem.get_memories(user_id)
        if memories:
            if (goal or "").strip().lower() == "troubleshoot":
                # surface what happened / what worked first when diagnosing
                memories = sorted(
                    memories,
                    key=lambda m: 0 if m["category"] in ("event", "learning") else 1,
                )
            parts.append("RECENT HISTORY\n" + "\n".join(
                f"- ({m['category']}) {m['content']}" for m in memories
            ))

        summary = self._mem.get_summary(user_id)
        if summary:
            parts.append("PAST SUMMARY: " + summary)
        return "\n\n".join(parts)

    def _rescue_leaked_tool_call(self, text: str) -> dict | None:
        """Parse a tool call leaked as plain text into a structured call, or None when the
        text is not a leak (or names no known tool, or carries an unparseable payload —
        those fall through to the normal no-tool path rather than guessing).

        Two dialects, both measured live on NVIDIA NIM under load (validation runs,
        2026-08), in both of which the model had chosen the RIGHT tool with the RIGHT
        arguments and the adapter delivered it as gibberish text:
          1. mistral-native:  [TOOL_CALLS]update_profile{"updates": {...}}
          2. bare JSON-ish:   {"name": "log_my_readings", "parameters": {... True, None}}
             — with Python literals, so json.loads alone cannot read it."""
        import ast
        import json as _json
        import re

        def _payload(raw: str):
            try:
                obj, _end = _json.JSONDecoder().raw_decode(raw)
                return obj
            except ValueError:
                try:
                    return ast.literal_eval(raw.strip())
                except (ValueError, SyntaxError):
                    return None

        m = re.search(r"\[TOOL_CALLS\]\s*(\w+)\s*\{", text)
        if m and m.group(1) in self._tools_by_name:
            args = _payload(text[m.end() - 1:])
            if isinstance(args, dict):
                return {"name": m.group(1), "args": args, "id": "rescued-leaked-call"}
            return None

        brace = text.find("{")
        if brace >= 0:
            obj = _payload(text[brace:])
            if (isinstance(obj, dict) and obj.get("name") in self._tools_by_name):
                args = obj.get("parameters") or obj.get("arguments") or obj.get("args") or {}
                if isinstance(args, dict):
                    # None-valued optionals mean "not given" — drop them so tool defaults
                    # apply instead of exploding on float(None).
                    args = {k: v for k, v in args.items() if v is not None}
                    return {"name": obj["name"], "args": args, "id": "rescued-leaked-call"}
        return None

    # --- model instrumentation -------------------------------------------
    @staticmethod
    def _usage(ai) -> tuple:
        """(input_tokens, output_tokens) from a model reply, or (None, None).

        Two shapes because providers disagree: LangChain normalises modern chat models into
        `usage_metadata`, while others only pass through the raw OpenAI-style
        `response_metadata['token_usage']`. Anything else yields None rather than a zero —
        an absent count and a genuine zero must not aggregate into the same number.
        """
        meta = getattr(ai, "usage_metadata", None)
        if isinstance(meta, dict) and ("input_tokens" in meta or "output_tokens" in meta):
            return meta.get("input_tokens"), meta.get("output_tokens")
        raw = getattr(ai, "response_metadata", None) or {}
        tu = raw.get("token_usage") or raw.get("usage") or {}
        if isinstance(tu, dict) and tu:
            return (tu.get("prompt_tokens") or tu.get("input_tokens"),
                    tu.get("completion_tokens") or tu.get("output_tokens"))
        return None, None

    def _invoke_model(self, model, messages: list, stage: str):
        """Call a chat model, timing it and accumulating its cost into the turn.

        Every model call in the turn goes through here. The course is explicit that the
        transformer is the latency and the cost, and this project measured only retrieval —
        the fast stage — until now. Instrumenting the call site rather than the turn also
        separates the two questions a slow turn raises: was it one slow call, or six.
        """
        t0 = time.perf_counter()
        try:
            ai = model.invoke(messages)
        finally:
            elapsed = int((time.perf_counter() - t0) * 1000)
        tin, tout = self._usage(ai)
        runtime.record_llm_call(elapsed, tin, tout)
        self._analytics.record("llm_call", stage=stage, latency_ms=elapsed,
                               tokens_in=tin, tokens_out=tout)
        return ai

    # --- the tool-calling loop -------------------------------------------
    def _run_tool_loop(self, messages: list, user_id: str) -> str:
        fabrication_nudged = False
        promise_nudged = False
        for _ in range(_MAX_ITERS):
            ai = self._invoke_model(self._bound, messages, "agent")
            messages.append(ai)
            tool_calls = getattr(ai, "tool_calls", None)
            if not tool_calls:
                # Rescue leaked tool calls. Measured (validation run, 2026-08): mistral on
                # NVIDIA NIM sometimes emits its NATIVE tool-call syntax as plain text —
                # `[TOOL_CALLS]update_profile{"updates": {...}}` — which the adapter fails
                # to parse into structured tool_calls, so a correct decision by the model
                # dies as gibberish text. Parse it ourselves and run the call: the leaked
                # AIMessage is replaced with a well-formed one so the transcript stays
                # valid for strict OpenAI-compatible templates.
                rescued = self._rescue_leaked_tool_call(ai.content or "")
                if rescued is not None:
                    messages.pop()
                    messages.append(AIMessage(content="", tool_calls=[rescued]))
                    tool_calls = [rescued]
            if not tool_calls:
                text = (ai.content or "").strip()
                # Tripwire: a reply that cites an "earlier result" is only honest if this
                # conversation actually ran a tool. Measured failure (validation run,
                # 2026-08): a model told to reuse earlier results started PREFIXING
                # fabricated tool output with "[earlier result from X]" — invented costs,
                # invented forecasts — without calling anything. One corrective iteration,
                # once; if the model insists, the fabrication is refused outright rather
                # than delivered as truth.
                if "[earlier result" in text.lower():
                    if not fabrication_nudged:
                        fabrication_nudged = True
                        messages.append(SystemMessage(content=(
                            "STOP: '[earlier result from ...]' is a fabrication marker. If "
                            "that result truly appears earlier in this conversation, restate "
                            "its actual numbers plainly with no stage directions. If it does "
                            "not, CALL the tool now and answer only from its real output.")))
                        continue
                    return ("I almost gave you numbers without computing them — caught it. "
                            "Ask me that again in one message and I'll run the real "
                            "calculation.")
                if _PROMISE.search(text) and not promise_nudged:
                    promise_nudged = True
                    messages.append(SystemMessage(content=(
                        "You ANNOUNCED an action but called no tool in that reply, so "
                        "nothing actually happened. If the action needs a tool (logging "
                        "readings, a forecast, saving the profile, sizing, costing), CALL "
                        "the tool NOW and answer from its real output. If you were only "
                        "asking the user a question, return your question unchanged.")))
                    continue
                return text or "I'm not sure how to help with that yet."
            for call in tool_calls:
                tool = self._tools_by_name.get(call["name"])
                if tool is None:
                    result = f"TOOL_ERROR: unknown tool {call['name']!r}"
                else:
                    try:
                        result = tool.invoke(call["args"])
                    except Exception as exc:  # fed back so the model can correct; never hidden
                        result = f"TOOL_ERROR: {exc}"
                runtime.record_tool_call()
                self._analytics.record("tool_call", user_id=user_id, tool=call["name"],
                                       ok=not str(result).startswith("TOOL_ERROR"))
                self._conv.append_message(user_id, "tool", result, tool_name=call["name"])
                captured = profile.profile_updates_from_tool(call["name"], call["args"], result)
                if captured:
                    self._mem.set_facts(user_id, captured, source="tool_call")
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        # Hit the tool-call cap (e.g. the model kept calling tools without answering). Force a
        # final natural-language reply with tools disabled, so the user always gets a real answer.
        try:
            messages.append(SystemMessage(content="Now reply to the user in plain text using what "
                                                   "you have. Do not call any more tools."))
            final = self._invoke_model(self._base, messages, "final")
            text = (getattr(final, "content", "") or "").strip()
            if text:
                return text
        except Exception:
            log.debug("forced final answer failed", exc_info=True)
        return "Here's what I have so far — could you tell me a bit more so I can pin it down?"

    # --- the single public seam ------------------------------------------
    def handle_message(self, channel: str, channel_user: str, text: str,
                       display_name: str | None = None, fact_text: str | None = None) -> str:
        """`fact_text` overrides which text deterministic fact-extraction reads.

        It exists because not every turn is the user's own words. An image turn's text is
        mostly a VISION MODEL's observation, and `sanitize_observation` is a lexicon — so it
        leaks. A leaked reading used to be parsed out here and stored with source="parsed",
        indistinguishable from something the operator actually reported and replayed into
        every later turn. Image turns therefore pass their caption (the only part the user
        actually wrote); voice turns pass nothing, because a transcript IS the user's words.
        """
        if self._bound is None:
            # Refused BEFORE the trace opens, deliberately. A turn is a run through the
            # pipeline; this never reaches it, and recording it as one would drop a ~0 ms row
            # into the latency distribution and flatter the p50 of turns that really ran. The
            # event is still counted, so an operator can see a missing model rather than infer
            # it from an absence of turns.
            self._analytics.record("no_model", channel=channel)
            return ("I can't hold a conversation right now — no tool-calling model is "
                    f"configured ({self._chat_error}). Your live twin still works: "
                    "/log and /forecast never touch a model.")
        owns_turn = runtime.start_turn()
        t_turn = time.perf_counter()
        try:
            return self._handle_message_inner(channel, channel_user, text, display_name, fact_text)
        finally:
            if owns_turn:
                self._record_turn(channel, t_turn)
                runtime.end_turn()

    def _record_turn(self, channel: str, t0: float) -> None:
        """Close the turn out with its end-to-end shape: total wall clock, how much of it
        was the model, how many calls it took, and what it cost in tokens.

        Written in a `finally`, so a turn that raised is still measured. A failed turn is
        the one whose latency you most want in the distribution, and dropping it is how a
        p95 comes to look healthier than the service actually is.
        """
        m = runtime.turn_metrics()
        fields = {
            "channel": channel,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "llm_ms": m.get("llm_ms"),
            "llm_calls": m.get("llm_calls"),
            "tool_calls": m.get("tool_calls"),
        }
        if m.get("usage_seen"):     # omit entirely when the provider reported no usage
            fields["tokens_in"] = m.get("tokens_in")
            fields["tokens_out"] = m.get("tokens_out")
        self._analytics.record("turn", **fields)

    def _handle_message_inner(self, channel: str, channel_user: str, text: str,
                              display_name: str | None = None,
                              fact_text: str | None = None) -> str:
        user_id = self._conv.get_or_create_user(channel, channel_user, display_name)
        self._analytics.record("message", user_id=user_id, channel=channel)
        source_text = text if fact_text is None else fact_text
        self._mem.set_facts(user_id, memory_extract.extract_facts(source_text), source="parsed")
        self._conv.append_message(user_id, "user", text)

        # Outcome loop: a delivered follow-up is being answered now; a not-yet-sent one is
        # superseded by the user messaging first.
        capture_note = None
        open_fu = self._followups.open_for(user_id)
        if open_fu and open_fu["status"] == "sent":
            self._followups.record_outcome(open_fu["id"], text)  # audit: what they answered
            self._followups.mark_answered(open_fu["id"])
            capture_note = (
                f'You earlier asked this user: "{open_fu["question"]}". They are replying now. '
                f"If they report whether it worked, save the result with "
                f"remember_about_user(category='learning')."
            )
        elif open_fu and open_fu["status"] == "pending":
            self._followups.cancel(open_fu["id"])

        runtime.set_current(self._mem, user_id, self._followups, self._community,
                            self._calibration, self._readings, self._proposals)  # tools reach this user
        try:
            messages = self._build_context(user_id, query=text)
            if capture_note:
                messages.append(SystemMessage(content=capture_note))
            reply = self._run_tool_loop(messages, user_id)
            # Capture any files a tool produced (e.g. a schematic) BEFORE clearing context.
            atts = runtime.get_attachments()
        finally:
            runtime.clear_current()
        if atts:
            self._pending_attachments[user_id] = atts   # keyed by user; adapter drains it
        self._conv.append_message(user_id, "assistant", reply)
        self._schedule_summary(user_id)
        return reply

    def record_feedback(self, channel: str, channel_user: str, positive: bool) -> str:
        """Record a thumbs up/down on the last reply and acknowledge it.

        The course's cheapest quality signal and the only one that measures what the other
        evaluators cannot: whether the person actually asked was helped. Deliberately a bare
        rating with no comment field — a free-text box here would be the one place message
        content could enter the analytics log, and the allowlist would drop it anyway.
        """
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._analytics.record("feedback", user_id=user_id, channel=channel,
                               rating=1 if positive else -1)
        return ("Noted, thank you — that helps me get better."
                if positive else
                "Thanks for telling me. What was wrong with it? I'll use that to do better.")

    def take_attachments(self, channel: str, channel_user: str) -> list:
        """Files the last turn produced for this user, to be sent by the channel adapter.
        Draining is idempotent — returns [] once taken."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        return self._pending_attachments.pop(user_id, [])

    def handle_image(self, channel: str, channel_user: str, image_bytes: bytes,
                     caption: str | None = None, display_name: str | None = None) -> str:
        """A photo arrives: the VLM produces a plain-language visual observation, which is
        then run through the NORMAL text turn — so memory, the trust-gated tools, and cited
        knowledge all still apply. The vision model never calls tools or emits numbers."""
        owns_turn = runtime.start_turn()
        t_turn = time.perf_counter()
        try:
            return self._handle_image_inner(channel, channel_user, image_bytes, caption, display_name)
        finally:
            if owns_turn:
                self._record_turn(channel, t_turn)
                runtime.end_turn()

    def _handle_image_inner(self, channel: str, channel_user: str, image_bytes: bytes,
                            caption: str | None = None, display_name: str | None = None) -> str:
        self._analytics.record("image", user_id=self._conv.get_or_create_user(channel, channel_user),
                               channel=channel)
        if self._describe is None:
            return ("I can't look at images yet — but describe what you see (leaf colour, "
                    "fish behaviour, water look) and I'll help from there.")
        try:
            observation = (self._describe(image_bytes, caption) or "").strip()
        except Exception:
            log.warning("vision describe failed", exc_info=True)
            return ("I couldn't read that photo just now — try again, or describe what you "
                    "see and I'll help from there.")
        if not observation:
            return ("I couldn't make anything out in that photo — try a clearer, closer shot, "
                    "or describe what you see.")

        # The VLM was told to observe without diagnosing, prescribing, or stating numbers.
        # The guard enforces the enforceable part of that instruction.
        observation, flags = sanitize_observation(observation)
        for category in ("verdict", "stripped", "unclear"):
            if any(f.split(":")[0] == category for f in flags):
                # Event name, not a field: analytics._ALLOWED_FIELDS is a whitelist, and the
                # observation text itself must never be recorded.
                self._analytics.record(f"image_guard_{category}",
                                       user_id=self._conv.get_or_create_user(channel, channel_user),
                                       channel=channel)

        if "unclear" in flags or not observation:
            return ("I couldn't make anything out in that photo — try a clearer, closer shot, "
                    "or describe what you see.")

        ask = (caption or "").strip() or "What's going on here?"
        note = ("\n\n" + _VERDICT_INSTRUCTION) if any(f.startswith("verdict:") for f in flags) else ""
        classifier_features, classifier_note = self._classify_image(image_bytes)
        # Deterministic differential from the visible features. Attached rather than left to a
        # tool call so the cited candidates are ALWAYS present for a photo — the observation
        # itself is untrusted prose, but this part is auditable like the sizing path.
        composed = (f"[The user sent a photo. A vision model observed: {observation}]{note}"
                    f"{classifier_note}"
                    f"{self._visual_triage(observation, classifier_features)}\n\n{ask}")
        # Facts come from the CAPTION only — never from the model's observation. See
        # handle_message's fact_text docstring for why the guard alone is not enough.
        return self.handle_message(channel, channel_user, composed, display_name,
                                   fact_text=(caption or ""))

    def _classify_image(self, image_bytes: bytes) -> tuple[dict, str]:
        """(extra feature kwargs, a note naming what the classifier said).

        Returns ({}, "") whenever no classifier is configured — the normal case — so this is
        a no-op for every existing deployment. Best-effort: a failing classifier must never
        cost the user their answer."""
        if self._classify is None:
            return {}, ""
        try:
            from agent.classifier import describe_predictions, features_from_predictions
            predictions = self._classify(image_bytes) or []
            note = describe_predictions(predictions)
            return features_from_predictions(predictions), ("\n\n" + note if note else "")
        except Exception:
            log.debug("image classifier unavailable", exc_info=True)
            return {}, ""

    @staticmethod
    def _visual_triage(observation: str, extra_features: dict | None = None) -> str:
        """A cited differential for what the photo shows, or "" when nothing is diagnostic.

        Best-effort: triage is a convenience on top of the observation, so any failure here
        degrades to the plain observation rather than costing the user their answer."""
        try:
            from agent.observation_features import features_from
            from aqua_model.triage import format_triage, triage_symptoms
            result = triage_symptoms(features_from(observation, extra_features))
            return "" if result.is_empty() else "\n\n" + format_triage(result)
        except Exception:
            log.debug("visual triage unavailable", exc_info=True)
            return ""

    def handle_voice(self, channel: str, channel_user: str, audio_bytes: bytes,
                     mime: str | None = None, display_name: str | None = None) -> str:
        """A voice note arrives: transcribe it, then run the transcript through the NORMAL
        text turn. The transcript IS the user's message, so everything (memory, tools, cited
        knowledge, reply-in-user-language) applies unchanged."""
        owns_turn = runtime.start_turn()
        t_turn = time.perf_counter()
        try:
            return self._handle_voice_inner(channel, channel_user, audio_bytes, mime, display_name)
        finally:
            if owns_turn:
                self._record_turn(channel, t_turn)
                runtime.end_turn()

    def _handle_voice_inner(self, channel: str, channel_user: str, audio_bytes: bytes,
                            mime: str | None = None, display_name: str | None = None) -> str:
        self._analytics.record("voice", user_id=self._conv.get_or_create_user(channel, channel_user),
                               channel=channel)
        if self._transcribe is None:
            return ("I can't listen to voice notes yet — type your message and I'll help "
                    "right away.")
        try:
            transcript = (self._transcribe(audio_bytes, mime) or "").strip()
        except Exception:
            log.warning("voice transcription failed", exc_info=True)
            return ("I couldn't make out that voice note — try again, or type it and I'll "
                    "help from there.")
        if not transcript:
            return "I didn't catch anything in that voice note — try again, or type it out?"
        return self.handle_message(channel, channel_user, transcript, display_name)

    def profile_text(self, channel: str, channel_user: str) -> str:
        """Human-readable view of what the agent remembers — backs the /whoami command."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        block = self._recall_block(user_id)
        return block or "I don't know anything about your system yet. Tell me about it!"

    def _run_tool_direct(self, channel: str, channel_user: str, tool_name: str,
                         args: dict) -> str:
        """Run one tool deterministically for a user, NO LLM in the path — backs the
        /log and /forecast commands. The point is a guarantee: free-tier LLM weather
        (congestion, leaked tool calls, narration instead of action — all measured) must
        never stand between an operator and their own live twin. The result is recorded
        in the conversation like any tool turn, so the LLM sees it as context later."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        tool = {t.name: t for t in AGRONAUT_TOOLS}[tool_name]
        runtime.set_current(self._mem, user_id, self._followups, self._community,
                            self._calibration, self._readings, self._proposals)
        try:
            result = tool.invoke(args)
        except Exception as exc:  # noqa: BLE001 — a command must answer, not stack-trace
            result = f"That didn't work: {exc}"
        finally:
            runtime.clear_current()
        self._conv.append_message(user_id, "tool", result, tool_name=tool_name)
        self._analytics.record("tool_call", user_id=user_id, tool=tool_name,
                               ok=not str(result).startswith("TOOL_ERROR"))
        return result

    def log_readings_direct(self, channel: str, channel_user: str, args: dict) -> str:
        """Deterministic /log: readings straight into the live twin."""
        return self._run_tool_direct(channel, channel_user, "log_my_readings", args)

    def forecast_direct(self, channel: str, channel_user: str, days: int = 7,
                        greenhouse: str = "poly") -> str:
        """Deterministic /forecast: the live twin, advanced and projected."""
        return self._run_tool_direct(channel, channel_user, "my_system_forecast",
                                     {"days_ahead": days, "greenhouse": greenhouse})

    def advise_direct(self, channel: str, channel_user: str, days: int = 7,
                      greenhouse: str = "poly") -> str:
        """Deterministic /advise: the twin's proposal, no LLM between the state and the
        recommendation. Same guarantee /log and /forecast earned, and it matters more here:
        a model that paraphrases a proposal can drop the confidence class, the source or the
        measure-first gate, and every one of those is load-bearing."""
        return self._run_tool_direct(channel, channel_user, "recommend_actions",
                                     {"days_ahead": days, "greenhouse": greenhouse})

    def decide_direct(self, channel: str, channel_user: str, approve: bool,
                      numbers: list) -> str:
        """Deterministic /approve and /reject. An approval must NEVER pass through a model:
        the whole value of the gate is that what got recorded is what the human typed."""
        return self._run_tool_direct(channel, channel_user, "decide_on_recommendations",
                                     {"approve": bool(approve), "numbers": list(numbers)})

    @property
    def chat_error(self) -> str | None:
        """Why chat is unavailable, or None when a tool-calling model is bound. The
        deterministic twin surfaces work either way."""
        return self._chat_error

    def twin_snapshot(self, channel: str, channel_user: str, days: int = 7,
                      greenhouse: str = "poly"):
        """The live twin as STRUCTURE, for a dashboard — no LLM, no prose.

        Backs the same computation `/forecast` renders as words, so the two surfaces can
        never disagree about the pond."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        return twin_view.compute(self._mem, user_id, days=days, greenhouse=greenhouse,
                                 readings=self._readings)

    def reset(self, channel: str, channel_user: str) -> None:
        """Clear the conversation thread. Long-term memory (facts/memories) is kept."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._conv.reset_conversation(user_id)

    def forget_everything(self, channel: str, channel_user: str) -> None:
        """Wipe conversation AND long-term memory for this user (the /forget command)."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._conv.reset_conversation(user_id)
        self._mem.forget(user_id)

    # --- data rights (DPG indicators 6 & do-no-harm) ------------------------
    def export_user_data(self, channel: str, channel_user: str) -> dict:
        """Everything Agronaut holds about this user, as a portable JSON-serializable dict
        (the DPG non-proprietary-export requirement). Scoped strictly to this user."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        return {
            "identity": {"user_id": user_id, "channel": channel, "channel_user": str(channel_user)},
            "profile": self._mem.get_facts(user_id),
            "memories": self._mem.all_memories(user_id),
            "summary": self._mem.get_summary(user_id),
            "messages": self._conv.recent_messages(user_id, limit=100000),
            "measurements": self._calibration.export(user_id),
            "twin_readings": self._readings.history(user_id),
            "approved_actions": self._proposals.approved_history(user_id, limit=100000),
            "exported_at": _now(),
        }

    def delete_me(self, channel: str, channel_user: str) -> None:
        """Erase ALL of this user's data — conversation, profile, memories, summary, and
        calibration measurements. The chat-reachable right-to-erasure."""
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._conv.reset_conversation(user_id)
        self._mem.forget(user_id)
        self._calibration.purge(user_id)
        self._readings.purge(user_id)
        self._proposals.purge(user_id)

    # --- follow-up delivery API (called by a channel poller) ----------------
    def due_followups(self, channel: str) -> list:
        """Follow-ups due for delivery on `channel` right now."""
        return self._followups.due(channel, _now())

    def mark_followup_sent(self, followup_id: int) -> None:
        self._followups.mark_sent(followup_id)

    def followup_send_failed(self, followup_id: int) -> None:
        """A delivery attempt failed; retry next tick, but give up after 3."""
        if self._followups.bump_attempt(followup_id) >= 3:
            self._followups.mark_failed(followup_id)

    def set_goal(self, channel: str, channel_user: str, goal: str) -> str:
        """Explicitly set the consultation goal (backs the /design, /optimize, /troubleshoot
        commands). Persists profile['goal'] and returns the user-facing confirmation. Does
        NOT touch conversation history or other facts. Raises ValueError on an unknown goal."""
        g = (goal or "").strip().lower()
        if g not in profile.GOALS:
            raise ValueError(f"unknown goal {goal!r}")
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._mem.set_fact(user_id, "goal", g, source="user_stated")
        self._analytics.record("goal_set", user_id=user_id, goal=g)
        facts = self._mem.get_facts(user_id)
        return f"{profile.GOAL_HEADERS[g]}. {profile.essentials_hint(g, facts)}"

    # --- background cross-session summary (no user-facing latency) --------
    def _schedule_summary(self, user_id: str, every: int = 12) -> None:
        """Refresh the rolling summary in a daemon thread once history is long enough."""
        msgs = self._conv.recent_messages(user_id, limit=200)
        if len(msgs) < every:
            return
        threading.Thread(target=self._refresh_summary, args=(user_id, msgs), daemon=True).start()

    def _refresh_summary(self, user_id: str, msgs: list) -> None:
        try:
            transcript = "\n".join(
                f"{m['role']}: {m['content']}" for m in msgs if m["role"] in ("user", "assistant")
            )[:6000]
            prompt = (
                "Summarise this user's aquaponics system and the key points of the conversation "
                "in 2-4 sentences, for your own future recall. Focus on durable facts, decisions, "
                "and open problems — not pleasantries.\n\n" + transcript
            )
            summary = get_llm(temperature=0.0).invoke(prompt).strip()
            if summary:
                self._mem.set_summary(user_id, summary)
        except Exception:  # background best-effort; never affect the live turn
            log.debug("summary refresh failed", exc_info=True)


def _repl() -> None:
    """Local dry-run: talk to the agent from the terminal, no Telegram. Needs a configured
    tool-calling provider (e.g. LLM_PROVIDER=nvidia NVIDIA_API_KEY=...)."""

    from .channels.repl import ReplChannel
    ReplChannel(AgronautAgent()).run()


if __name__ == "__main__":
    _repl()
