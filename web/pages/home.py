import uuid

import streamlit as st

from app.services import chat_records
from web.nav import goto, navigate
from web.pages.page2 import start_new_conversation as start_new_page2_conversation


_HOME_NAV_CSS = """
<style>
section[data-testid="stMain"] .block-container {
  padding-top: 16px !important;
}

/* ===== Centered home navigation (Eassy-podcast home style) ===== */
.dnd-home-stage {
  height: clamp(2.5rem, 9vh, 5rem);
}

.st-key-home_start,
.st-key-home_secondary {
  max-width: 680px;
  margin-inline: auto;
}

/* ===== Liquid-glass buttons, mirrored from the sign-in page ===== */
section[data-testid="stMain"] .st-key-home_start .stButton > button,
section[data-testid="stMain"] .st-key-home_secondary .stButton > button {
  min-height: 4.4rem;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.06em;
  border-radius: 16px !important;
  color: #f8fafc !important;
  -webkit-backdrop-filter: blur(1px) !important;
  backdrop-filter: blur(1px) !important;
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease, background 160ms ease !important;
}

/* Primary pair (开始 / 继续) — same material as the 登录 button. */
section[data-testid="stMain"] .st-key-home_start .stButton > button {
  border: 1px solid rgba(191, 219, 254, 0.52) !important;
  background: linear-gradient(135deg, rgba(39,143,218,.30), rgba(118,87,219,.30)) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.24),
    0 12px 28px rgba(49, 117, 208, 0.24),
    0 0 24px rgba(118, 87, 219, 0.14) !important;
}

/* Secondary group (prompt / 记录 / APIkey / 生成语音) — same material as the 游客进入 button. */
section[data-testid="stMain"] .st-key-home_secondary .stButton > button {
  border: 1px solid rgba(148, 163, 184, 0.24) !important;
  background: rgba(15, 23, 42, 0.40) !important;
  color: rgba(226, 232, 240, 0.82) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04) !important;
}

section[data-testid="stMain"] .st-key-home_start .stButton > button:hover,
section[data-testid="stMain"] .st-key-home_secondary .stButton > button:hover {
  transform: translateY(-1px) !important;
}

section[data-testid="stMain"] .st-key-home_start .stButton > button:hover {
  border-color: rgba(224, 231, 255, 0.82) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    0 14px 34px rgba(49, 117, 208, 0.30),
    0 0 30px rgba(139, 92, 246, 0.20) !important;
}

section[data-testid="stMain"] .st-key-home_secondary .stButton > button:hover {
  border-color: rgba(148, 197, 255, 0.50) !important;
  background: rgba(30, 41, 59, 0.58) !important;
}

section[data-testid="stMain"] .st-key-home_start .stButton > button:active,
section[data-testid="stMain"] .st-key-home_secondary .stButton > button:active {
  transform: translateY(1px) !important;
}

section[data-testid="stMain"] .st-key-home_start .stButton > button:focus-visible,
section[data-testid="stMain"] .st-key-home_secondary .stButton > button:focus-visible {
  outline: 3px solid rgba(85, 184, 255, 0.30) !important;
  outline-offset: 3px;
}

section[data-testid="stMain"] .st-key-home_start .stButton > button:disabled,
section[data-testid="stMain"] .st-key-home_secondary .stButton > button:disabled {
  border-color: rgba(148, 163, 184, 0.14) !important;
  background: rgba(15, 23, 42, 0.24) !important;
  color: rgba(226, 232, 240, 0.34) !important;
  box-shadow: none !important;
}

@media (max-width: 640px) {
  .dnd-home-stage { height: 8vh; }
  section[data-testid="stMain"] .st-key-home_start .stButton > button,
  section[data-testid="stMain"] .st-key-home_secondary .stButton > button { min-height: 4rem; }
}
</style>
"""


def _latest_visible_record_id() -> str:
    """Return the newest chat record id visible to the current account (mirrors records page filtering)."""
    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    scope = "guest" if mode == "guest" else None
    items = chat_records.load_index_sorted(scope=scope)
    if not bool(st.session_state.get("hidden_unlocked", False)):
        items = [
            i
            for i in items
            if not bool(getattr(i, "hidden", False))
            and not str(i.title or "").strip().startswith("隐藏：")
        ]
    return items[0].id if items else ""


def _start_game(start_new_conversation, page: str) -> None:
    if start_new_conversation(reset_settings=True) is not False:
        navigate(page)


def _continue_game(page: str, record_id: str) -> None:
    st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex
    navigate(page, record_id=record_id)


def render(
    *,
    start_new_conversation=start_new_page2_conversation,
    start_page: str = "main",
    prompt_page: str = "settings",
):
    st.markdown(_HOME_NAV_CSS, unsafe_allow_html=True)
    st.markdown('<div class="dnd-home-stage" aria-hidden="true"></div>', unsafe_allow_html=True)

    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()

    with st.container(key="home_start"):
        start_column, continue_column = st.columns(2, gap="medium")
        with start_column:
            st.button(
                "开始",
                key="home_start_button",
                icon=":material/play_arrow:",
                use_container_width=True,
                on_click=_start_game,
                args=(start_new_conversation, start_page),
            )
        with continue_column:
            latest_record_id = _latest_visible_record_id()
            st.button(
                "继续",
                key="home_continue_button",
                icon=":material/arrow_forward:",
                use_container_width=True,
                disabled=not latest_record_id,
                on_click=_continue_game,
                args=(start_page, latest_record_id),
            )

    with st.container(key="home_secondary"):
        prompt_column, records_column = st.columns(2, gap="medium")
        with prompt_column:
            if st.button(
                "prompt",
                key="home_prompt_button",
                icon=":material/tune:",
                use_container_width=True,
            ):
                goto(prompt_page)
        with records_column:
            if st.button(
                "记录",
                key="home_records_button",
                icon=":material/history:",
                use_container_width=True,
            ):
                goto("records")
        if mode != "guest":
            if st.button(
                "APIkey",
                key="home_apikey_button",
                icon=":material/key:",
                use_container_width=True,
            ):
                goto("modelSettings")
            if st.button(
                "生成语音",
                key="home_speech_button",
                icon=":material/record_voice_over:",
                use_container_width=True,
            ):
                goto("speech")
