"""Real Prompt editors and bridge, using an explicitly isolated data directory."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use a temporary test data directory"

import streamlit as st
from web import prompt_draft_guard as guard
from web.pages import settings
from app.services import quick_inputs
from web.nav import goto, nav_init

st.set_page_config(layout="wide")
nav_init("settings")
st.session_state.setdefault("auth_mode", "user")
st.session_state.setdefault("user_id", "draft-test")
if not quick_inputs.load_quick_inputs():
    quick_inputs.save_quick_input("", "切换记录用条目", "原始正文")
guard.begin()
if st.session_state.nav_page != "home":
    guard.navigation("sidebar_back")
    if st.sidebar.button("返回", key="sidebar_back"):
        goto("home")

if st.session_state.nav_page == "settings":
    settings.render()
elif st.session_state.nav_page == "home":
    st.title("测试首页")
    if st.button("Prompt", key="test_prompt"):
        goto("settings")
    if st.button("自动保存页面"):
        goto("autosave")
else:
    st.title("自动保存测试")
    st.session_state.setdefault("auto_saved", "初始内容")
    guard.protect("auto_prompt", st.session_state.auto_saved, "自动保存 prompt", requires_commit=False)
    def autosave():
        st.session_state.auto_saved = st.session_state.auto_prompt
    st.text_area("自动保存 prompt", value=st.session_state.auto_saved, key="auto_prompt", on_change=autosave)
    st.selectbox("普通选项", ["选项一", "选项二"])
    if st.button("普通操作"):
        st.success("普通操作完成")
guard.render()
