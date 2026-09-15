"""Real Page2 UI with manually released media workers; never calls paid APIs."""
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use a temporary test data directory"

import requests
import streamlit as st
from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn
from app.services import page2_service
from web.pages import page2


def no_network(*args, **kwargs):
    raise AssertionError("Fixture forbids external HTTP requests")


requests.get = no_network
requests.post = no_network
st.set_page_config(layout="wide")
page2._ensure_state()
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
if "concurrency_seeded" not in st.session_state:
    st.session_state.concurrency_seeded = True
    st.session_state.fixture_gates = {kind: threading.Event() for kind in ("image", "video")}
    st.session_state.page2_turns = [Page2ConversationTurn(id="t1", user_message="开场", assistant_message="原有剧情")]
    st.session_state.page2_generated_media = [GeneratedImageRecord(
        id="old-image", provider="grok", prompt="原有图片", image_data_base64=PNG,
        source_turn_id="t1", video_prompt="角色向前走",
    )]
    st.session_state.page2_selected_media_id = "old-image"
    st.session_state.page2_image_prompt = "新图片提示词"
    st.session_state.page2_story_brain_enabled = False

# Capture plain values on the script thread. Workers never access Session State.
gates = dict(st.session_state.fixture_gates)


def generate_image(**kwargs):
    if not gates["image"].wait(90):
        raise TimeoutError("模拟图片等待超时")
    return GeneratedImageRecord(id="generated-image", provider="grok", prompt=kwargs["prompt"], image_data_base64=PNG)


def generate_video(**kwargs):
    if not gates["video"].wait(90):
        raise TimeoutError("模拟视频等待超时")
    return GeneratedImageRecord(id="generated-video", provider="grok", prompt=kwargs["prompt"],
        media_kind=GeneratedMediaKind.VIDEO,
        video_url_string="https://openrouter.ai/api/v1/videos/test-job/content?index=0")


def save_record():
    st.session_state.fixture_saved = {
        "turns": [turn.to_dict() for turn in st.session_state.page2_turns],
        "media": [item.id for item in st.session_state.page2_generated_media],
    }


page2_service.generate_image = generate_image
page2_service.generate_video_from_image = generate_video
page2_service.send_message = lambda **kwargs: "回复：" + kwargs["user_message"] + f"；已有 {len(kwargs['turns'])} 轮"
page2_service.generate_video_prompt = lambda **kwargs: "对应图片的视频动作"
page2_service.load_context_from_settings = page2_service.default_context
page2.OpenRouterConfig.api_key = lambda: "fixture-key"
page2._upsert_record = save_record
page2.url_favorites.render_url_favorites = lambda *args, **kwargs: None

st.title("并发交互验证")
with st.container(horizontal=True):
    if st.button("完成模拟图片"):
        gates["image"].set()
    if st.button("完成模拟视频"):
        gates["video"].set()

page2.prompt_draft_guard.begin()
page2.render()
st.caption("验证预览选择：" + str(st.session_state.page2_selected_media_id))
st.caption("验证视频输入：" + str(st.session_state.get("page2_video_source")))
st.code(json.dumps(st.session_state.get("fixture_saved", {}), ensure_ascii=False), language="json")
page2.prompt_draft_guard.render()
