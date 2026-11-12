"""Editable modal for the current game's event snapshot."""
from copy import deepcopy

import streamlit as st

from app.services import foreshadowing


def render_dialog(work, turns, *, read_only, save, close):
    @st.dialog("本局事件", width="large", dismissible=False)
    def dialog():
        plan = deepcopy(work["plan"])
        if not plan.get("story") and not plan.get("scene_events") and not plan.get("time_events"):
            st.info("本局暂无事件。可点击右上角的事件图标生成。")
            if st.button("关闭", key="page2_current_events_close"):
                close()
            return
        state = foreshadowing.game_state(turns)
        clock = f"第{state['day']}天"
        if state["minute"] is not None:
            clock += f" {state['minute'] // 60:02d}:{state['minute'] % 60:02d}"
        note = "保存仅修改本局事件；已触发事件保留触发状态。" if not read_only else "游客仅可查看。"
        st.caption("当前游戏时间：" + clock + " · " + note)
        consumed = {event.get("id") for turn in turns if foreshadowing.successful(turn)
                    for event in getattr(turn, "special_events", [])}
        prefix = "current_events_" + work["nonce"]
        with st.form(prefix):
            name_col, enabled_col = st.columns([3, 1], vertical_alignment="bottom")
            with name_col:
                plan["title"] = st.text_input("方案名称", plan.get("title", "事件方案"), disabled=read_only)
            with enabled_col:
                plan["enabled"] = st.checkbox("启用本局事件", value=bool(plan.get("enabled", True)), disabled=read_only)
            with st.expander("故事背景"):
                plan["story"] = st.text_area("故事背景", plan.get("story", ""), height=160, disabled=read_only)
            with st.container(height=280, border=False):
                for group, label in (("scene_events", "地点事件"), ("time_events", "时间事件")):
                    st.markdown(f"**{label} · {len(plan.get(group, []))}条**")
                    for index, event in enumerate(plan.get(group, []), 1):
                        key = f"{prefix}_{group}_{index}"
                        status = "已触发" if event.get("id") in consumed else "未触发"
                        heading = event.get("location", "未命名") if group == "scene_events" else f"第{event.get('day', 1)}天 {event.get('time', '')} · {event.get('title', '未命名')}"
                        with st.expander(f"{label} {index} · {heading} · {status}"):
                            event["enabled"] = st.checkbox("启用此条", value=bool(event.get("enabled", True)), key=key + "_enabled", disabled=read_only)
                            if group == "scene_events":
                                event["location"] = st.text_input("目标地点", event.get("location", ""), key=key + "_location", disabled=read_only)
                                patterns = st.text_area("地点表述（每行一个）", "\n".join(event.get("patterns", [])), key=key + "_patterns", disabled=read_only)
                                event["patterns"] = [line.strip() for line in patterns.splitlines() if line.strip()]
                            else:
                                event["title"] = st.text_input("事件名称", event.get("title", ""), key=key + "_title", disabled=read_only)
                                event["day"] = int(st.number_input("游戏第几天", min_value=1, value=int(event.get("day", 1)), step=1, key=key + "_day", disabled=read_only))
                                event["time"] = st.text_input("发生时间（HH:MM）", event.get("time", "14:00"), key=key + "_time", disabled=read_only)
                            event["event_content"] = st.text_area("事件内容", event.get("event_content", ""), height=130, key=key + "_content", disabled=read_only)
            confirm, cancel = st.columns(2)
            submitted = confirm.form_submit_button("保存到本局", type="primary", use_container_width=True, disabled=read_only)
            cancelled = cancel.form_submit_button("关闭" if read_only else "取消", use_container_width=True)
        if cancelled:
            close()
        if submitted:
            try:
                save(foreshadowing.without_event_dates(plan), work)
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))
            else:
                close(saved=True)
    dialog()
