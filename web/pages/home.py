import uuid

import streamlit as st

from app.services import chat_records
from web.nav import goto
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

/* Push buttons mirrored from the Eassy-podcast 设置/历史记录 buttons. */
.st-key-home_start .stButton > button,
.st-key-home_secondary .stButton > button {
  min-height: 4.6rem;
  font-size: 1.02rem;
  font-weight: 560;
  letter-spacing: 0.02em;
  border: 1px solid rgba(23, 59, 89, 0.82) !important;
  border-radius: 14px !important;
  background: linear-gradient(180deg, #101822, #0a0f16) !important;
  color: #f5f7fa !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.12),
    0 5px 0 rgb(13, 31, 46),
    0 14px 26px rgba(0, 0, 0, 0.58) !important;
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease !important;
}

.st-key-home_start .stButton > button:hover,
.st-key-home_secondary .stButton > button:hover {
  border-color: #55b8ff !important;
  color: #f5f7fa !important;
  transform: translateY(-2px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.16),
    0 6px 0 rgb(32, 68, 94),
    0 17px 34px rgba(0, 0, 0, 0.58),
    0 0 24px rgba(85, 184, 255, 0.18) !important;
}

.st-key-home_start .stButton > button:active,
.st-key-home_secondary .stButton > button:active {
  transform: translateY(3px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.08),
    0 2px 0 rgb(12, 29, 44),
    0 7px 14px rgba(0, 0, 0, 0.58) !important;
}

.st-key-home_start .stButton > button:focus-visible,
.st-key-home_secondary .stButton > button:focus-visible {
  outline: 3px solid rgba(85, 184, 255, 0.18) !important;
  outline-offset: 3px;
}

@media (max-width: 640px) {
  .dnd-home-stage { height: 8vh; }
  .st-key-home_start .stButton > button,
  .st-key-home_secondary .stButton > button { min-height: 4rem; }
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
            if st.button(
                "开始",
                key="home_start_button",
                icon=":material/play_arrow:",
                use_container_width=True,
            ):
                start_new_conversation(reset_settings=True)
                goto(start_page)
        with continue_column:
            latest_record_id = _latest_visible_record_id()
            if st.button(
                "继续",
                key="home_continue_button",
                icon=":material/arrow_forward:",
                use_container_width=True,
                disabled=not latest_record_id,
            ):
                st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex
                goto(start_page, record_id=latest_record_id)

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
