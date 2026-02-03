"""Aquaponics diagnostic chatbot + RAG (FAISS).

This file is based on your existing controller-style chatbot (test2.py)
and merges in the FAISS vectorstore pipeline you previously used in
chatbot3_ollama_react.py.

Pipeline at startup:
  urls.txt -> load web pages -> chunk -> embed -> FAISS index

At runtime:
  user question -> retrieve top-k chunks -> feed as <context> into prompts

Notes
-----
* If FAISS / embeddings can't be built (e.g., offline, missing deps), the bot
  will still run, but without retrieval context.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import requests_cache
from langchain_ollama.llms import OllamaLLM
from langchain.prompts import ChatPromptTemplate

# --- RAG deps (FAISS) ---
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


# =============================================================================
# Configuration
# =============================================================================

URL_FILE = "urls.txt"

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


def load_urls_from_file(file_path: str) -> List[str]:
    p = pathlib.Path(file_path)
    if not p.exists():
        logging.error("URL file %s does not exist", file_path)
        return []
    urls = [u.strip() for u in p.read_text(encoding="utf-8").splitlines() if u.strip()]
    # de-dup
    return sorted(set(urls))


def load_web_page(url: str):
    try:
        logging.info("Loading %s", url)
        loader = WebBaseLoader(url)
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

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    logging.info("Creating FAISS index with %d chunks …", len(docs))
    return FAISS.from_documents(docs, embeddings)


def retrieve_context(vs: Optional[FAISS], question: str, k: int = TOP_K) -> str:
    if vs is None:
        return ""
    try:
        retrieved = vs.similarity_search(question, k=k)
        return "\n\n".join(doc.page_content for doc in retrieved)
    except Exception as err:  # noqa: BLE001
        logging.warning("Retrieval failed – %s", err)
        return ""


# =============================================================================
# STATE
# =============================================================================


class ThreadState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mode = "DIAGNOSIS"
        self.problem_id = str(uuid4())
        self.known_facts = {}
        self.asked_questions = []
        # Questions asked in the most recent round. Used to deterministically
        # map numbered follow-up answers (e.g. "1) 25C, 2) pH 7, 3) Tilapia")
        # into known_facts so we don't rely on the LLM to do extraction.
        self.pending_questions: List[str] = []
        self.confidence = 0.0
        self.last_answer = ""


state = ThreadState()


# =============================================================================
# JSON PARSER
# =============================================================================


def extract_json(text: str):
    m = re.search(r"({.*})", text, re.DOTALL)
    if not m:
        raise ValueError("NO JSON:\n" + text)
    blob = m.group(1)
    # Lightweight repair for common model JSON mistakes:
    # - trailing commas before ] or }
    # This prevents crashes on outputs like:  "answer_outline":"",],
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
# DECISION PROMPT (now includes RAG context)
# =============================================================================


decision_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are an aquaponics diagnostic controller.

Use <context> as supporting evidence when relevant.
If <context> is empty, rely on general aquaponics knowledge.

STRICT RULES:
1) If need_more_info = true, you MUST output at least ONE question in next_questions.
2) Never list something in missing_info if user already provided it.
3) Always validate numeric values:
   - If temperature < 15°C or > 35°C → flag as abnormal.
   - If pH < 6 or > 9 → flag as risky.
4) If you already have enough info, set:
   need_more_info = false
   and provide an answer_outline.
5) NEVER return need_more_info=true with empty next_questions.

6) Ask 2-4 HIGH-VALUE questions per round (not just one).
7) NEVER repeat a question that appears in asked_questions.
8) If missing_info is empty -> need_more_info MUST be false.


STOP RULE:
If you already have:
- temperature
- pH
- fish species
- general behavior

AND no red flags appear,
you MUST set:
"need_more_info": false
and produce final answer.

OUTPUT STRICT JSON:

{{
 "action":"DIAGNOSE" or "REFINE",
 "need_more_info":true or false,
 "confidence":0.0-1.0,
 "known_facts_update":{{}},
 "missing_info":[],
 "next_questions":[],
 "answer_outline":"",
 "stop_reason":""
}}
""",
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

USER:
{user}
""".strip(),
        ),
    ]
)


# =============================================================================
# COMPLEX REASON (now includes RAG context)
# =============================================================================


complex_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "Use known facts and <context> to diagnose."),
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
# Chat controller functions
# =============================================================================


VECTORSTORE: Optional[FAISS] = None




def _extract_followup_facts(user: str) -> None:
    """Best-effort parsing of common follow-up answers into known_facts.

    Why this exists:
    - The LLM often returns known_facts_update = {} for short follow-up answers.
    - The decision prompt currently does not give the LLM the *questions it asked*,
      so it may not know that "25, 7, tilapia" correspond to temp/pH/species.

    This parser is intentionally simple and conservative.
    """

    u = user.strip()
    ul = u.lower()

    # If we asked specific questions, map numbers in the same order.
    # Example user reply: "1. 25 degree, 2. pH 7, 3. Tilapia"
    nums = [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", ul)]

    asked = " ".join(q.lower() for q in state.asked_questions[-6:])  # last few are enough

    # Fish species (keyword)
    if "tilapia" in ul and "fish_species" not in state.known_facts:
        state.known_facts["fish_species"] = "tilapia"

    # Temperature
    temp_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:°\s*c|\bc\b|degrees?|deg)\b", ul)
    if temp_match:
        state.known_facts["temperature_c"] = float(temp_match.group(1))
    elif ("temperature" in asked or "temp" in asked) and nums:
        # Fall back to first numeric answer if it looks like a temp range
        if 0 <= nums[0] <= 60:
            state.known_facts.setdefault("temperature_c", nums[0])

    # pH
    ph_match = re.search(r"\bph\b\D*(\d+(?:\.\d+)?)", ul)
    if ph_match:
        state.known_facts["pH"] = float(ph_match.group(1))
    else:
        # If both temp and pH were asked and we have >=2 numbers, guess order: temp then pH
        if ("ph" in asked) and len(nums) >= 2:
            # If we already set temp from the first number, use the second as pH
            candidate = nums[1]
            if 0 <= candidate <= 14:
                state.known_facts.setdefault("pH", candidate)

def decision_model(user: str):
    context = retrieve_context(VECTORSTORE, user)

    msgs = decision_prompt.format_messages(
        context=context,
        # Give the model the *current* asked questions for better follow-up reasoning.
        asked=json.dumps(state.pending_questions or state.asked_questions, indent=2),
        facts=json.dumps(state.known_facts, indent=2),
        user=user,
    )

    raw = llm.invoke(msgs)
    print("\n[RAW MODEL OUTPUT]\n", raw)
    data = extract_json(raw)

    # merge facts
    state.known_facts.update(data.get("known_facts_update", {}))

    # auto stop enforcement
    required = {"temperature_c", "pH", "fish_species", "general_behavior"}
    if required.issubset(state.known_facts.keys()):
        data["need_more_info"] = False
        data["stop_reason"] = "Enough diagnostic info collected"

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
    # Keep refinement lightweight but still allow retrieved snippets
    context = retrieve_context(VECTORSTORE, user)
    return llm.invoke(
        f"""
Refine diagnosis.

<context>
{context}

FACTS:
{json.dumps(state.known_facts,indent=2)}

PREVIOUS:
{state.last_answer}

NEW:
{user}
"""
    )


def dedupe(new_qs):
    clean = []
    for q in new_qs:
        qn = q.lower().strip()
        if any(qn in old.lower() or old.lower() in qn for old in state.asked_questions):
            continue
        clean.append(q)
    return clean


# =============================================================================
# Deterministic follow-up parsing (fixes known_facts_update being empty)
# =============================================================================


_TEMP_RE = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*(?:°\s*)?c\b|(?P<val2>\d+(?:\.\d+)?)\s*(?:deg(?:ree)?s?)\b", re.IGNORECASE)
_PH_RE = re.compile(r"\bp\s*H\s*[:=]?\s*(?P<val>\d+(?:\.\d+)?)\b", re.IGNORECASE)


def _extract_numbered_answers(text: str) -> List[str]:
    """Extract answers from formats like:
    '1) 25C, 2) 7, 3) Tilapia' or '1. ... 2. ... 3. ...'
    """
    t = (text or "").strip()
    if not t:
        return []

    # Split on leading numbering markers.
    parts = re.split(r"\s*(?:^|[,;\n])\s*(?:\d+)\s*[\).:-]\s*", t)
    parts = [p.strip(" ,;\n\t") for p in parts if p.strip(" ,;\n\t")]
    if len(parts) >= 2:
        return parts

    # Fallback: split by commas if user replied "25, 7, Tilapia"
    comma_parts = [p.strip() for p in re.split(r"\s*,\s*", t) if p.strip()]
    if 2 <= len(comma_parts) <= 6:
        return comma_parts

    return [t]


def _parse_temperature_c(text: str) -> Optional[float]:
    m = _TEMP_RE.search(text)
    if not m:
        # If they answered "25" with no unit, treat 10-40 as plausible temp.
        m2 = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
        if m2:
            val = float(m2.group(1))
            if 10 <= val <= 40:
                return val
        return None
    val = m.group("val") or m.group("val2")
    try:
        return float(val)
    except Exception:  # noqa: BLE001
        return None


def _parse_ph(text: str) -> Optional[float]:
    m = _PH_RE.search(text)
    if m:
        try:
            return float(m.group("val"))
        except Exception:  # noqa: BLE001
            return None
    # If they answered "7" with no label, treat 4-10 as plausible pH.
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
    # Very small normalization set (extend later).
    low = t.lower()
    if "tilapia" in low:
        return "Tilapia"
    if "catfish" in low:
        return "Catfish"
    if "trout" in low:
        return "Trout"
    if "goldfish" in low:
        return "Goldfish"
    # If user wrote a short species name, keep as-is.
    if len(t) <= 40:
        return t
    return None


def update_known_facts_from_followup(user: str) -> None:
    """Update state.known_facts using the user's follow-up message.

    We prefer mapping by the last asked questions (pending_questions) because
    user replies often omit labels (e.g., "1) 25, 2) 7, 3) Tilapia").
    """
    answers = _extract_numbered_answers(user)
    pending = list(state.pending_questions)

    # If we have a one-to-one mapping, use it.
    if pending and len(answers) >= 2:
        for i, ans in enumerate(answers[: len(pending)]):
            q = pending[i].lower()
            if "temperature" in q:
                temp = _parse_temperature_c(ans)
                if temp is not None:
                    state.known_facts["temperature_c"] = temp
            elif "ph" in q:
                ph = _parse_ph(ans)
                if ph is not None:
                    state.known_facts["pH"] = ph
            elif "type of fish" in q or "fish" in q and "type" in q:
                species = _parse_fish_species(ans)
                if species:
                    state.known_facts["fish_species"] = species
            elif "behavior" in q or "gasp" in q or "stress" in q:
                # Keep behavior as free text.
                if ans.strip():
                    state.known_facts["general_behavior"] = ans.strip()

        # After a follow-up, clear pending questions.
        state.pending_questions = []

    # Heuristic extraction even without pending mapping.
    if "temperature_c" not in state.known_facts:
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


last_bot = ""


def handle_turn(user: str):
    global last_bot

    # Best-effort: turn short follow-up replies into structured facts.
    # This fixes the issue where the model returns known_facts_update = {}.
    if state.mode == "DIAGNOSIS" and (state.pending_questions or state.asked_questions):
        update_known_facts_from_followup(user)

    intent = classify_intent(last_bot, user)

    # override followup
    if state.mode == "DIAGNOSIS" and state.asked_questions:
        intent = "FOLLOWUP"

    if intent == "NEW_TOPIC":
        print("[RESET]")
        state.reset()

    if state.mode == "REFINEMENT":
        ans = refine_answer(user)
        state.last_answer = ans
        print(ans)
        return

    data = decision_model(user)

    # safety stop: no missing info -> force refine
    if data.get("need_more_info") and not data.get("missing_info"):
        data["need_more_info"] = False
        data["action"] = "REFINE"
        data["stop_reason"] = "No missing info left"

    # ask
    if data.get("need_more_info") and data.get("action") == "DIAGNOSE":
        # dynamic budget
        budget = 5 if len(state.known_facts) < 2 else 2

        qs = []
        data["next_questions"] = dedupe(data.get("next_questions", []))
        for q in data["next_questions"]:
            if q not in state.asked_questions:
                qs.append(q)

        # Track what we just asked so the next user reply can be parsed deterministically.
        state.pending_questions = qs[:budget]
        for q in state.pending_questions:
            state.asked_questions.append(q)
            print("Q:", q)
        return

    # answer
    ans = complex_reason(user)
    state.mode = "REFINEMENT"
    state.last_answer = ans
    print(ans)


def build_rag_index_from_urls() -> Optional[FAISS]:
    """Build FAISS index from urls.txt. Returns None if build fails."""
    urls = load_urls_from_file(URL_FILE)
    if not urls:
        logging.warning("No URLs found in %s. Running without RAG.", URL_FILE)
        return None

    documents = []
    with ThreadPoolExecutor() as pool:
        future_to_url = {pool.submit(load_web_page, url): url for url in urls}
        for future in as_completed(future_to_url):
            documents.extend(future.result())

    if not documents:
        logging.warning("No documents loaded from URLs. Running without RAG.")
        return None

    try:
        return build_vector_store(documents)
    except Exception as err:  # noqa: BLE001
        logging.warning("Failed to build FAISS index – %s", err)
        return None


def chat():
    global last_bot
    print("Aquaponics AI v6 + RAG (exit to quit)")
    while True:
        u = input("You> ")
        if u == "exit":
            break
        handle_turn(u)
        last_bot = state.last_answer


if __name__ == "__main__":
    # Cache HTTP fetches
    requests_cache.install_cache(CACHE_NAME, expire_after=CACHE_EXPIRE)

    # Build FAISS once at startup
    VECTORSTORE = build_rag_index_from_urls()
    if VECTORSTORE is None:
        logging.info("RAG disabled (no vectorstore).")
    else:
        logging.info("RAG enabled.")

    chat()