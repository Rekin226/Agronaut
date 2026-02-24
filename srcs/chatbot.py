"""Aquaponics diagnostic chatbot + RAG (FAISS) - v7.3.1 patch

Improvements in v7.3.1 patch
------------------
- Layered decision modes: ASK_MORE / PARTIAL_PLAN / FINAL_PLAN
- Deterministic coverage scoring (less "fake completion")
- Safer fish species parser (prevents garbage values from polluting known_facts)
- Ask branch now prints immediate actions + follow-up invitation
- Structured final answer prompt (causes / actions / monitoring / escalation)
- VSCode-friendly formatting (Black-compatible style)
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import requests_cache
from langchain.prompts import ChatPromptTemplate
from langchain_ollama.llms import OllamaLLM

# --- RAG deps (FAISS) ---
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import WebBaseLoader, TextLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


# =============================================================================
# Configuration
# =============================================================================

URL_FILE = "urls.txt"
KNOWLEDGE_DIR = "knowledge"
WEB_LOAD_TIMEOUT = 20


CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"
TOP_K = 3

CACHE_NAME = "web_cache"
CACHE_EXPIRE = 86_400  # 1 day

LOG_LEVEL = logging.INFO


# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(level=LOG_LEVEL, format="[%(levelname)s] %(message)s")


# =============================================================================
# LLM
# =============================================================================

llm = OllamaLLM(model="llama3", temperature=0)


# =============================================================================
# RAG helpers (URLs -> docs -> FAISS)
# =============================================================================



def parse_urls_file(file_path: str) -> List[Dict[str, str]]:
    """
    Supports both formats:

    New format (recommended):
      CATEGORY|URL
      CATEGORY|URL|LABEL

    Legacy format (backward compatible):
      URL

    Returns list of dicts:
      {"category": "...", "url": "...", "label": "..."}
    """
    p = pathlib.Path(file_path)
    if not p.exists():
        logging.error("URL file %s does not exist", file_path)
        return []

    entries: List[Dict[str, str]] = []
    seen = set()

    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        category = "UNCATEGORIZED"
        label = ""

        if "|" in line:
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 2 and re.match(r"^https?://", parts[1], re.IGNORECASE):
                category = parts[0] or "UNCATEGORIZED"
                url = parts[1]
                if len(parts) >= 3:
                    label = parts[2]
            else:
                # Fallback to legacy if the split is malformed
                url = line
        else:
            url = line

        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue

        key = (category.lower(), url.lower())
        if key in seen:
            continue
        seen.add(key)

        entries.append({"category": category, "url": url, "label": label})

    return entries


def load_urls_from_file(file_path: str) -> List[str]:
    # Backward-compatible helper used by old code paths
    return [e["url"] for e in parse_urls_file(file_path)]


def load_local_knowledge_documents(knowledge_dir: str = KNOWLEDGE_DIR):
    docs = []
    base = pathlib.Path(knowledge_dir)
    if not base.exists():
        return docs

    for fp in base.rglob("*"):
        if fp.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            loader = TextLoader(str(fp), encoding="utf-8")
            loaded = loader.load()
            for d in loaded:
                d.metadata["source_type"] = "local_file"
                d.metadata["source_path"] = str(fp)
                d.metadata["kb_tag"] = fp.stem.lower()
            docs.extend(loaded)
        except Exception as err:  # noqa: BLE001
            logging.warning("Failed to load local file %s – %s", fp, err)

    if docs:
        logging.info("Loaded %d local knowledge docs", len(docs))
    return docs


def load_web_page(url: str):

    try:
        logging.info("Loading %s", url)
        loader = WebBaseLoader(url)
        try:
            loader.requests_per_second = 2
        except Exception:
            pass
        try:
            if hasattr(loader, 'requests_kwargs') and isinstance(loader.requests_kwargs, dict):
                loader.requests_kwargs.setdefault('timeout', WEB_LOAD_TIMEOUT)
            else:
                loader.requests_kwargs = {'timeout': WEB_LOAD_TIMEOUT}
        except Exception:
            pass
        return loader.load()
    except Exception as err:  # noqa: BLE001
        logging.warning("Failed to load %s – %s", url, err)
        return []


def build_vector_store(documents) -> FAISS:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    docs = splitter.split_documents(documents)

    boilerplate_kw = (
        "privacy policy",
        "cookie",
        "terms",
        "accessibility",
        "legal notice",
        "help and support",
        "contact us",
        "subscribe",
        "sign in",
        "log in",
        "all rights reserved",
    )

    def looks_like_boilerplate(text: str) -> bool:
        t = (text or "").lower()
        if not t or len(t) < 200:
            return True
        if any(k in t for k in boilerplate_kw):
            return True
        return False

    filtered = [d for d in docs if not looks_like_boilerplate(getattr(d, "page_content", ""))]
    if filtered:
        docs = filtered

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    logging.info("Creating FAISS index with %d chunks …", len(docs))
    return FAISS.from_documents(docs, embeddings)


_BOILERPLATE_KEYWORDS = {
    "privacy policy",
    "cookie",
    "cookies",
    "terms of use",
    "terms and conditions",
    "accessibility",
    "legal notice",
    "all rights reserved",
    "help and support",
    "contact us",
    "subscribe",
    "sign in",
    "log in",
}


def _is_boilerplate_text(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return True
    if len(t) < 200:
        return True
    if any(k in t for k in _BOILERPLATE_KEYWORDS):
        return True
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if lines:
        short = sum(1 for ln in lines if len(ln) <= 40)
        if short / max(len(lines), 1) > 0.75 and len(t) < 2500:
            return True
    return False


def retrieve_context(vs: Optional[FAISS], question: str, k: int = TOP_K) -> str:
    if vs is None:
        return ""

    try:
        pairs = vs.similarity_search_with_score(question, k=max(k * 2, k))
        docs = [doc for (doc, _score) in pairs if not _is_boilerplate_text(doc.page_content)]
        docs = docs[:k]
        return "\n\n".join(doc.page_content for doc in docs) if docs else ""
    except Exception:
        try:
            retrieved = vs.similarity_search(question, k=max(k * 2, k))
            retrieved = [d for d in retrieved if not _is_boilerplate_text(d.page_content)]
            retrieved = retrieved[:k]
            return "\n\n".join(doc.page_content for doc in retrieved) if retrieved else ""
        except Exception as err:  # noqa: BLE001
            logging.warning("Retrieval failed – %s", err)
            return ""


# =============================================================================
# STATE
# =============================================================================


class ThreadState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.mode = "DIAGNOSIS"
        self.problem_id = str(uuid4())
        self.known_facts: Dict[str, Any] = {}
        self.asked_questions: List[str] = []
        self.pending_questions: List[str] = []
        self.confidence = 0.0
        self.last_answer = ""
        self.last_agent_message = ""
        self.last_subquestion = ""
        self.last_topic = ""


state = ThreadState()


# =============================================================================
# JSON PARSER
# =============================================================================


def extract_json(text: str):
    m = re.search(r"({.*})", text, re.DOTALL)
    if not m:
        raise ValueError("NO JSON:\n" + text)
    blob = m.group(1)
    blob = re.sub(r",\s*([\]}])", r"\1", blob)
    return json.loads(blob)


# =============================================================================
# INTENT
# =============================================================================


intent_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "Return ONLY: FOLLOWUP | NEW_TOPIC"),
        ("human", "Last:{last}\nUser:{user}"),
    ]
)


def classify_intent(last_bot: str, user: str) -> str:
    return llm.invoke(intent_prompt.format_messages(last=last_bot, user=user)).strip()


# =============================================================================
# Decision / reasoning prompts
# =============================================================================


decision_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are an aquaponics assistant that supports two workflows:
(A) DESIGN: designing a new aquaponics system.
(B) TROUBLESHOOT: diagnosing an existing system issue.

Determine which workflow applies from the latest USER message and KNOWN_FACTS.

Use <context> only as supporting evidence when relevant.
If <context> looks like website navigation/legal text (privacy policy, cookies, terms, accessibility, login), IGNORE it.

Return ONLY a valid JSON object with exactly these keys:
- problem_type: "DESIGN" or "TROUBLESHOOT"
- mode: "ASK_MORE" or "PARTIAL_PLAN" or "FINAL_PLAN"
- severity: "LOW" or "MEDIUM" or "HIGH" or "UNKNOWN"
- confidence: number 0.0-1.0
- known_facts_update: object
- missing_info: list of strings
- next_questions: list of strings (0-4 items)
- immediate_actions: list of strings (safe first steps; can be empty)
- plan_steps: list of strings (can be empty)
- explanations: list of strings (can be empty)
- followup_offer: string
- stop_reason: string

Rules:
1) Never repeat any question in ASKED_QUESTIONS.
2) Never list a missing field that already exists in KNOWN_FACTS.
3) ASK_MORE = insufficient info. Ask 1-4 high-value questions.
4) PARTIAL_PLAN = some info available. Provide safe immediate_actions + 1-4 questions.
5) FINAL_PLAN = enough info. Provide plan_steps and explanations. next_questions can be empty.
6) TROUBLESHOOT questions should prioritize: temperature, pH, ammonia/nitrite/nitrate, aeration/DO, flow rate, recent changes, timeline, mortality.
7) DESIGN questions should prioritize: goal, scale/footprint, budget, system type, fish/plants, filtration, aeration, water volume, sunlight, monitoring.
8) If fish are gasping/dying, include at least one immediate action related to aeration / feeding reduction / checking flow.
9) Keep all outputs concise and practical.
""".strip(),
        ),
        (
            "human",
            """
<context>
{context}

ASKED_QUESTIONS:
{asked}

KNOWN_FACTS:
{facts}

COVERAGE_HINT:
{coverage_hint}

USER:
{user}
""".strip(),
        ),
    ]
)


complex_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
Use KNOWN_FACTS and <context> to produce a practical aquaponics response.

If <context> looks like website navigation/legal text, ignore it.

Structure your answer with these sections:
1) Most likely causes (ranked)
2) Immediate actions (safe steps first)
3) Step-by-step plan
4) What to monitor in the next 24 hours
5) When to escalate / urgent warning signs
6) Follow-up question (invite user to continue)

Be specific, practical, and avoid generic advice.
""".strip(),
        ),
        (
            "human",
            """
<context>
{context}

FACTS:
{facts}

USER:
{user}
""".strip(),
        ),
    ]
)


# =============================================================================
# Deterministic scenario + coverage logic (v7)
# =============================================================================


FIELD_ALIASES = {
    "temperature_c_or_range": ("temperature_c", "temperature_range_c"),
    "aeration": ("aeration", "do", "dissolved_oxygen"),
    "recent_changes": ("recent_changes", "water_change", "added_chemicals", "added_fish"),
    "flow_rate": ("flow_rate", "circulation", "pump_flow"),
    "plant_symptom": ("plant_symptom", "leaf_symptom", "yellow_leaves"),
    "iron_signs": ("iron_signs", "iron_deficiency_signs"),
    "system_type": ("system_type", "grow_bed_type"),
    "goal": ("goal", "primary_goal"),
    "system_size": ("system_size", "footprint", "scale"),
    "budget": ("budget",),
    "fish_type": ("fish_type", "fish_species"),
    "plants": ("plants", "crop_type"),
    "aeration_filtration": ("aeration", "filtration", "biofiltration"),
}

REQUIRED_FIELDS: Dict[str, List[str]] = {
    "fish_gasping": [
        "general_behavior",
        "temperature_c_or_range",
        "pH",
        "aeration",
        "recent_changes",
    ],
    "fish_not_eating": [
        "general_behavior",
        "temperature_c_or_range",
        "pH",
        "recent_changes",
        "fish_type",
    ],
    "yellow_leaves": [
        "plant_symptom",
        "pH",
        "nitrate",
        "iron_signs",
        "flow_rate",
    ],
    "design_beginner": [
        "goal",
        "system_size",
        "budget",
        "fish_type",
        "plants",
        "system_type",
        "aeration_filtration",
    ],
    "generic_troubleshoot": [
        "general_behavior",
        "temperature_c_or_range",
        "pH",
        "recent_changes",
        "flow_rate",
    ],
}


def classify_problem_type(user: str, known_facts: Dict[str, Any]) -> str:
    txt = f"{user} {json.dumps(known_facts, ensure_ascii=False)}".lower()
    design_kw = (
        "design",
        "from scratch",
        "build",
        "setup",
        "components",
        "nft",
        "dwc",
        "media bed",
        "system size",
        "budget",
    )
    if any(k in txt for k in design_kw):
        return "DESIGN"
    return "TROUBLESHOOT"


def classify_scenario(user: str, known_facts: Dict[str, Any]) -> str:
    txt = f"{user} {json.dumps(known_facts, ensure_ascii=False)}".lower()
    if any(k in txt for k in ("gasp", "surface", "floating at top", "oxygen")):
        return "fish_gasping"
    if any(k in txt for k in ("yellow leaf", "yellow leaves", "chlorosis", "leaf yellow")):
        return "yellow_leaves"
    if any(k in txt for k in ("not eating", "stopped eating", "loss of appetite")):
        return "fish_not_eating"
    if classify_problem_type(user, known_facts) == "DESIGN":
        return "design_beginner"
    return "generic_troubleshoot"


def _has_field(known_facts: Dict[str, Any], logical_field: str) -> bool:
    aliases = FIELD_ALIASES.get(logical_field, (logical_field,))
    for key in aliases:
        if key in known_facts and known_facts[key] not in (None, "", []):
            return True
    return False


def compute_coverage(known_facts: Dict[str, Any], required_fields: List[str]) -> Tuple[float, List[str]]:
    missing = [field for field in required_fields if not _has_field(known_facts, field)]
    total = max(len(required_fields), 1)
    coverage = (total - len(missing)) / total
    return coverage, missing


def compute_confidence_from_coverage(coverage: float, problem_type: str) -> float:
    # Conservative confidence to avoid over-answering with sparse data.
    if problem_type == "TROUBLESHOOT":
        if coverage < 0.4:
            return 0.25
        if coverage < 0.7:
            return 0.55
        return 0.8
    if coverage < 0.4:
        return 0.3
    if coverage < 0.7:
        return 0.6
    return 0.82


def choose_mode_from_coverage(coverage: float) -> str:
    if coverage < 0.4:
        return "ASK_MORE"
    if coverage < 0.75:
        return "PARTIAL_PLAN"
    return "FINAL_PLAN"


def coverage_hint_payload(user: str) -> Dict[str, Any]:
    problem_type = classify_problem_type(user, state.known_facts)
    scenario = classify_scenario(user, state.known_facts)
    required = REQUIRED_FIELDS.get(scenario, REQUIRED_FIELDS["generic_troubleshoot"])
    coverage, missing = compute_coverage(state.known_facts, required)
    confidence = compute_confidence_from_coverage(coverage, problem_type)
    mode = choose_mode_from_coverage(coverage)
    return {
        "problem_type_guess": problem_type,
        "scenario_guess": scenario,
        "required_fields": required,
        "coverage": round(coverage, 3),
        "missing_fields": missing,
        "recommended_mode": mode,
        "recommended_confidence": confidence,
    }


# =============================================================================
# Chat controller functions
# =============================================================================


VECTORSTORE: Optional[FAISS] = None


def decision_model(user: str) -> Dict[str, Any]:
    context = retrieve_context(VECTORSTORE, user)
    coverage_hint = coverage_hint_payload(user)

    msgs = decision_prompt.format_messages(
        context=context,
        asked=json.dumps(state.pending_questions or state.asked_questions, indent=2),
        facts=json.dumps(state.known_facts, indent=2),
        coverage_hint=json.dumps(coverage_hint, indent=2),
        user=user,
    )

    raw = llm.invoke(msgs)
    print("\n[RAW MODEL OUTPUT]\n", raw)
    try:
        data = extract_json(raw)
    except Exception as err:  # noqa: BLE001
        logging.warning("Model returned invalid JSON (%s). Retrying once…", err)
        retry_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Return ONLY valid JSON. No extra text. No trailing commas.",
                ),
                ("human", "{raw}"),
            ]
        )
        repair = llm.invoke(retry_prompt.format_messages(raw=raw))
        print("\n[RAW MODEL OUTPUT - RETRY]\n", repair)
        data = extract_json(repair)

    # Normalize/sanitize expected keys (prevents JSON shape issues)
    defaults: Dict[str, Any] = {
        "problem_type": coverage_hint["problem_type_guess"],
        "mode": coverage_hint["recommended_mode"],
        "severity": "UNKNOWN",
        "confidence": coverage_hint["recommended_confidence"],
        "known_facts_update": {},
        "missing_info": coverage_hint["missing_fields"],
        "next_questions": [],
        "immediate_actions": [],
        "plan_steps": [],
        "explanations": [],
        "followup_offer": "Do you want me to continue step by step?",
        "stop_reason": "",
    }
    for key, val in defaults.items():
        data.setdefault(key, val)

    data = sanitize_decision_payload(data, defaults)

    # Merge model-updated facts
    state.known_facts.update(data.get("known_facts_update", {}))

    # Deterministic override from coverage (prevents premature FINAL_PLAN)
    refreshed_hint = coverage_hint_payload(user)
    state.confidence = float(refreshed_hint["recommended_confidence"])
    data["confidence"] = round(state.confidence, 2)

    if data.get("mode") == "FINAL_PLAN" and refreshed_hint["coverage"] < 0.75:
        data["mode"] = refreshed_hint["recommended_mode"]
        data["stop_reason"] = "Coverage too low for FINAL_PLAN; downgraded by deterministic check."

    # Ensure missing_info aligns with deterministic missing fields (union, de-dup)
    merged_missing = list(dict.fromkeys(refreshed_hint["missing_fields"] + data["missing_info"]))
    data["missing_info"] = [m for m in merged_missing if not _has_field(state.known_facts, m)]

    # Clamp question count and de-dup
    data["next_questions"] = dedupe(data.get("next_questions", []))[:4]
    data = filter_questions_by_known_facts(data)

    # Ensure PARTIAL_PLAN has at least one immediate action in troubleshoot cases
    if (
        data.get("mode") in {"ASK_MORE", "PARTIAL_PLAN"}
        and refreshed_hint["problem_type_guess"] == "TROUBLESHOOT"
        and not data["immediate_actions"]
    ):
        data["immediate_actions"] = default_immediate_actions(user, state.known_facts)

    # Always end with an invitation
    if not data.get("followup_offer"):
        data["followup_offer"] = default_followup_offer(data["mode"], refreshed_hint["problem_type_guess"])

    return data


def complex_reason(user: str) -> str:
    context = retrieve_context(VECTORSTORE, user)
    return llm.invoke(
        complex_prompt.format_messages(
            context=context,
            facts=json.dumps(state.known_facts, indent=2),
            user=user,
        )
    )


def refine_answer(user: str) -> str:
    context = retrieve_context(VECTORSTORE, user)
    return llm.invoke(
        f"""
Refine the prior aquaponics diagnosis/plan using the new user message.
Keep it practical and step-by-step.

<context>
{context}

FACTS:
{json.dumps(state.known_facts, indent=2)}

PREVIOUS:
{state.last_answer}

NEW:
{user}
"""
    )


def dedupe(new_qs: List[str]) -> List[str]:
    clean: List[str] = []
    seen = set()
    for q in new_qs:
        if not isinstance(q, str):
            continue
        qn = q.lower().strip()
        if not qn:
            continue
        if qn in seen:
            continue
        if any(qn in old.lower() or old.lower() in qn for old in state.asked_questions):
            continue
        seen.add(qn)
        clean.append(q.strip())
    return clean




def sanitize_decision_payload(data: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = {
        "problem_type": "TROUBLESHOOT",
        "mode": "ASK_MORE",
        "severity": "UNKNOWN",
        "confidence": 0.3,
        "known_facts_update": {},
        "missing_info": [],
        "next_questions": [],
        "immediate_actions": [],
        "plan_steps": [],
        "explanations": [],
        "followup_offer": "",
        "stop_reason": "",
    }
    if fallback:
        defaults.update({k: v for k, v in fallback.items() if k in defaults})
    out = dict(defaults)
    out.update(data or {})

    if not isinstance(out.get("known_facts_update"), dict):
        out["known_facts_update"] = {}

    for key in ["missing_info", "next_questions", "immediate_actions", "plan_steps", "explanations"]:
        val = out.get(key)
        if isinstance(val, str):
            out[key] = [val.strip()] if val.strip() else []
        elif not isinstance(val, list):
            out[key] = []

    cleaned_actions = []
    for item in out["immediate_actions"]:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s:
            continue
        if s.lower().startswith('"immediate_actions"'):
            continue
        cleaned_actions.append(s)
    out["immediate_actions"] = list(dict.fromkeys(cleaned_actions))

    try:
        out["confidence"] = float(out.get("confidence", 0.3))
    except Exception:  # noqa: BLE001
        out["confidence"] = 0.3
    out["confidence"] = max(0.0, min(1.0, out["confidence"]))

    if out.get("mode") not in {"ASK_MORE", "PARTIAL_PLAN", "FINAL_PLAN"}:
        out["mode"] = "ASK_MORE"

    return out


def filter_questions_by_known_facts(data: Dict[str, Any]) -> Dict[str, Any]:
    known = state.known_facts
    filtered_qs: List[str] = []

    for q in data.get("next_questions", []):
        slot = _question_slot_name(q)
        if slot == "temperature" and ("temperature_c" in known or "temperature_range_c" in known):
            continue
        if slot == "ph" and "pH" in known:
            continue
        if slot == "fish_species" and ("fish_species" in known or "fish_type" in known):
            continue
        if slot == "general_behavior" and "general_behavior" in known:
            continue
        if slot == "aeration" and "aeration" in known:
            continue
        if slot == "flow_rate" and "flow_rate" in known:
            continue
        if slot == "recent_changes" and "recent_changes" in known:
            continue
        filtered_qs.append(q)

    data["next_questions"] = dedupe(filtered_qs)
    return data


def detect_inline_subquestion(user: str) -> Dict[str, Any]:
    text = (user or "").strip()
    low = text.lower()

    question_mark = "?" in text or "？" in text
    ask_phrases = [
        "can you recommend",
        "recommend",
        "which one",
        "what should i",
        "how to",
        "what pump",
        "which pump",
        "can i",
        "is it okay to",
        "what do you suggest",
        "should i",
    ]
    has_ask_phrase = any(p in low for p in ask_phrases)
    if not (question_mark or has_ask_phrase):
        return {"has_subquestion": False, "topic": "", "question_text": ""}

    if "pump" in low or "flow rate" in low or "circulation" in low:
        topic = "pump_flow"
    elif "ph" in low:
        topic = "ph"
    elif any(k in low for k in ["yellow leaf", "yellow leaves", "chlorosis", "iron"]):
        topic = "plant_deficiency"
    elif any(k in low for k in ["fish", "slow", "not eating", "gasping"]):
        topic = "fish_behavior"
    else:
        topic = "general"

    m = re.search(
        r"([A-Za-z0-9 ,'\-()]+(?:can you recommend|recommend|which one|how to|what should i|should i)[^?.!？]*[?.!？])",
        text,
        flags=re.IGNORECASE,
    )
    qtext = m.group(1).strip() if m else text

    return {"has_subquestion": True, "topic": topic, "question_text": qtext}


def answer_inline_subquestion(user: str, detected: Dict[str, Any]) -> str:
    topic = detected.get("topic", "general")
    qtext = detected.get("question_text", user)

    retrieval_query_parts = [qtext]
    if topic == "pump_flow":
        retrieval_query_parts.append("aquaponics pump sizing flow rate turnover head height rated flow actual flow")
    if "fish_species" in state.known_facts:
        retrieval_query_parts.append(f"fish species {state.known_facts['fish_species']}")
    if "temperature_range_c" in state.known_facts:
        retrieval_query_parts.append(f"temperature {state.known_facts['temperature_range_c']}")

    retrieval_query = " | ".join(retrieval_query_parts)
    context = retrieve_context(VECTORSTORE, retrieval_query, k=3) if "VECTORSTORE" in globals() else ""

    if topic == "pump_flow":
        fallback = (
            "Yes — I can recommend how to choose a better pump.\n\n"
            "- Pick based on **actual flow at your head height** (not the zero-head label only).\n"
            "- Add about **20–50% extra capacity** for pipe losses, clogging, and future expansion.\n"
            "- Check pump curve, head height, and pipe diameter before buying.\n"
            "- Do a bucket test to estimate your current real flow rate.\n\n"
            "To recommend more precisely, tell me: tank/system volume, head height, and grow-bed type."
        )
    else:
        fallback = "I can help with that. I need a bit more detail, but I can give a practical recommendation framework."

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "You are an aquaponics assistant. Answer the user's inline sub-question directly and practically. "
                    "Keep it concise and actionable. If exact specs are missing, give a recommendation framework or range. "
                    "Then add one short line saying we can continue diagnosis."
                ),
            ),
            (
                "human",
                "KNOWN_FACTS:\n{facts}\n\nINLINE_SUBQUESTION:\n{q}\n\n<context>\n{context}\n",
            ),
        ]
    )
    try:
        ans = llm.invoke(
            prompt.format_messages(
                facts=json.dumps(state.known_facts, ensure_ascii=False, indent=2),
                q=qtext,
                context=context,
            )
        ).strip()
        return ans
    except Exception as err:  # noqa: BLE001
        logging.warning("Inline sub-question answer failed: %s", err)
        return fallback


def detect_top_level_recommendation_intent(user: str) -> Dict[str, Any]:
    text = (user or "").strip()
    low = text.lower()
    recommend_phrases = [
        "recommend",
        "which pump",
        "what pump",
        "pump size",
        "size a pump",
        "choose a pump",
        "upgrade my pump",
        "upgrade the pump",
        "upgrade my water pump",
        "water pump upgrade",
        "pump recommendation",
        "how to choose pump",
        "how do i choose a pump",
        "how do i size a pump",
        "suggest a pump",
        "what pump spec",
        "buy a pump",
        "pump model",
    ]
    symptom_phrases = [
        "fish gasping",
        "fish dying",
        "fish move slow",
        "moving slow",
        "not eating",
        "yellow leaves",
        "brown roots",
        "ammonia",
        "nitrite",
        "nitrate",
        "ph problem",
        "cloudy water",
        "disease",
        "parasite",
    ]
    has_reco = any(p in low for p in recommend_phrases)
    has_symptom = any(p in low for p in symptom_phrases)

    if has_reco and ("pump" in low or "flow" in low or "circulation" in low):
        return {"is_recommendation": True, "topic": "pump_flow"}
    if has_reco and not has_symptom:
        return {"is_recommendation": True, "topic": "general"}
    return {"is_recommendation": False, "topic": ""}


def answer_top_level_recommendation(user: str, topic: str) -> str:
    retrieval_query = user
    if topic == "pump_flow":
        retrieval_query = (
            f"{user} | aquaponics pump sizing flow rate turnover head height "
            f"rated flow vs actual flow pipe loss bucket test"
        )
    context = retrieve_context(VECTORSTORE, retrieval_query, k=4) if "VECTORSTORE" in globals() else ""

    system_prompt = (
        "You are an aquaponics assistant. The user is asking for a recommendation/selection (not only troubleshooting). "
        "Answer directly first with a practical framework. If exact specs are missing, provide safe rule-of-thumb guidance "
        "and explain what to measure next. Keep it concise but useful."
    )

    if topic == "pump_flow":
        human_prompt = (
            "USER REQUEST:\n{user}\n\nKNOWN_FACTS:\n{facts}\n\n<context>\n{context}\n\n"
            "Please answer in this structure:\n"
            "1) Pump upgrade recommendation framework (practical)\n"
            "2) What to check before buying (head height, pipe losses, actual flow)\n"
            "3) Safe rule-of-thumb if exact numbers are missing\n"
            "4) Ask ONLY 3 key questions to give a precise recommendation\n"
        )
    else:
        human_prompt = (
            "USER REQUEST:\n{user}\n\nKNOWN_FACTS:\n{facts}\n\n<context>\n{context}\n\n"
            "Answer directly, then ask up to 3 key follow-up questions."
        )

    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", human_prompt)])

    try:
        return llm.invoke(
            prompt.format_messages(
                user=user,
                facts=json.dumps(state.known_facts, ensure_ascii=False, indent=2),
                context=context,
            )
        ).strip()
    except Exception as err:  # noqa: BLE001
        logging.warning("Top-level recommendation answer failed: %s", err)
        if topic == "pump_flow":
            return (
                "Yes — I can help you choose an upgrade pump.\n\n"
                "A good aquaponics pump is selected by **actual flow at your head height** "
                "(not just the label flow at zero head). As a starting point, choose a pump "
                "that can still deliver your target circulation with about **20–50% margin** "
                "for pipe losses and future expansion.\n\n"
                "Before buying, please tell me:\n"
                "1. Tank/system volume (L or gallons)\n"
                "2. Vertical head height (pump to highest outlet)\n"
                "3. Grow bed type (DWC / NFT / media bed) and how many beds\n"
            )
        return "I can recommend options, but I need a few details first."
def default_immediate_actions(user: str, known_facts: Dict[str, Any]) -> List[str]:
    txt = f"{user} {json.dumps(known_facts, ensure_ascii=False)}".lower()
    actions: List[str] = []

    if any(k in txt for k in ("gasp", "surface", "dying", "not eating", "stress")):
        actions.extend(
            [
                "Increase aeration immediately (add air stone / increase airflow / improve surface agitation).",
                "Reduce or stop feeding for the next 12-24 hours while checking water quality.",
                "Check pump flow and any clogs to ensure circulation is not restricted.",
            ]
        )

    if any(k in txt for k in ("yellow", "leaf", "plant")):
        actions.extend(
            [
                "Check pH first before adding supplements (nutrient lockout can mimic deficiency).",
                "Inspect flow distribution to grow beds / channels for weak spots or clogging.",
            ]
        )

    if not actions:
        actions.append("Share key water-quality values (temperature, pH, ammonia/nitrite/nitrate) so I can give a more precise plan.")

    return actions[:4]


def default_followup_offer(mode: str, problem_type: str) -> str:
    if mode == "FINAL_PLAN":
        return "Do you want me to continue with a monitoring checklist for the next 24 hours?"
    if problem_type == "DESIGN":
        return "Do you want to continue and I’ll build the next design step after your answers?"
    return "Do you want to continue with a step-by-step diagnosis after your reply?"


# =============================================================================
# Deterministic follow-up parsing (v7 safer parsing)
# =============================================================================


_TEMP_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?:°\s*)?c\b|(?P<val2>\d+(?:\.\d+)?)\s*(?:deg(?:ree)?s?)\b",
    re.IGNORECASE,
)
_PH_RE = re.compile(r"\bp\s*H\s*[:=]?\s*(?P<val>\d+(?:\.\d+)?)\b", re.IGNORECASE)


def _extract_numbered_answers(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []

    parts = re.split(r"\s*(?:^|[,;\n])\s*(?:\d+)\s*[\).:-]\s*", t)
    parts = [p.strip(" ,;\n\t") for p in parts if p.strip(" ,;\n\t")]
    if len(parts) >= 2:
        return parts

    comma_parts = [p.strip() for p in re.split(r"\s*,\s*", t) if p.strip()]
    if 2 <= len(comma_parts) <= 6:
        return comma_parts

    return [t]


def _parse_temperature_range_c(text: str) -> Optional[Tuple[float, float]]:
    t = (text or "").lower()
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:~|-|–|to)\s*(\d+(?:\.\d+)?)\b", t)
    if not m:
        return None
    try:
        lo = float(m.group(1))
        hi = float(m.group(2))
    except Exception:  # noqa: BLE001
        return None
    if lo > hi:
        lo, hi = hi, lo
    if 0 <= lo <= 60 and 0 <= hi <= 60:
        return (lo, hi)
    return None


def _parse_temperature_c(text: str) -> Optional[float]:
    m = _TEMP_RE.search(text)
    if m:
        val = m.group("val") or m.group("val2")
        try:
            return float(val)
        except Exception:  # noqa: BLE001
            return None

    m2 = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
    if m2:
        val = float(m2.group(1))
        if 10 <= val <= 40:
            return val
    return None


def _parse_ph(text: str) -> Optional[float]:
    m = _PH_RE.search(text)
    if m:
        try:
            return float(m.group("val"))
        except Exception:  # noqa: BLE001
            return None

    m2 = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
    if m2:
        val = float(m2.group(1))
        if 4 <= val <= 10:
            return val
    return None


def _parse_fish_species(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None

    low = t.lower()
    known = {
        "tilapia": "Tilapia",
        "catfish": "Catfish",
        "trout": "Trout",
        "goldfish": "Goldfish",
        "koi": "Koi",
        "carp": "Carp",
        "bass": "Bass",
    }
    for key, normalized in known.items():
        if key in low:
            return normalized

    # Conservative acceptance: letters/spaces/hyphen only, reject common non-fish replies.
    if re.fullmatch(r"[A-Za-z][A-Za-z\s\-]{1,30}", t):
        blocked = {
            "small",
            "big",
            "beginner",
            "hobbyist",
            "nft",
            "dwc",
            "media bed",
            "unknown",
            "not sure",
            "none",
        }
        if t.lower().strip() not in blocked:
            return t.title()

    return None


def _question_slot_name(question: str) -> Optional[str]:
    q = question.lower()
    if "temperature" in q or "temp" in q:
        return "temperature"
    if "ph" in q:
        return "ph"
    if ("type of fish" in q) or ("fish" in q and "type" in q):
        return "fish_species"
    if any(k in q for k in ("behavior", "gasp", "stress", "symptom")):
        return "general_behavior"
    if any(k in q for k in ("aeration", "air stone", "dissolved oxygen", "do ")):
        return "aeration"
    if any(k in q for k in ("flow rate", "flow", "circulation", "pump")):
        return "flow_rate"
    if any(k in q for k in ("recently changed", "recent change", "water change", "added chemicals")):
        return "recent_changes"
    if any(k in q for k in ("ammonia", "nitrite", "nitrate")):
        return "water_quality_readings"
    return None


def update_known_facts_from_followup(user: str) -> None:
    answers = _extract_numbered_answers(user)
    pending = list(state.pending_questions)

    # Prefer mapping by the exact pending questions order.
    if pending and len(answers) >= 1:
        for i, ans in enumerate(answers[: len(pending)]):
            slot = _question_slot_name(pending[i])

            if slot == "temperature":
                tr = _parse_temperature_range_c(ans)
                if tr is not None:
                    state.known_facts["temperature_range_c"] = [tr[0], tr[1]]
                else:
                    temp = _parse_temperature_c(ans)
                    if temp is not None:
                        state.known_facts["temperature_c"] = temp

            elif slot == "ph":
                ph = _parse_ph(ans)
                if ph is not None:
                    state.known_facts["pH"] = ph

            elif slot == "fish_species":
                species = _parse_fish_species(ans)
                if species:
                    state.known_facts["fish_species"] = species

            elif slot == "general_behavior":
                if ans.strip():
                    state.known_facts["general_behavior"] = ans.strip()

            elif slot == "aeration":
                if ans.strip():
                    state.known_facts["aeration"] = ans.strip()

            elif slot == "flow_rate":
                if ans.strip():
                    state.known_facts["flow_rate"] = ans.strip()

            elif slot == "recent_changes":
                if ans.strip():
                    state.known_facts["recent_changes"] = ans.strip()

            elif slot == "water_quality_readings":
                if ans.strip():
                    state.known_facts["water_quality_readings"] = ans.strip()

        state.pending_questions = []

    # Heuristic extraction fallback
    if "temperature_c" not in state.known_facts and "temperature_range_c" not in state.known_facts:
        tr = _parse_temperature_range_c(user)
        if tr is not None:
            state.known_facts["temperature_range_c"] = [tr[0], tr[1]]
        else:
            temp = _parse_temperature_c(user)
            if temp is not None:
                state.known_facts["temperature_c"] = temp

    if "pH" not in state.known_facts:
        ph = _parse_ph(user)
        if ph is not None:
            state.known_facts["pH"] = ph

    if "fish_species" not in state.known_facts:
        species = _parse_fish_species(user)
        if species:
            state.known_facts["fish_species"] = species

    low = (user or "").lower()

    # "unknown but useful" flow-rate responses
    if any(k in low for k in ["flow rate", "pump"]) and "flow_rate" not in state.known_facts:
        if any(k in low for k in ["not sure", "don't know", "do not know", "unknown"]):
            state.known_facts["flow_rate"] = "unknown"
        if any(k in low for k in ["pump is not enough", "pump not enough", "weak pump", "weak", "undersized"]):
            state.known_facts["pump_suspected_undersized"] = True
            if "flow_rate" not in state.known_facts:
                state.known_facts["flow_rate"] = "unknown (user suspects pump may be undersized)"

    # Pump upgrade interest hints
    if "pump" in low and any(k in low for k in ["upgrade", "weak", "not enough", "undersized"]):
        state.known_facts["pump_upgrade_interest"] = True
        if "not enough" in low or "weak" in low or "undersized" in low:
            state.known_facts["pump_suspected_undersized"] = True

    # Additional useful heuristics for free-form follow-up text
    if "general_behavior" not in state.known_facts:
        behavior_tags: List[str] = []
        if "moving slow" in low or "sluggish" in low:
            behavior_tags.append("fish moving slowly")
        if "eat less" in low or "eating less" in low or "not eating" in low:
            behavior_tags.append("reduced feeding/appetite")
        if "gasp" in low:
            behavior_tags.append("surface gasping")
        if behavior_tags:
            state.known_facts["general_behavior"] = ", ".join(dict.fromkeys(behavior_tags))

    if "recent_changes" not in state.known_facts:
        if re.search(r"\b(no|none|not really|nothing)\b.*\b(change|changed|changes)\b", low):
            state.known_facts["recent_changes"] = "no major recent changes reported"


# =============================================================================
# UI / output helpers
# =============================================================================


def render_ask_or_partial(data: Dict[str, Any]) -> str:
    mode = data.get("mode", "ASK_MORE")
    lines: List[str] = []

    if mode == "ASK_MORE":
        lines.append("I need a few details to narrow this down.")
    else:
        lines.append("I can give you a safe preliminary plan now, but I still need a few details to be more precise.")

    if data.get("severity") and data["severity"] != "UNKNOWN":
        lines.append(f"Severity: {data['severity']}")

    immediate_actions = data.get("immediate_actions", [])
    if immediate_actions:
        lines.append("")
        lines.append("Safe first steps you can do now:")
        for i, step in enumerate(immediate_actions, 1):
            lines.append(f"{i}. {step}")

    plan_steps = data.get("plan_steps", [])
    if mode == "PARTIAL_PLAN" and plan_steps:
        lines.append("")
        lines.append("Preliminary plan:")
        for i, step in enumerate(plan_steps, 1):
            lines.append(f"{i}. {step}")

    qs = data.get("next_questions", [])
    if qs:
        lines.append("")
        lines.append("Questions:")
        for i, q in enumerate(qs, 1):
            lines.append(f"{i}. {q}")
        lines.append("")
        lines.append("You can reply in one line, for example: 1) ... 2) ... 3) ...")

    offer = data.get("followup_offer", "").strip()
    if offer:
        lines.append("")
        lines.append(offer)

    return "\n".join(lines)


# =============================================================================
# Main turn handler
# =============================================================================


last_bot = ""


def handle_turn(user: str) -> None:
    global last_bot

    if state.mode == "DIAGNOSIS" and (state.pending_questions or state.asked_questions):
        update_known_facts_from_followup(user)

    # Top-level recommendation intent (e.g., "I want to upgrade my water pump")
    # Handle this BEFORE generic troubleshooting questioning.
    top_reco = detect_top_level_recommendation_intent(user)
    if top_reco.get("is_recommendation"):
        ans = answer_top_level_recommendation(user, top_reco.get("topic", "general"))
        state.mode = "DIAGNOSIS"
        state.last_agent_message = ans
        state.last_topic = top_reco.get("topic", "")
        print(ans)
        return

    intent = classify_intent(last_bot, user)

    # If we're in a diagnostic flow and have asked questions, treat replies as follow-up.
    if state.mode == "DIAGNOSIS" and state.asked_questions:
        intent = "FOLLOWUP"

    if intent == "NEW_TOPIC":
        print("[RESET]")
        state.reset()

    if state.mode == "REFINEMENT":
        ans = refine_answer(user)
        state.last_answer = ans
        state.last_agent_message = ans
        print(ans)
        return

    # Inline sub-question during ongoing diagnosis (e.g., pump recommendation in numbered reply)
    inline_q = detect_inline_subquestion(user)
    if state.mode == "DIAGNOSIS" and state.asked_questions and inline_q.get("has_subquestion"):
        sub_ans = answer_inline_subquestion(user, inline_q)

        data = decision_model(user)
        data = sanitize_decision_payload(data)
        if data.get("mode") not in {"ASK_MORE", "PARTIAL_PLAN"}:
            data["mode"] = "PARTIAL_PLAN"
        data = filter_questions_by_known_facts(data)
        data["next_questions"] = dedupe(data.get("next_questions", []))[:2]

        # Build continuation question queue
        qs: List[str] = []
        for q in data.get("next_questions", []):
            if q not in state.asked_questions:
                qs.append(q)
        state.pending_questions = qs[:2]
        for q in state.pending_questions:
            state.asked_questions.append(q)

        followup_block = render_ask_or_partial(data)
        combined = (
            f"{sub_ans}\n\n---\n"
            "To continue the diagnosis, here is the next step:\n"
            f"{followup_block}"
        )
        state.last_agent_message = combined
        state.last_subquestion = inline_q.get("question_text", "")
        state.last_topic = inline_q.get("topic", "")
        print(combined)
        return

    data = decision_model(user)
    data = sanitize_decision_payload(data)
    data = filter_questions_by_known_facts(data)

    # If model says FINAL but still has missing info, downgrade.
    if data.get("mode") == "FINAL_PLAN" and data.get("missing_info"):
        data["mode"] = "PARTIAL_PLAN"
        if not data.get("stop_reason"):
            data["stop_reason"] = "Missing information still remains."

    if data.get("mode") in {"ASK_MORE", "PARTIAL_PLAN"}:
        budget = 4 if len(state.known_facts) < 2 else 3
        qs: List[str] = []
        data["next_questions"] = dedupe(data.get("next_questions", []))
        for q in data["next_questions"]:
            if q not in state.asked_questions:
                qs.append(q)

        state.pending_questions = qs[:budget]
        for q in state.pending_questions:
            state.asked_questions.append(q)

        rendered = render_ask_or_partial(data)
        state.last_agent_message = rendered
        print(rendered)
        return

    ans = complex_reason(user)
    state.mode = "REFINEMENT"
    state.last_answer = ans
    state.last_agent_message = ans
    print(ans)


# =============================================================================
# Startup / RAG build
# =============================================================================


def build_rag_index_from_urls() -> Optional[FAISS]:
    url_entries = parse_urls_file(URL_FILE)
    urls = [e["url"] for e in url_entries]
    if not urls:
        logging.warning("No URLs found in %s. Running without web URLs for RAG.", URL_FILE)

    documents = []
    if urls:
        with ThreadPoolExecutor() as pool:
            future_to_entry = {pool.submit(load_web_page, e["url"]): e for e in url_entries}
            for future in as_completed(future_to_entry):
                entry = future_to_entry[future]
                loaded_docs = future.result()
                for d in loaded_docs:
                    d.metadata["source_type"] = "web"
                    d.metadata["url_category"] = entry.get("category", "UNCATEGORIZED")
                    if entry.get("label"):
                        d.metadata["url_label"] = entry["label"]
                documents.extend(loaded_docs)

    # Load local knowledge files (knowledge/*.md, *.txt)
    documents.extend(load_local_knowledge_documents(KNOWLEDGE_DIR))

    if not documents:
        logging.warning("No documents loaded from URLs or local knowledge. Running without RAG.")
        return None

    try:
        return build_vector_store(documents)
    except Exception as err:  # noqa: BLE001
        logging.warning("Failed to build FAISS index – %s", err)
        return None


def chat() -> None:
    global last_bot
    print("Aquaponics AI v7.3.1 + RAG (exit to quit)")
    while True:
        u = input("You> ")
        if u.strip().lower() == "exit":
            break
        handle_turn(u)
        # Use the actual last agent message (questions OR answer) for follow-up detection
        last_bot = state.last_agent_message or state.last_answer


if __name__ == "__main__":
    requests_cache.install_cache(CACHE_NAME, expire_after=CACHE_EXPIRE)

    VECTORSTORE = build_rag_index_from_urls()
    if VECTORSTORE is None:
        logging.info("RAG disabled (no vectorstore).")
    else:
        logging.info("RAG enabled.")

    chat()
