"""Deterministic media panel with held workers; no paid requests or user-data writes."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use an isolated data directory"

import streamlit as st
from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn
from app.services import page2_service
from web.pages import page2

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
st.set_page_config(layout="wide")
page2._ensure_state()
if "fixture_seeded" not in st.session_state:
    st.session_state.fixture_seeded = True
    st.session_state.page2_turns = [Page2ConversationTurn(id="story", assistant_message="story")]
    st.session_state.page2_generated_media = [GeneratedImageRecord(
        id="old", provider="grok", prompt="old", image_data_base64=PNG,
        image_url_string="https://example.com/image.png", source_turn_id="story",
    )]
    st.session_state.page2_selected_media_id = "old"
    st.session_state.page2_image_prompt = "ready image prompt"
    st.session_state.fixture_saves = 0


def hold_start(task, fn):
    task._fn = fn
    task._started = True


def saved(record):
    st.session_state.fixture_saves += 1


page2._Page2OperationTask.start = hold_start
page2._upsert_record = lambda: None
page2._save_image_record = saved
page2.settings.set = lambda *args, **kwargs: None
page2.media_store.request_cache = lambda *args: None
page2.media_store.cache_generated = lambda record: record
page2.media_store.available = lambda record: True
page2.url_favorites.render_url_favorites = lambda *args: None
page2_service.load_context_from_settings = page2_service.default_context
page2_service.generate_image_prompt = lambda **kwargs: "generated image prompt"
page2_service.generate_image = lambda **kwargs: GeneratedImageRecord(provider="grok", prompt=kwargs["prompt"], image_data_base64=PNG)
page2_service.generate_video_prompt = lambda **kwargs: "generated video prompt"
page2_service.generate_video_from_image = lambda **kwargs: GeneratedImageRecord(provider="grok", prompt=kwargs["prompt"], media_kind=GeneratedMediaKind.VIDEO)

st.title("媒体按钮验证")
if st.button("完成当前任务", key="fixture_finish"):
    for task in page2._active_operations():
        if not task.is_done():
            task._run()
if st.button("当前任务失败", key="fixture_fail"):
    for task in page2._active_operations():
        if not task.is_done():
            task.error = RuntimeError("模拟失败")
            task._done.set()

page2._render_media_column()
page2._render_operation_queue_status()
st.code(json.dumps({
    "pending": [task.kind for task in page2._pending_operations()],
    "saved": st.session_state.fixture_saves,
}, ensure_ascii=False), language="json")
