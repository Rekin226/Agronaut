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
        initial_sidebar_state="collapsed",
    )
    _ensure_session_state()
    _render_header()

    mode = st.sidebar.radio(
        "Mode",
        ("Assistant (chat)", "Design Calculator", "Optimize Ratio"),
        help="Chat troubleshoots a running system. Calculator sizes one. Optimizer finds the "
             "best fish/crop ratio for your constraint.",
    )

    if mode == "Design Calculator":
        render_calculator()
        return
    if mode == "Optimize Ratio":
        render_optimizer()
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
