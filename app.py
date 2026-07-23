"""Streamlit UI: deterministic Design Calculator / Optimizer + the consultative agent chat.

The "Assistant (chat)" mode drives the same tool-calling brain as the Telegram bot
(agronaut_agent) — per-browser-session identity, System Profile memory, calibration, and
the validation-gated deterministic tools. The legacy srcs/chatbot state machine is no
longer wired to the UI.
"""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from agent.calculator_ui import render_calculator
from agent.optimizer_ui import render_optimizer


APP_TITLE = "🌱 Agronaut"


def _agent_error() -> str | None:
    """Build the per-session agent if needed. Returns a user-facing reason when chat is
    unavailable (missing chat stack or no tool-calling LLM provider configured)."""
    if "agent" in st.session_state:
        return None
    if "agent_error" in st.session_state:
        return st.session_state.agent_error
    try:
        from agronaut_agent.core import AgronautAgent
        st.session_state.agent = AgronautAgent()
        return None
    except ModuleNotFoundError as exc:
        reason = (f"Chat mode needs the optional chat stack (`{exc.name}` isn't installed). "
                  "The **Design Calculator** and **Optimize Ratio** modes work without it — "
                  "to enable chat: `pip install -r requirement.txt`.")
    except Exception as exc:
        reason = ("Chat needs a tool-calling LLM provider (e.g. `LLM_PROVIDER=nvidia` with "
                  f"`NVIDIA_API_KEY`) — couldn't start one: {exc}. The **Design Calculator** "
                  "and **Optimize Ratio** modes are fully deterministic and keep working.")
    st.session_state.agent_error = reason
    return reason


def _web_user() -> str:
    """Stable per-browser-session identity — concurrent web users never share memory."""
    if "web_user" not in st.session_state:
        st.session_state.web_user = uuid4().hex[:12]
    return st.session_state.web_user


def _ensure_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _render_header() -> None:
    st.title(APP_TITLE)
    st.write("Your agronomy agent: design, optimize, and troubleshoot aquaponics systems.")


def _render_chat_sidebar() -> None:
    st.sidebar.header("Controls")
    if st.sidebar.button("Reset conversation", use_container_width=True):
        agent = st.session_state.get("agent")
        if agent is not None:
            agent.reset("web", _web_user())
        st.session_state.messages = []
        _rerun()
    st.sidebar.caption(
        "Memory lasts for this browser session. The bot remembers your system as you talk "
        "(same brain as the Telegram bot)."
    )


def _add_message(role: str, content: str) -> None:
    st.session_state.messages.append({"role": role, "content": content})


def _render_messages() -> None:
    for msg in st.session_state.messages:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])


def _handle_user_input(user_text: str) -> None:
    _add_message("user", user_text)
    agent = st.session_state.agent
    try:
        with st.spinner("Thinking (running the numbers)..."):
            reply = agent.handle_message("web", _web_user(), user_text)
    except Exception:
        reply = ("Something went wrong talking to the model — your message wasn't lost, "
                 "please try again.")
    _add_message("assistant", reply)


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
    # on a fresh install. Chat needs the agent stack + a tool-calling LLM provider.
    mode = st.sidebar.radio(
        "Mode",
        ("Design Calculator", "Optimize Ratio", "Assistant (chat)"),
        help="Calculator sizes one system. Optimizer finds the best fish/crop ratio for "
             "your constraint. Chat runs a consultation with the full agent (needs an LLM).",
    )

    if mode == "Design Calculator":
        render_calculator()
        return
    if mode == "Optimize Ratio":
        render_optimizer()
        return

    # Assistant (chat) — the real consultative agent, degrading gracefully when the
    # chat stack or an LLM provider is missing (never a traceback in the UI).
    reason = _agent_error()
    if reason:
        st.warning(reason)
        return

    _render_chat_sidebar()
    _render_messages()

    if not st.session_state.messages:
        st.info("Tell me what you're trying to do — design a system, optimize a ratio, "
                "or troubleshoot a problem.")

    prompt = st.chat_input("Describe your system, goal, or problem...")
    if prompt:
        _handle_user_input(prompt)
        _rerun()


if __name__ == "__main__":
    main()
