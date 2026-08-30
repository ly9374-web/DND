from __future__ import annotations

import streamlit as st

from web.nav import goto
from app.storage import ChatRecordStore
import time


_PASSCODE_FULL = "1369"      # 完整账户：登录后隐藏空间可见
_PASSCODE_LIMITED = "1234"   # 受限账户：登录后隐藏空间不可见


def _toast_error(message: str):
    toast = getattr(st, "toast", None)
    if callable(toast):
        toast(message, icon="⚠️")
    else:
        st.error(message)


def render():
    st.markdown(
        """
<style>
/* ===== Force a single-viewport layout (no vertical scroll) ===== */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  height: 100dvh !important;
  max-height: 100dvh !important;
  overflow: hidden !important;
}

/* Kill default paddings/gaps and use flex centering on the main block container */
[data-testid="stMainBlockContainer"],
section.main > div.block-container {
  padding: 0 !important;
  height: 100dvh !important;
  max-height: 100dvh !important;
  overflow: hidden !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}

/* Ensure the root vertical stack is centered and does not add surprise spacing */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"],
section.main > div.block-container > [data-testid="stVerticalBlock"] {
  width: min(980px, 92vw);
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 26px !important;
  margin: 0 auto !important;
}

/* ===== Centered sign-in layout ===== */
.ly-signin-card {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 26px;
  padding: 24px 16px;
  text-align: center;
}
.ly-signin-title {
  width: 100%;
  text-align: center;
  font-size: 64px;
  font-weight: 800;
  letter-spacing: 2px;
  margin: 0;
  color: #e5e7eb;
}

/* ===== Input pill ===== */
.ly-input {
  width: min(720px, 92vw);
}
.ly-input [data-testid="stTextInput"] {
  width: 100%;
}
.ly-input input {
  height: 64px !important;
  border-radius: 999px !important;
  border: 1px solid rgba(255,255,255,0.10) !important;
  background: #f8fafc !important;
  color: #111827 !important;
  font-size: 22px !important;
  padding: 0 24px !important;
}
.ly-input input::placeholder {
  color: rgba(17, 24, 39, 0.45) !important;
}

/* ===== Buttons row (two buttons, centered, near screenshot width) ===== */
.ly-btn-row {
  width: min(760px, 92vw);
}
.ly-btn-row [data-testid="stHorizontalBlock"] {
  justify-content: center !important;
  gap: 28px !important;
}
.ly-btn-row [data-testid="column"] {
  max-width: 320px !important;
}
.ly-btn [data-testid="stButton"] button {
  height: 52px !important;
  border-radius: 999px !important;
  background: #1f2937 !important;
  color: #e5e7eb !important;
  border: 1px solid rgba(255,255,255,0.10) !important;
  font-size: 18px !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="ly-signin-card">', unsafe_allow_html=True)
    st.markdown('<div class="ly-signin-title">DND</div>', unsafe_allow_html=True)

    st.markdown('<div class="ly-input">', unsafe_allow_html=True)
    code = st.text_input(
        label="口令",
        value="",
        placeholder="",
        label_visibility="collapsed",
        key="signin_passcode",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="ly-btn-row">', unsafe_allow_html=True)
    b1, b2 = st.columns([1, 1], gap="large")
    with b1:
        st.markdown('<div class="ly-btn">', unsafe_allow_html=True)
        login = st.button("登陆", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with b2:
        st.markdown('<div class="ly-btn">', unsafe_allow_html=True)
        guest = st.button("游客登录", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    if guest:
        st.session_state.auth_ok = True
        st.session_state.auth_mode = "guest"
        st.session_state.user_id = ""
        st.session_state.hidden_unlocked = False
        st.session_state.guest_expires_at = time.time() + 10 * 60
        ChatRecordStore.clear_scope(ChatRecordStore.GUEST_SCOPE)
        for key in [
            "page2_record_id",
            "page2_loaded_record_id",
            "page2_turns",
            "page2_generated_media",
            "page2_selected_media_id",
            "page2_image_prompt",
            "page2_image_prompt_mode",
            "page2_image_prompt_subject",
        ]:
            if key in st.session_state:
                st.session_state.pop(key, None)
        goto("home", push_history=False)

    if login:
        entered = str(code or "").strip()
        if entered in (_PASSCODE_FULL, _PASSCODE_LIMITED):
            st.session_state.auth_ok = True
            st.session_state.auth_mode = "user"
            st.session_state.user_id = "1"
            st.session_state.hidden_unlocked = entered == _PASSCODE_FULL
            st.session_state.pop("guest_expires_at", None)
            goto("home", push_history=False)
        else:
            _toast_error("密码错误")
