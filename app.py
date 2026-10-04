"""Streamlit UI: Assistant, Design (size a system, find the best ratio), My Twin, Quality.

The Assistant drives the same tool-calling brain as the Telegram bot
(agronaut_agent) — per-browser-session identity, System Profile memory, calibration, and
the validation-gated deterministic tools. The legacy srcs/chatbot state machine is no
longer wired to the UI.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import streamlit as st

from agent.calculator_ui import render_calculator
from agent.optimizer_ui import render_optimizer
from agent.quality_ui import render_quality
from agent.twin_ui import render_twin

# Optional private add-on (Agronaut Twin). Without it the app is unchanged.
try:
    from agronaut_twin.ui.studio import render_twin_studio
except ImportError:
    render_twin_studio = None
TWIN_STUDIO = "Digital Twin Studio"

APP_TITLE = "🌱 Agronaut"
_PHOTO_TYPES = ["png", "jpg", "jpeg", "webp"]

# Attaching a file to the chat box needs Streamlit's `accept_file` (1.43+). requirement.txt
# does not pin a version, so detect rather than assume — an older install falls back to a
# separate uploader instead of raising on first render.
_CHAT_INPUT_ACCEPTS_FILES = "accept_file" in inspect.signature(st.chat_input).parameters


def _agent_error() -> str | None:
    """Build the per-session agent if needed. Returns a user-facing reason when chat is
    unavailable (missing chat stack or no tool-calling LLM provider configured)."""
    # The error first: with no tool-calling model the agent is still built (My Twin needs
    # its stores), so "an agent exists" does not mean "chat works". Checked the other way
    # round, every call after the first reported a broken Assistant as fine.
    if "agent_error" in st.session_state:
        return st.session_state.agent_error
    if "agent" in st.session_state:
        return None
    try:
        from agronaut_agent.core import AgronautAgent
        # require_tools=False: the agent still carries the stores and the deterministic
        # twin when no tool-calling provider exists, so My Twin survives a missing key.
        agent = AgronautAgent(require_tools=False)
        st.session_state.agent = agent
        if agent.chat_error:
            reason = ("The Assistant needs a tool-calling model provider; run `agronaut "
                      f"setup` to pick one (couldn't start one: {agent.chat_error}). "
                      "**Design** and **My Twin** are fully deterministic and keep working.")
            st.session_state.agent_error = reason
            return reason
        return None
    except ModuleNotFoundError as exc:
        reason = (f"Chat mode needs the optional chat stack (`{exc.name}` isn't installed). "
                  "**Design** and **My Twin** work without it — "
                  "to enable chat: `pip install -r requirement.txt`.")
    except Exception as exc:
        reason = ("The Assistant needs a tool-calling model provider; run `agronaut setup` "
                  f"to pick one (couldn't start one: {exc}). **Design** and **My Twin** are "
                  "fully deterministic and keep working.")
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


def _render_chat_sidebar() -> None:
    if st.sidebar.button("Reset conversation", width="stretch"):
        agent = st.session_state.get("agent")
        if agent is not None:
            agent.reset("web", _web_user())
        st.session_state.messages = []
        _rerun()
    st.sidebar.caption(
        "Memory lasts for this browser session. The bot remembers your system as you talk "
        "(same brain as the Telegram bot)."
    )


def _add_message(role: str, content: str, image: bytes | None = None) -> None:
    # The image lives only in this browser session's state so the user can see what they
    # sent. It is never written to disk — PRIVACY.md promises photos are not retained.
    st.session_state.messages.append({"role": role, "content": content, "image": image})


def _render_messages() -> None:
    for msg in st.session_state.messages:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            if msg.get("image"):
                st.image(msg["image"], width=280)
            if msg.get("content"):
                st.markdown(msg["content"])


def _route_turn(agent, user_id: str, text: str, image_bytes: bytes | None) -> str:
    """Send the turn to the right agent seam. A photo goes to handle_image with the typed
    text as its caption — the same seam Telegram and WhatsApp use, so the observation guard
    and cited tools apply here too. Extracted from the widget code to be testable without a
    Streamlit script run."""
    if image_bytes:
        return agent.handle_image("web", user_id, image_bytes, caption=(text or None))
    return agent.handle_message("web", user_id, text)


def _read_chat_input() -> tuple[str, bytes | None]:
    """Collect this run's turn as (text, image_bytes). Empty text with no image means the
    user has not submitted anything yet."""
    if _CHAT_INPUT_ACCEPTS_FILES:
        value = st.chat_input("Describe your system, or attach a photo...",
                              accept_file=True, file_type=_PHOTO_TYPES)
        if not value:
            return "", None
        if isinstance(value, str):            # some versions return a plain string
            return value.strip(), None
        files = list(getattr(value, "files", None) or [])
        return (getattr(value, "text", "") or "").strip(), (files[0].getvalue() if files else None)

    upload = st.file_uploader("Attach a photo (optional)", type=_PHOTO_TYPES, key="chat_photo")
    text = st.chat_input("Describe your system, goal, or problem...")
    if not text:
        return "", None
    return text.strip(), (upload.getvalue() if upload is not None else None)


def _handle_turn(user_text: str, image_bytes: bytes | None = None) -> None:
    _add_message("user", user_text, image=image_bytes)
    agent = st.session_state.agent
    spinner = "Looking at your photo..." if image_bytes else "Thinking (running the numbers)..."
    try:
        with st.spinner(spinner):
            reply = _route_turn(agent, _web_user(), user_text, image_bytes)
    except Exception:
        reply = ("Something went wrong talking to the model. Your message wasn't lost, "
                 "please try again.")
    _add_message("assistant", reply)


MODES = ("Assistant", "Design", "My Twin", "Quality")
_WIDE = (TWIN_STUDIO, "Quality")


def _render_design() -> None:
    """The two deterministic design tools, side by side as tabs: both answer "design my
    system", one for a system you have chosen, one to choose it."""
    st.subheader("Design")
    size_tab, ratio_tab = st.tabs(["Size a system", "Find the best ratio"])
    with size_tab:
        render_calculator(heading=False)
    with ratio_tab:
        render_optimizer(heading=False)


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="💧",
        layout="wide" if st.session_state.get("app_mode") in _WIDE else "centered",
        initial_sidebar_state="expanded",
    )
    _ensure_session_state()

    # Open on the Assistant: the consultant is the front door, and it calls the same
    # engine. Without a working model, open on Design instead, which never needs one.
    if "app_mode" not in st.session_state:
        st.session_state.app_mode = "Assistant" if _agent_error() is None else "Design"

    st.sidebar.markdown(f"## {APP_TITLE}")
    st.sidebar.caption("Design, run and troubleshoot an aquaponics system.")
    mode = st.sidebar.radio(
        "Mode",
        MODES + ((TWIN_STUDIO,) if render_twin_studio else ()),
        key="app_mode",
        label_visibility="collapsed",
    )
    if st.session_state.get("agent_error"):
        st.sidebar.caption("Assistant is off: no working model. `agronaut setup` picks one.")

    if mode == "Design":
        _render_design()
        return
    if mode == TWIN_STUDIO and render_twin_studio:
        render_twin_studio()
        return
    if mode == "Quality":
        render_quality()
        return

    # My Twin is deterministic — it must render even when no LLM is configured, because
    # surviving model weather is the entire promise of the twin's command layer.
    if mode == "My Twin":
        reason = _agent_error()
        if "agent" not in st.session_state:
            st.warning(reason or "The twin is unavailable in this install.")
            return
        render_twin(brain=st.session_state.agent, user=_web_user())
        return

    # Assistant — the real consultative agent, degrading gracefully when the chat stack
    # or an LLM provider is missing (never a traceback in the UI).
    st.subheader("Assistant")
    reason = _agent_error()
    if reason:
        st.warning(reason)
        return

    _render_chat_sidebar()
    _render_messages()

    if not st.session_state.messages:
        st.info("Tell me what you're trying to do: design a system, optimize a ratio, "
                "or troubleshoot a problem. You can attach a photo of the plants, fish, "
                "or water and I'll take a look.")

    text, image_bytes = _read_chat_input()
    if text or image_bytes:
        _handle_turn(text, image_bytes)
        _rerun()


if __name__ == "__main__":
    main()
