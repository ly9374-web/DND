import time

import streamlit as st

from app.config import APP_TITLE, DATA_DIR
from app import log_store
from app.ui.theme import apply_dark_mode
from app.services import media_store
from web.nav import back, goto, nav_init, nav_state
from web import prompt_draft_guard
from web.streamlit_log_filters import install_stale_fragment_filter
from web.pages.home import render as render_home
from web.pages.model_settings import render as render_model_settings
from web.pages.speech import render as render_speech
from web.pages.page2 import (
    page2_operation_queue_busy,
    render as render_page2,
    render_sidebar_context as render_page2_sidebar_context,
)
from web.pages.records import render as render_records
from web.pages.settings import render as render_settings
from web.pages.signin_page import (
    _reset_chat_session_state as reset_chat_session_state,
)
from web.pages.signin_page import render as render_signin


install_stale_fragment_filter()


PAGES = {
    "signin": ("登录", render_signin),
    "home": ("首页", render_home),
    "main": ("开始", render_page2),
    "settings": ("设置", render_settings),
    "records": ("记录", render_records),
    "modelSettings": ("APIkey", render_model_settings),
    "speech": ("生成语音", render_speech),
}

_GUEST_COUNTDOWN_CSS = """
<style>
.ly-guest-countdown {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 10px;
  z-index: 9999;
  display: flex;
  justify-content: center;
  pointer-events: none;
}
.ly-guest-countdown > div {
  pointer-events: none;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(17, 24, 39, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: #e5e7eb;
  font-size: 14px;
}
</style>
"""


@st.fragment(run_every=1)
def _render_guest_countdown():
    expires_at = float(st.session_state.get("guest_expires_at") or 0.0)
    remaining = int(expires_at - time.time())
    if remaining <= 0:
        st.session_state.auth_ok = False
        st.session_state.auth_mode = ""
        st.session_state.user_id = ""
        st.session_state.hidden_unlocked = False
        st.session_state.pop("guest_expires_at", None)
        reset_chat_session_state()
        st.warning("游客试用已结束，请重新登录。")
        goto("signin", push_history=False)
        st.rerun()

    mm = remaining // 60
    ss = remaining % 60
    clock = f"{mm:02d}:{ss:02d}"

    st.markdown(_GUEST_COUNTDOWN_CSS, unsafe_allow_html=True)
    st.markdown(
        f'<div class="ly-guest-countdown"><div>此次游客试用还剩 {clock}</div></div>',
        unsafe_allow_html=True,
    )


def _render_sidebar():
    current = nav_state().page
    can_go_back = current == "home" or bool(nav_state().history)
    if current != "home" and (not nav_state().history or nav_state().history[-1][0] != current):
        prompt_draft_guard.navigation("sidebar_back")
    if st.sidebar.button(
        "返回",
        key="sidebar_back",
        use_container_width=True,
        disabled=not can_go_back or (current == "main" and page2_operation_queue_busy()),
    ):
        if current == "home":
            st.session_state.auth_ok = False
            st.session_state.auth_mode = ""
            st.session_state.user_id = ""
            st.session_state.hidden_unlocked = False
            st.session_state.pop("guest_expires_at", None)
            st.session_state.nav_history = []
            reset_chat_session_state()
            goto("signin", push_history=False)
        else:
            back()

    if current == "main":
        render_page2_sidebar_context()


def main():
    st.set_page_config(
        page_title=APP_TITLE,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    nav_init(default_page="signin")
    log_store.start_maintenance(DATA_DIR / "logs")
    media_store.start_maintenance()

    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False
    if "auth_mode" not in st.session_state:
        st.session_state.auth_mode = ""
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""
    if "hidden_unlocked" not in st.session_state:
        st.session_state.hidden_unlocked = False
    if bool(st.session_state.auth_ok) and not str(st.session_state.auth_mode or "").strip():
        st.session_state.auth_mode = "user"
        st.session_state.user_id = st.session_state.user_id or "1"

    if nav_state().page != "signin" and not bool(st.session_state.auth_ok):
        goto("signin", push_history=False)

    if (
        bool(st.session_state.auth_ok)
        and str(st.session_state.auth_mode or "").strip().lower() == "guest"
        and nav_state().page != "signin"
    ):
        _render_guest_countdown()

    if nav_state().page in {"modelSettings", "speech"} and str(st.session_state.auth_mode or "").strip().lower() == "guest":
        st.warning("游客模式无法访问此页面。")
        goto("home", push_history=False)

    page = nav_state().page
    prompt_draft_guard.begin()
    apply_dark_mode(animate_background=page in {"signin", "home"})
    if page != "signin":
        _render_sidebar()

    _, render = PAGES.get(page, PAGES["home"])
    render()
    prompt_draft_guard.render()


if __name__ == "__main__":
    main()
