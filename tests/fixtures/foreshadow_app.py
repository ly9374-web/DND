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


calls = st.session_state.setdefault("test_model_calls", [])

def model_reply(**kwargs):
    system = kwargs["system_prompt"]
    if system == foreshadow_prompts.EVENTS_SYSTEM:
        calls.append("背景与事件")
        if "测试生成失败" in kwargs["user_message"]:
            return "{}"
        target = "A家地下室"
        time_events = [
            {"title": f"口信{i}", "day": 1, "time": clock, "leads_to_location": target,
             "event_content": "邻居给主角留了口信，请主角去A家地下室辨认一封信。"}
            for i, clock in enumerate(("00:30", "01:30"))
        ] + [
            {"title": f"时间事件{i}", "day": 1, "time": clock, "event_content": "收粮队清点物资。"}
            for i, clock in enumerate(("03:00", "04:00", "06:00"))
        ]
        return json.dumps({"story": "第1天 03:00，A回到学校403宿舍。第1天 06:00，收粮队抵达。地下室信件与收粮队有关。", "scene_events": [{"location": target, "patterns": [target, "小A家的地下室", "A住所地下室", "A宅地下室"], "event_content": f"秘密{i+1}，主角未发现时不直接展示。", "enabled": True} for i in range(5)],
                           "time_events": time_events}, ensure_ascii=False)
    raise AssertionError("Unexpected model call")


page2_service._send_with_current_chat_model = model_reply
st.set_page_config(layout="wide")
st.session_state.setdefault("auth_mode", "user")
settings.render()
st.caption("模型调用记录：" + " → ".join(st.session_state.get("test_model_calls", [])))
