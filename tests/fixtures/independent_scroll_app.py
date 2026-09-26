"""Real Page2 layout with long local content and no external model calls."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use an isolated data directory"

import requests
import streamlit as st

from app.models import GeneratedImageRecord, Page2ConversationTurn
from app.services import page2_service
from app.ui.theme import apply_dark_mode
from web.pages import page2


def no_network(*args, **kwargs):
    raise AssertionError("Fixture forbids external HTTP requests")


requests.get = no_network
requests.post = no_network
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
apply_dark_mode(animate_background=False)
page2._ensure_state()
if "scroll_fixture_seeded" not in st.session_state:
    st.session_state.scroll_fixture_seeded = True
    st.session_state.page2_turns = [
        Page2ConversationTurn(
            id=f"turn-{index}", user_message=f"第 {index} 轮问题",
            assistant_message=(f"第 {index} 轮故事。" + "沿着道路继续探索。" * 50),
        )
        for index in range(20)
    ]
    st.session_state.page2_story_brain_enabled = True
    st.session_state.page2_image_prompt = "\n".join(f"图片提示词第 {i} 行" for i in range(60))
    st.session_state.page2_generated_media = [GeneratedImageRecord(
        id="scroll-image", provider="grok", prompt="测试图片",
        image_data_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        source_turn_id="turn-19", video_prompt="向前行走",
    )]
    st.session_state.page2_selected_media_id = "scroll-image"

page2_service.load_context_from_settings = page2_service.default_context
page2_service.send_message = lambda **kwargs: "本地模拟回复"
page2._schedule_chat_followups = lambda *args, **kwargs: None
page2._upsert_record = lambda: None
page2._current_short_story_brain = lambda: "Story Brain 测试内容\n" * 30

with st.sidebar:
    if st.button("切换测试页面"):
        st.session_state.scroll_fixture_other_page = not st.session_state.get("scroll_fixture_other_page", False)

page2.prompt_draft_guard.begin()
if st.session_state.get("scroll_fixture_other_page"):
    st.title("其他页面")
    for index in range(60):
        st.write(f"普通页面第 {index} 行")
else:
    page2.render()
page2.prompt_draft_guard.render()
