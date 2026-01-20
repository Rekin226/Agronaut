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
from typing import List, Optional
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
    return json.loads(m.group(1))


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


def decision_model(user: str):
    context = retrieve_context(VECTORSTORE, user)

    msgs = decision_prompt.format_messages(
        context=context,
        facts=json.dumps(state.known_facts, indent=2),
        user=user,
    )

    raw = llm.invoke(msgs)
    print("\n[RAW MODEL OUTPUT]\n", raw)
    data = extract_json(raw)

    # merge facts
    state.known_facts.update(data.get("known_facts_update", {}))

    # auto stop enforcement
    required = {"water_temp", "pH_level", "fish_behavior", "water_clarity"}
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


last_bot = ""


def handle_turn(user: str):
    global last_bot

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

        for q in qs[:budget]:
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
