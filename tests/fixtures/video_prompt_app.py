"""Real video prompt UI with deterministic model replies; no network or user data writes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
from app.models import GeneratedImageRecord, Page2ConversationTurn
from app.services import page2_service
from app.video_models import WAN_MODEL
from web.pages import page2

st.set_page_config(layout="wide")
page2._ensure_state()
if "video_fixture_seeded" not in st.session_state:
    st.session_state.video_fixture_seeded = True
    st.session_state.page2_turns = [Page2ConversationTurn(
        id=f"t{i}", user_message=f"用户{i}", assistant_message=f"故事{i}",
    ) for i in range(1, 9)]
    st.session_state.page2_generated_media = [
        GeneratedImageRecord(id="image8", provider="grok", prompt="蓝衣女子站在左侧",
                             source_turn_id="t8", previous_source_turn_id="t7"),
        GeneratedImageRecord(id="image7", provider="grok", prompt="红衣女子站在右侧",
                             source_turn_id="t7", previous_source_turn_id="t6"),
        GeneratedImageRecord(id="legacy", provider="grok", prompt="缺少来源的旧图片"),
    ]
    st.session_state.page2_selected_media_id = "image8"


def save_record():
    st.session_state.video_fixture_saved = json.dumps(
        [item.to_dict() for item in st.session_state.page2_generated_media], ensure_ascii=False,
    )


# Background model calls must not read session state.
failed = st.query_params.get("fail") == "1"
def send(**kwargs):
    if failed:
        raise RuntimeError("模拟网络失败")
    return "蓝衣女子抬手微笑，转身看向身旁的人。" + kwargs["user_message"].split("本轮故事内容：\n")[-1]


page2_service._send_with_current_chat_model = send
page2_service.load_context_from_settings = page2_service.default_context
page2._upsert_record = save_record
page2_service.generate_video_from_image = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("测试禁止真实生视频"))

st.title("视频 prompt 交互验证")
with st.container(horizontal=True):
    if st.button("编辑第八轮", disabled=page2.page2_operation_queue_busy()):
        st.session_state.page2_turns[7].assistant_message = "第八轮编辑后的故事"
    if st.button("撤回末轮", disabled=page2.page2_operation_queue_busy()):
        page2._undo_last_turn()
    if st.button("重新载入已保存图片", disabled=page2.page2_operation_queue_busy()):
        st.session_state.page2_generated_media = [GeneratedImageRecord.from_dict(item)
            for item in json.loads(st.session_state.video_fixture_saved)]

page2._render_video_prompt_controls(st.session_state.page2_generated_media, WAN_MODEL)
page2._render_operation_queue_status()
