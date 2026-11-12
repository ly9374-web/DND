"""Immersive generation controls with delayed local replies and isolated storage."""
import base64
import io
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use isolated test data"

import requests
import streamlit as st
from PIL import Image
from app.config import settings as store, AppStorageKeys
from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn
from app.services import page2_service, system_prompts, foreshadowing
from app.ui.theme import apply_dark_mode
from web.pages import page2
from web.nav import nav_init
from web.components.immersive_workspace import prepare_workspace
from web.components import foreshadow_editor

FINAL = "1. 背景介绍\n固定背景指令\n2. 世界书\n世界事实\n3. 用户角色\n用户设定\n4. 其他角色\n同伴设定\n5. DM 的核心任务\n固定DM指令\n6. 回复格式要求\n固定格式指令"
image_bytes = io.BytesIO()
Image.new("RGB", (400, 300), "#24384a").save(image_bytes, format="PNG")
IMAGE = base64.b64encode(image_bytes.getvalue()).decode()

def no_network(*args, **kwargs):
    raise AssertionError("Fixture forbids external requests")
requests.get = requests.post = no_network
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
nav_init(default_page="main")
is_settings = st.query_params.get("view") == "settings"
prepare_workspace(active=not is_settings)
apply_dark_mode(animate_background=False)
page2._ensure_state()
if "generation_fixture_seeded" not in st.session_state:
    st.session_state.generation_fixture_seeded = True
    state = system_prompts.load_state()
    if not state.records:
        system_prompts.save_prompt(state, record_id="", title="事件测试", prompt=FINAL)
        state = system_prompts.load_state()
    record = state.records[0]
    state.selected_record_id = record.id
    system_prompts.update_selected_prompt(state, FINAL)
    st.session_state.page2_final_setting_id = record.id
    st.session_state.auth_mode = "user"
    st.session_state.page2_turns = [Page2ConversationTurn(id="t1", user_message="环顾四周", assistant_message="街道上人来人往。\n>当前时间：第8天 14:00，当前地点：学校")]
    if st.query_params.get("empty") == "1":
        st.session_state.page2_turns = []
    st.session_state.page2_story_brain_short = "点击时的记忆：用户仍在学校。"
    st.session_state.page2_generated_media = [GeneratedImageRecord(id="input", provider="grok", prompt="学校外的街道", source_turn_id="t1", image_data_base64=IMAGE)]
    st.session_state.page2_selected_media_id = "input"
    st.session_state.page2_foreshadowing = {"id": "old", "enabled": True}


def context():
    ctx = page2_service.default_context()
    ctx.system_prompt = FINAL
    return ctx


def events(**kwargs):
    assert "固定背景指令" not in kwargs["user_message"]
    assert "固定DM指令" not in kwargs["user_message"]
    assert "固定格式指令" not in kwargs["user_message"]
    time.sleep(6)
    day = 1 if "尚未开局" in kwargs["user_message"] else 8
    return json.dumps({"story": "新故事：商队将在学校附近停留。",
        "scene_events": [{"location": "学校&门口", "patterns": ["学校&门口", "校门", "学校&入口", "校园&大门"], "event_content": "商队抵达。"} for _ in range(5)],
        "time_events": [{"title": f"事件{i}", "day": day, "time": "15:00", "event_content": "商队卸货。"} for i in range(5)]}, ensure_ascii=False)


def image_prompt(**kwargs):
    time.sleep(2)
    return "全新图片prompt"


def image(**kwargs):
    time.sleep(2)
    return GeneratedImageRecord(provider="grok", prompt=kwargs["prompt"], image_data_base64=IMAGE)


def video_prompt(**kwargs):
    time.sleep(2)
    return "全新视频prompt"


def video(**kwargs):
    time.sleep(2)
    return GeneratedImageRecord(provider="grok", prompt=kwargs["prompt"], media_kind=GeneratedMediaKind.VIDEO,
                                source_image_id=kwargs["source_record"].id, video_url_string="https://fixture.invalid/video.mp4")

page2_service.load_context_from_settings = context
page2_service._send_with_current_chat_model = events
page2_service.send_message = lambda **kwargs: "继续聊天成功。\n>当前时间：第8天 14:10，当前地点：学校"
page2_service.generate_image_prompt = image_prompt
page2_service.generate_image = image
page2_service.generate_video_prompt = video_prompt
page2_service.generate_video_from_image = video
page2._schedule_chat_followups = lambda *args, **kwargs: None
page2._has_dashscope_key = lambda: False
page2.media_store.cache_generated = lambda record: record
page2.media_store.available = lambda record: True
page2.media_store.request_cache = lambda *args: None
page2.media_store.remember_image = lambda *args: None
page2._render_media_item = lambda *args, **kwargs: None

page2.prompt_draft_guard.begin()
if is_settings:
    state = system_prompts.load_state()
    record = system_prompts.get_record(state, st.session_state.page2_final_setting_id)
    foreshadow_editor.render_header(state, record, "fixture_final_prompt")
    foreshadow_editor.render_editor(state, record)
else:
    page2.render_sidebar_context()
    page2.render()
page2.prompt_draft_guard.render()
