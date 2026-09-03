import streamlit as st

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
        if st.button(
            "开始",
            key="home_start_button",
            icon=":material/play_arrow:",
            use_container_width=True,
        ):
            start_new_conversation(reset_settings=True)
            goto(start_page)

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
