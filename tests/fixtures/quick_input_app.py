"""Local browser harness: real composer bridge and management UI, no model calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
from app.services import quick_inputs
from web.pages.page2 import _render_empty_space_submit_bridge
from web.pages.settings import _render_quick_inputs

st.set_page_config(layout="wide")
if st.query_params.get("full") == "1":
    from app.ui.theme import apply_dark_mode
    from app.models import Page2ConversationTurn
    from web.pages.page2 import _ensure_state, render
    apply_dark_mode()
    _ensure_state()
    if not st.session_state.page2_turns:
        st.session_state.page2_turns = [Page2ConversationTurn(
            user_message="查看房间", assistant_message="这是用于检查输入框位置的已有聊天。",
        )]
    if st.query_params.get("resend") == "1":
        import time
        from app.services import page2_service
        failed = st.query_params.get("fail") == "1"

        def fake_send_message(**kwargs):
            time.sleep(1.5)
            if failed:
                raise RuntimeError("测试网络失败")
            return "重发的新回复：" + kwargs["user_message"]

        page2_service.send_message = fake_send_message
        st.session_state.page2_story_brain_enabled = False
    render()
    st.stop()
st.title("快捷输入交互验证")
st.session_state.hidden_unlocked = st.checkbox("私密账号")
busy = st.checkbox("等待回复")
view = st.radio("页面", ["聊天", "管理"])
if st.session_state.hidden_unlocked:
    if not any(item.title == "私密测试" for item in quick_inputs.load_quick_inputs(True)):
        quick_inputs.save_quick_input("", "私密测试", "私密测试内容", hidden_space=True)
if view == "管理":
    _render_quick_inputs(False)
else:
    with st.container(key="page2_chat_composer"):
        text = st.chat_input("输入消息", key="page2_chat_input")
        _render_empty_space_submit_bridge(send_disabled=busy)
    if text is not None:
        st.session_state["test_sent"] = st.session_state.get("test_sent", []) + [text]
    st.write("发送次数：", len(st.session_state.get("test_sent", [])))
    if st.button("模拟后台刷新"):
        st.rerun()
    st.write(st.session_state.get("test_sent", []))
