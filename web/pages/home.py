import streamlit as st

from web.nav import goto
from web.pages.page2 import start_new_conversation as start_new_page2_conversation


def render(
    *,
    start_new_conversation=start_new_page2_conversation,
    start_page: str = "main",
    prompt_page: str = "settings",
):
    st.markdown(
        """
<style>
section[data-testid="stMain"] .block-container {
  padding-top: 16px !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )
    st.title("首页")
    st.caption("选择一个功能进入。")

    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("开始", use_container_width=True):
            start_new_conversation(reset_settings=True)
            goto(start_page)
        if st.button("prompt", use_container_width=True):
            goto(prompt_page)
    with col2:
        if st.button("记录", use_container_width=True):
            goto("records")
        if mode != "guest":
            if st.button("APIkey", use_container_width=True):
                goto("modelSettings")
