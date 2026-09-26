"""Real media-import UI against isolated local files; no paid network calls."""
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use an isolated data directory"

import requests
import streamlit as st
from PIL import Image
from app.models import Page2ConversationTurn
from app.services import page2_service
from app.services import media_store
from web.pages import page2


def no_network(*args, **kwargs):
    raise AssertionError("No network in reference UI fixture")


requests.post = no_network
requests.get = no_network
page2._upsert_record = lambda: None
page2.settings.set = lambda *args, **kwargs: None
page2.url_favorites.render_url_favorites = lambda *args: None
page2_service.load_context_from_settings = page2_service.default_context
st.set_page_config(layout="wide")
page2._ensure_state()
if "seeded" not in st.session_state:
    st.session_state.seeded = True
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(output, format="PNG")
    image = media_store.import_image(output.getvalue(), "原有蓝图.png")
    image.provider = "grok"
    image.source_turn_id = "story"
    media_store.remember_image(image)
    st.session_state.page2_generated_media = [image]
    st.session_state.page2_turns = [Page2ConversationTurn(id="story", assistant_message="一个测试故事")]
    for name, color, permanent in (("其他聊天的永久图", "red", True), ("其他聊天的临时图", "green", False)):
        output = io.BytesIO()
        Image.new("RGB", (32, 24), color).save(output, format="PNG")
        media_store.import_image(output.getvalue(), name, permanent=permanent)
st.title("图片引用验证")
if st.button("固定本次任务的参考图"):
    st.session_state.frozen_refs = ["media://" + item for item in page2._manual_reference_ids()]
if st.button("保存第一张图片"):
    media_store.save(st.session_state.page2_generated_media[0])
    st.rerun()
page2._render_media_column()
references = ["media://" + item for item in page2._manual_reference_ids()]
references.extend(st.session_state.get(page2._reference_key() + "_urls", "").splitlines())
st.code(json.dumps({
    "references": references,
    "frozen": st.session_state.get("frozen_refs", []),
    "preview": st.session_state.page2_selected_media_id,
    "video": st.session_state.get("page2_video_source"),
    "library": [{"id": item.id, "name": item.prompt} for item in media_store.list_images()],
    "images": [{"id": item.id, "name": item.prompt,
                "saved": bool(media_store.info(item).get("saved")),
                "base64": item.image_data_base64} for item in st.session_state.page2_generated_media],
}, ensure_ascii=False), language="json")
