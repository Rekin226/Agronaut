"""Streamlit UI for the aquaponics diagnostic chatbot."""

from __future__ import annotations

from typing import List

import streamlit as st

from agent.calculator_ui import render_calculator
from agent.optimizer_ui import render_optimizer


APP_TITLE = "🌱 Agronaut"


def _core():
    """Lazy-import the chat/RAG core so the Design Calculator mode runs without the
    chat stack (langchain, faiss, Ollama, requests_cache) installed."""
    import srcs.chatbot as core
    return core


def _chat_available() -> bool:
    """True when the optional chat/RAG stack is importable.

    Lets the deterministic Calculator/Optimizer modes run — and the chat mode
    degrade with a friendly message instead of a traceback — when langchain,
    faiss, requests_cache, or an LLM backend isn't installed."""
    try:
        import srcs.chatbot  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _init_cache() -> None:
    # Cache HTTP fetches for RAG content.
    import requests_cache
    core = _core()
    requests_cache.install_cache(core.CACHE_NAME, expire_after=core.CACHE_EXPIRE)


@st.cache_resource(show_spinner=False)
def _build_vectorstore() -> object | None:
    _init_cache()
    return _core().build_rag_index_from_urls()


def _reset_session_state() -> None:
    st.session_state.messages = []
    st.session_state.last_bot = ""
    core = _core()
    core.state.reset()
    core.last_bot = ""


def _ensure_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_bot" not in st.session_state:
        st.session_state.last_bot = ""


def _set_rag(use_rag: bool) -> None:
    core = _core()
    if use_rag:
        with st.spinner("Building knowledge index..."):
            core.VECTORSTORE = _build_vectorstore()
    else:
        core.VECTORSTORE = None


def _rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _render_header() -> None:
    st.title(APP_TITLE)
    st.write("Your agronomy agent: design, optimize, and troubleshoot aquaponics systems.")


def _render_sidebar() -> None:
    st.sidebar.header("Controls")

    use_rag = st.sidebar.checkbox(
        "Use web knowledge (RAG)",
        value=True,
        help="Disable to use general aquaponics knowledge only.",
    )

    if st.sidebar.button("Reset conversation", use_container_width=True):
        _reset_session_state()
        _rerun()

    _set_rag(use_rag)
    if use_rag:
        if _core().VECTORSTORE is None:
            st.sidebar.caption("RAG unavailable; using general knowledge.")
        else:
            st.sidebar.caption("RAG ready.")


def _format_questions(questions: List[str]) -> str:
    if not questions:
        return ""
    lines = ["I need a bit more info:"]
    lines.extend([f"- {q}" for q in questions])
    return "\n".join(lines)


def _add_message(role: str, content: str) -> None:
    st.session_state.messages.append({"role": role, "content": content})


def _render_messages() -> None:
    for msg in st.session_state.messages:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])


def _handle_user_input(user_text: str) -> None:
    core = _core()
    _add_message("user", user_text)

    prev_pending = list(core.state.pending_questions)
    prev_answer = core.state.last_answer

    core.handle_turn(user_text)
    core.last_bot = core.state.last_answer

    # If the model asked follow-up questions, surface them as assistant message.
    if core.state.pending_questions and core.state.pending_questions != prev_pending:
        assistant_text = _format_questions(core.state.pending_questions)
    else:
        assistant_text = core.state.last_answer or prev_answer or "I'm here to help."

    _add_message("assistant", assistant_text)


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="💧",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    _ensure_session_state()
    _render_header()

    # Design Calculator is the default: deterministic, no heavy deps, never crashes
    # on a fresh install. Chat is the legacy mode and needs the optional chat stack.
    mode = st.sidebar.radio(
        "Mode",
        ("Design Calculator", "Optimize Ratio", "Assistant (chat)"),
        help="Calculator sizes one system. Optimizer finds the best fish/crop ratio for "
             "your constraint. Chat troubleshoots a running system (needs the chat stack).",
    )

    if mode == "Design Calculator":
        render_calculator()
        return
    if mode == "Optimize Ratio":
        render_optimizer()
        return

    # Assistant (chat) — legacy. Degrade gracefully if the optional chat stack is
    # absent, so a missing dependency never takes down the whole app.
    if not _chat_available():
        st.warning(
            "Chat mode needs the optional chat stack (langchain, faiss, requests_cache, "
            "and an LLM backend), which isn't installed. The **Design Calculator** and "
            "**Optimize Ratio** modes work without it."
        )
        st.caption("To enable chat: `pip install -r requirement.txt`")
        return

    _render_sidebar()

    _render_messages()

    if not st.session_state.messages:
        st.info("Start with your fish behavior, water temperature, and pH.")

    prompt = st.chat_input("Describe your system issue or question...")
    if prompt:
        _handle_user_input(prompt)
        _rerun()


if __name__ == "__main__":
    main()
