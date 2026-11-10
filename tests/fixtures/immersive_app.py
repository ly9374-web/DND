"""Real immersive workspace with isolated data and deterministic local model calls."""
import base64
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use an isolated data directory"
import requests
import streamlit as st
from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn
from app.services import page2_service, media_store
from app.ui.theme import apply_dark_mode
from web.pages import page2
from web.nav import nav_init
from web.components.immersive_workspace import prepare_workspace


def no_network(*args, **kwargs):
    raise AssertionError("Fixture forbids external HTTP requests")

requests.get = no_network
requests.post = no_network
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
nav_init(default_page="main")
prepare_workspace(active=not st.session_state.get("fixture_other_page", False))
apply_dark_mode(animate_background=False)
page2._ensure_state()
if "immersive_fixture_seeded" not in st.session_state:
    st.session_state.immersive_fixture_seeded = True
    st.session_state.page2_turns = [Page2ConversationTurn(
        id=f"turn-{i}", user_message=f"第 {i} 轮问题",
        assistant_message="测试故事。" + "海风吹过，灯塔在远处亮起。" * 70,
    ) for i in range(int(st.query_params.get("turns", os.environ.get("DND_UI_FIXTURE_TURNS", "20"))))]
    st.session_state.page2_story_brain_short = "Story Brain 实际记忆内容\n" * 80
    path = Path(os.environ["DND_UI_FIXTURE_IMAGE"])
    st.session_state.page2_generated_media = [GeneratedImageRecord(
        id="local-image", provider="grok", prompt="测试场景图片", image_data_base64=base64.b64encode(path.read_bytes()).decode(),
        source_turn_id="turn-7",
    )]
    st.session_state.page2_selected_media_id = "local-image"

page2_service.load_context_from_settings = page2_service.default_context
page2_service.send_message = lambda **kwargs: "正式消息接口的本地模拟回复"
page2_service.generate_image_prompt = lambda **kwargs: "本地模拟图片提示词"
page2_service.generate_image = lambda **kwargs: GeneratedImageRecord(
    provider=kwargs["provider"], prompt=kwargs["prompt"],
    image_data_base64=base64.b64encode(Path(os.environ["DND_UI_FIXTURE_IMAGE"]).read_bytes()).decode(),
)
page2_service.generate_video_prompt = lambda **kwargs: "本地模拟视频提示词"
if os.environ.get("DND_UI_FIXTURE_VIDEO"):
    media_store.download_video_bytes = lambda *args, **kwargs: Path(os.environ["DND_UI_FIXTURE_VIDEO"]).read_bytes()
    page2_service.generate_video_from_image = lambda **kwargs: GeneratedImageRecord(
        provider="fixture", prompt=kwargs["prompt"], media_kind=GeneratedMediaKind.VIDEO,
        video_url_string="https://fixture.invalid/local.mp4",
    )
page2._schedule_chat_followups = lambda *args, **kwargs: None
page2._has_dashscope_key = lambda: False
with st.sidebar:
    if st.button("切换测试页面"):
        st.session_state.fixture_other_page = not st.session_state.get("fixture_other_page", False)
        st.rerun()
page2.prompt_draft_guard.begin()
if st.session_state.get("fixture_other_page"):
    st.title("其他页面")
    if st.button("返回游戏", key="home_start_button"):
        st.session_state.fixture_other_page = False
        st.rerun()
else:
    page2.render_sidebar_context()
    page2.render()
page2.prompt_draft_guard.render()
