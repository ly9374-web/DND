"""Real Prompt UI with deterministic model responses and isolated test storage."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use isolated test data"

import streamlit as st
from app.services import foreshadow_prompts, page2_service
from web.pages import settings


def model_reply(**kwargs):
    system = kwargs["system_prompt"]
    calls = st.session_state.setdefault("test_model_calls", [])
    if system == foreshadow_prompts.QUESTIONS_SYSTEM:
        assert kwargs["ctx"].selected_chat_model == "deepseekFlash"
        assert kwargs["thinking_enabled"] is False
        calls.append("问卷")
        return "\n".join(
            f"{i + 1}. 第{i + 1}项世界发展方向？" + "".join(f"\n{key}. {key}方向" for key in "ABCD")
            for i in range(8)
        )
    if system == foreshadow_prompts.EVENTS_SYSTEM:
        calls.append("背景与事件")
        if "测试生成失败" in kwargs["user_message"]:
            return "{}"
        return json.dumps({"story": "第1天14:00，A回到学校403宿舍。第2天，收粮队抵达。地下室信件与收粮队有关。", "scene_events": [{"location": "A家地下室", "patterns": ["A家地下室", "小A家的地下室", "A住所地下室", "A宅地下室"], "event_content": f"秘密{i+1}，主角未发现时不直接展示。", "enabled": True} for i in range(5)],
                           "time_events": [{"title": f"时间事件{i+1}", "day": i % 4 + 1, "time": "14:00", "event_content": f"时间事件{i+1}：A回到403宿舍，主角不知情时只记录记忆。", "enabled": True} for i in range(5)]}, ensure_ascii=False)
    raise AssertionError("Unexpected model call")


page2_service._send_with_current_chat_model = model_reply
st.set_page_config(layout="wide")
st.session_state.setdefault("auth_mode", "user")
settings.render()
st.caption("模型调用记录：" + " → ".join(st.session_state.get("test_model_calls", [])))
