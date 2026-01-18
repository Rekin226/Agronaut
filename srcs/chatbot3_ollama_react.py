from __future__ import annotations

import logging
import pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, List, Tuple

import requests_cache
from langchain.agents import AgentType, initialize_agent
from langchain.memory import ConversationBufferMemory
from langchain.prompts import ChatPromptTemplate
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.tools import StructuredTool
from langchain_community.chat_models import ChatOllama
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

# ✅ Your choice:
# Router (Agent brain): phi3:mini
# Answer model: llama3
ROUTER_MODEL_NAME = "phi3:mini"
ANSWER_MODEL_NAME = "llama3"

CACHE_NAME = "web_cache"
CACHE_EXPIRE = 86_400  # 1 day

TOP_K = 3  # RAG retrieval chunks


# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# =============================================================================
# Prompt templates (minimal / testable)
# =============================================================================

SIMPLE_PROMPT = """
You are a helpful aquaponics Q&A assistant.

Goal:
- Answer simple, focused questions about aquaponics in English.
- Keep it short, clear, and practical.

Rules:
1) Use <Context> as the primary source when possible.
2) If context is missing, you may use general aquaponics knowledge,
   but do NOT invent specific measurements, citations, or study results.
3) Do NOT show chain-of-thought. Provide only the final answer.
4) Keep the response concise:
   - 1–3 short paragraphs, OR
   - 3–6 bullet points.

<context>
{context}

<chat_history>
{history}

<question>
{question}

Answer in English, clearly and concisely:
""".strip()


# Phase 1: Clarify (ask 5–10 key questions, no solutions)
COMPLEX_CLARIFY_PROMPT = """
In this message, do NOT provide solutions. Your only job is to ask clarifying questions.
Assume the user is a beginner. Use simple words and short sentences.
Output format: (1) One-sentence understanding. (2) A numbered list of questions.
Ask 5–10 questions.

<context>
{context}

<chat_history>
{history}

<user_question>
{question}
""".strip()


# Phase 2: Answer + refine (give best answer, then ask 1–3 refinement questions/options)
COMPLEX_ANSWER_PROMPT = """
Start by giving the best answer you can using the information available so far.
Use simple language suitable for beginners.
Do NOT repeat the clarification questions from earlier messages.

Then, at the end:
- Ask 1–3 follow-up questions OR offer up to 2 refinement options
  that would make the answer more precise.

<context>
{context}

<chat_history>
{history}

<user_response_or_new_info>
{question}
""".strip()


# =============================================================================
# Helpers
# =============================================================================

def load_urls_from_file(file_path: str) -> List[str]:
    p = pathlib.Path(file_path)
    if not p.exists():
        logging.error("File %s does not exist", file_path)
        return []
    urls = [u.strip() for u in p.read_text(encoding="utf-8").splitlines() if u.strip()]
    return sorted(set(urls))


def load_web_page(url: str):
    try:
        logging.info("Loading %s", url)
        loader = WebBaseLoader(url)
        return loader.load()
    except Exception as err:  # noqa: BLE001
        logging.warning("Failed to load %s – %s", url, err)
        return []


def build_vector_store(documents):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    docs = splitter.split_documents(documents)

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    logging.info("Creating FAISS index with %d chunks …", len(docs))
    return FAISS.from_documents(docs, embeddings)


# =============================================================================
# Chatbot (Agent Router + Funnel-style complex flow)
# =============================================================================

class AquaponicsChatbot:
    """
    Funnel behavior for complex questions:
    - Step 1: complex_clarify tool asks 5–10 key beginner-friendly questions (no solution).
    - Step 2: next user message automatically goes to complex_answer tool
              (best answer based on available info + 1–3 refinement questions/options).
    """

    def __init__(self, vectorstore: FAISS) -> None:
        self.vs = vectorstore
        self.memory = ConversationBufferMemory(return_messages=True)

        # Router LLM (chooses tools)
        self.router_llm = ChatOllama(model=ROUTER_MODEL_NAME, temperature=0)

        # Answer LLM (does all responses)
        self.answer_llm = ChatOllama(model=ANSWER_MODEL_NAME, temperature=0)

        # Prompt templates
        self.simple_prompt = ChatPromptTemplate.from_template(SIMPLE_PROMPT)
        self.complex_clarify_prompt = ChatPromptTemplate.from_template(COMPLEX_CLARIFY_PROMPT)
        self.complex_answer_prompt = ChatPromptTemplate.from_template(COMPLEX_ANSWER_PROMPT)

        # Funnel stage control
        # - "auto": normal mode (agent decides simple vs complex_clarify)
        # - "awaiting_complex_answer": next user message must go to complex_answer
        self.funnel_stage: str = "auto"

        # Tools
        tools = [
            StructuredTool.from_function(
                name="simple_aquaponics_answer",
                func=self.tool_simple_aquaponics_answer,
                description=(
                    "Use for simple aquaponics questions such as definitions, basic concepts, "
                    "quick factual answers, or short parameter guidance."
                ),
                return_direct=True,
            ),
            StructuredTool.from_function(
                name="complex_clarify",
                func=self.tool_complex_clarify,
                description=(
                    "Use when the user describes a situation/problem or asks for advice and more "
                    "information is needed. Ask 5–10 beginner-friendly clarifying questions. "
                    "Do NOT provide solutions."
                ),
                return_direct=True,
            ),
            StructuredTool.from_function(
                name="complex_answer",
                func=self.tool_complex_answer,
                description=(
                    "Use AFTER clarification. Provide the best possible answer based on info so far, "
                    "then ask 1–3 follow-up questions or offer up to 2 refinement options."
                ),
                return_direct=True,
            ),
        ]

        # Agent executor
        self.agent = initialize_agent(
            tools=tools,
            llm=self.router_llm,
            agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True,  # set False if you don't want tool logs
            handle_parsing_errors=True,
            max_iterations=2,  # avoid loops
            early_stopping_method="generate",
        )

    # --------------------------------------------------------------------- #
    # Public loop
    # --------------------------------------------------------------------- #

    def chat(self) -> None:
        print("\n--- Aquaponics Chatbot (Funnel Agent Router) ---\n")
        print(f"Router model : {ROUTER_MODEL_NAME}")
        print(f"Answer model : {ANSWER_MODEL_NAME}")
        print("Type 'clear' to reset memory, 'exit' to quit.")
        print("Tip: After a clarify step, your next message will automatically trigger the answer step.\n")

        try:
            while True:
                query = input("Question> ").strip()
                if not query:
                    continue

                ql = query.lower()
                if ql == "exit":
                    print("Bye!")
                    return
                if ql == "clear":
                    self.memory.clear()
                    self.funnel_stage = "auto"
                    print("Memory cleared.\n")
                    continue

                self._answer_query(query)

        except KeyboardInterrupt:
            print("\nInterrupted – goodbye!")

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    def _build_context_and_history(self, question: str) -> Tuple[str, str]:
        retrieved = self.vs.similarity_search(question, k=TOP_K)
        context = "\n\n".join(doc.page_content for doc in retrieved)

        history_text = "".join(
            f"{'User' if m.type == 'human' else 'Assistant'}: {m.content}\n"
            for m in self.memory.chat_memory.messages
        )
        return context, history_text

    def _normalize_tool_question(self, q: Any, fallback: str) -> str:
        """
        Robustly recover a question string even if router produces schema-like dicts.
        """
        if isinstance(q, str) and q.strip():
            return q.strip()

        if isinstance(q, dict):
            title = q.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()

            inner = q.get("question")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
            if isinstance(inner, dict):
                inner_title = inner.get("title")
                if isinstance(inner_title, str) and inner_title.strip():
                    return inner_title.strip()

        return fallback

    def _invoke_answer_llm(self, prompt: ChatPromptTemplate, question: str) -> str:
        context, history = self._build_context_and_history(question)
        messages = prompt.format_messages(context=context, history=history, question=question)
        resp = self.answer_llm.invoke(messages)
        return (resp.content or "").strip()

    # --------------------------------------------------------------------- #
    # Tool functions (StructuredTool expects clean signature)
    # --------------------------------------------------------------------- #

    def tool_simple_aquaponics_answer(self, question: str) -> str:
        q = self._normalize_tool_question(question, fallback=question if isinstance(question, str) else "")
        if not q.strip():
            return "Please provide a question."
        return self._invoke_answer_llm(self.simple_prompt, q)

    def tool_complex_clarify(self, question: str) -> str:
        q = self._normalize_tool_question(question, fallback=question if isinstance(question, str) else "")
        if not q.strip():
            return "Please describe your aquaponics situation or goal."

        # After we clarify, next user message should go to answer stage automatically
        self.funnel_stage = "awaiting_complex_answer"
        return self._invoke_answer_llm(self.complex_clarify_prompt, q)

    def tool_complex_answer(self, question: str) -> str:
        q = self._normalize_tool_question(question, fallback=question if isinstance(question, str) else "")
        if not q.strip():
            return "Please answer the previous questions so I can help you better."

        # After answering, reset funnel to auto so next new question can be routed again
        self.funnel_stage = "auto"
        return self._invoke_answer_llm(self.complex_answer_prompt, q)

    # --------------------------------------------------------------------- #
    # Routing entrypoint
    # --------------------------------------------------------------------- #

    def _answer_query(self, query: str) -> None:
        """
        Funnel logic:
        - If we just asked clarification questions, next message bypasses agent and goes to complex_answer.
        - Otherwise, let the agent choose between simple vs complex_clarify.
        """
        try:
            if self.funnel_stage == "awaiting_complex_answer":
                response = self.tool_complex_answer(query)
            else:
                result = self.agent.invoke({"input": query})
                response = (result.get("output") or "").strip()
                if not response:
                    # fallback
                    response = self.tool_simple_aquaponics_answer(query)

        except Exception as e:  # noqa: BLE001
            logging.error("Routing failed (%s). Falling back to SIMPLE tool.", e)
            self.funnel_stage = "auto"
            response = self.tool_simple_aquaponics_answer(query)

        print("\nAnswer:\n" + "-" * 60)
        print(response)
        print("-" * 60 + "\n")

        self.memory.chat_memory.add_user_message(query)
        self.memory.chat_memory.add_ai_message(response)


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    requests_cache.install_cache(CACHE_NAME, expire_after=CACHE_EXPIRE)

    urls = load_urls_from_file(URL_FILE)
    if not urls:
        logging.error("No URLs found in %s – aborting.", URL_FILE)
        return

    documents = []
    with ThreadPoolExecutor() as pool:
        future_to_url = {pool.submit(load_web_page, url): url for url in urls}
        for future in as_completed(future_to_url):
            documents.extend(future.result())

    if not documents:
        logging.error("No documents loaded – aborting.")
        return

    vector_store = build_vector_store(documents)
    bot = AquaponicsChatbot(vector_store)
    bot.chat()


if __name__ == "__main__":
    main()
