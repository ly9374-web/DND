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

/* Kill default paddings/gaps and center the glass panel in the viewport. */
[data-testid="stMainBlockContainer"],
section.main > div.block-container {
  padding: 20px !important;
  height: 100dvh !important;
  max-height: 100dvh !important;
  overflow: hidden !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}

/* Ensure the root vertical stack is centered and does not add surprise spacing. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"],
section.main > div.block-container > [data-testid="stVerticalBlock"] {
  width: 100%;
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 0 !important;
  margin: 0 auto !important;
}

/* ===== Sign-in panel: pure transparent, only refracts the background ===== */
.st-key-signin_panel {
  width: min(460px, calc(100vw - 32px)) !important;
  margin-inline: auto !important;
  padding: 32px 34px 34px !important;
  border: 1px solid rgba(255, 255, 255, 0.16) !important;
  border-radius: 28px !important;
  background: transparent !important;
  -webkit-backdrop-filter: blur(1px) !important;
  backdrop-filter: blur(1px) !important;
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.30) !important;
}
.st-key-signin_panel > [data-testid="stVerticalBlock"] {
  gap: 16px !important;
}

.dnd-signin-heading {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  margin: 0 0 8px;
}
.dnd-signin-mark {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  margin-bottom: 14px;
  border: 1px solid rgba(191, 219, 254, 0.52);
  border-radius: 50%;
  background: linear-gradient(145deg, #55b8ff, #8b5cf6);
  color: #ffffff;
  font-size: 27px;
  line-height: 1;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.36),
    0 10px 30px rgba(85, 184, 255, 0.30),
    0 0 24px rgba(139, 92, 246, 0.24);
}
.dnd-signin-title {
  color: #f8fafc;
  font-size: 38px;
  font-weight: 780;
  line-height: 1.05;
  letter-spacing: 0.08em;
}
.dnd-signin-subtitle {
  margin-top: 9px;
  color: rgba(226, 232, 240, 0.68);
  font-size: 14px;
  letter-spacing: 0.04em;
}

/* ===== Passcode input ===== */
.st-key-signin_passcode {
  width: 100% !important;
  max-width: none !important;
  margin: 0 !important;
}
.st-key-signin_passcode label,
.st-key-signin_passcode label p {
  color: rgba(241, 245, 249, 0.88) !important;
  font-size: 13px !important;
  font-weight: 650 !important;
  letter-spacing: 0.04em;
}
.st-key-signin_passcode [data-baseweb="input"] {
  min-height: 54px !important;
  border: 1px solid rgba(148, 163, 184, 0.20) !important;
  border-radius: 16px !important;
  background: rgba(5, 10, 19, 0.58) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04) !important;
  transition: border-color 180ms ease, box-shadow 180ms ease, background 180ms ease;
}
.st-key-signin_passcode [data-baseweb="input"]:focus-within {
  border-color: rgba(96, 165, 250, 0.78) !important;
  background: rgba(6, 12, 24, 0.76) !important;
  box-shadow:
    0 0 0 3px rgba(85, 184, 255, 0.12),
    0 0 22px rgba(85, 184, 255, 0.12) !important;
}
.st-key-signin_passcode input {
  min-height: 52px !important;
  background: transparent !important;
  color: #f8fafc !important;
  font-size: 17px !important;
  padding-inline: 16px !important;
}
.st-key-signin_passcode input::placeholder {
  color: rgba(203, 213, 225, 0.40) !important;
}

/* ===== Primary and secondary actions ===== */
.st-key-signin_login,
.st-key-signin_guest {
  width: 100% !important;
}
.st-key-signin_login button,
.st-key-signin_guest button {
  width: 100% !important;
  min-height: 52px !important;
  border-radius: 16px !important;
  color: #f8fafc !important;
  font-size: 16px !important;
  font-weight: 700 !important;
  letter-spacing: 0.06em;
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease !important;
}
.st-key-signin_login button {
  border: 1px solid rgba(191, 219, 254, 0.52) !important;
  background: linear-gradient(135deg, #278fda, #7657db) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.24),
    0 12px 28px rgba(49, 117, 208, 0.24),
    0 0 24px rgba(118, 87, 219, 0.14) !important;
}
.st-key-signin_guest button {
  border: 1px solid rgba(148, 163, 184, 0.24) !important;
  background: rgba(15, 23, 42, 0.40) !important;
  color: rgba(226, 232, 240, 0.82) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04) !important;
}
.st-key-signin_login button:hover,
.st-key-signin_guest button:hover {
  transform: translateY(-1px) !important;
}
.st-key-signin_login button:hover {
  border-color: rgba(224, 231, 255, 0.82) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    0 14px 34px rgba(49, 117, 208, 0.30),
    0 0 30px rgba(139, 92, 246, 0.20) !important;
}
.st-key-signin_guest button:hover {
  border-color: rgba(148, 197, 255, 0.50) !important;
  background: rgba(30, 41, 59, 0.58) !important;
}

@media (max-width: 560px) {
  .st-key-signin_panel {
    padding: 26px 22px 28px !important;
    border-radius: 24px !important;
  }
  .dnd-signin-title { font-size: 34px; }
}
</style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="signin_panel"):
        st.markdown(
            """
            <div class="dnd-signin-heading">
              <div class="dnd-signin-mark" aria-hidden="true">✦</div>
              <div class="dnd-signin-title">DND</div>
              <div class="dnd-signin-subtitle">输入口令，开启你的冒险</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        code = st.text_input(
            label="访问口令",
            value="",
            placeholder="请输入访问口令",
            type="password",
            key="signin_passcode",
        )
        login = st.button(
            "登录",
            use_container_width=True,
            type="primary",
            key="signin_login",
        )
        guest = st.button(
            "游客进入",
            use_container_width=True,
            key="signin_guest",
        )

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
