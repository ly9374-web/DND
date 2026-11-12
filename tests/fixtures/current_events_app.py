"""Current-game event editor with real isolated persistence and no external requests."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use isolated test data"
import requests
import streamlit as st
from app.config import CHAT_RECORDS_DIR
from app.models import ChatRecord, Page2ConversationTurn
from app.services import page2_service, system_prompts
from app.ui.theme import apply_dark_mode
from web.components.immersive_workspace import prepare_workspace
from web.nav import nav_init
from web.pages import page2

FINAL = "# 世界书\n世界事实\n# 用户角色\n用户事实"
PLAN = {"id": "game-plan", "title": "本局方案", "story": "当前游戏的背景", "enabled": True,
        "scene_events": [{"id": "site", "enabled": True, "location": "学校&门口", "patterns": ["学校&门口"], "event_content": "门口出现商队。"}],
        "time_events": [{"id": "used-time", "enabled": True, "title": "商队到达", "day": 3, "time": "14:30", "date": "9999-12-31", "event_content": "商队已抵达。"}]}

def no_network(*args, **kwargs):
    raise AssertionError("Fixture forbids external requests")
requests.get = requests.post = no_network
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
nav_init(default_page="main")
prepare_workspace(active=True)
apply_dark_mode(animate_background=False)
page2._ensure_state()
if not st.session_state.get("current_events_fixture_seeded"):
    st.session_state.current_events_fixture_seeded = True
    st.session_state.auth_mode = "user"
    state = system_prompts.load_state()
    if not state.records:
        system_prompts.save_prompt(state, record_id="", title="模板", prompt=FINAL)
        state = system_prompts.load_state()
    record = state.records[0]
    state.selected_record_id = record.id
    system_prompts.update_selected_prompt(state, FINAL)
    system_prompts.save_foreshadowing(state, record.id, {"enabled": True, "selected_plan_id": "template-plan",
        "plans": [{"id": "template-plan", "title": "原模板", "story": "模板背景", "scene_events": [], "time_events": []}]})
    st.session_state.page2_final_setting_id = record.id
    saved = sorted(CHAT_RECORDS_DIR.glob("record-*.json"))
    if saved:
        game = ChatRecord.from_dict(json.loads(saved[0].read_text()))
        st.session_state.page2_record_id = game.id
        st.session_state.page2_turns = game.turns
        st.session_state.page2_foreshadowing = game.foreshadowing
    else:
        st.session_state.page2_turns = [Page2ConversationTurn(id="t1", user_message="继续", assistant_message="商队已抵达。\n>当前时间：第3天 15:00，当前地点：学校", special_events=[{"id": "used-time", "event_content": "商队已抵达。"}])]
        st.session_state.page2_foreshadowing = PLAN

def context():
    ctx = page2_service.default_context()
    ctx.system_prompt = FINAL
    return ctx
page2_service.load_context_from_settings = context
page2._has_dashscope_key = lambda: False
page2.prompt_draft_guard.begin()
page2.render_sidebar_context()
page2.render()
page2.prompt_draft_guard.render()
