"""Spoiler-collapsed authoring UI, separate from the first-input editor."""
from copy import deepcopy
from uuid import uuid4

import streamlit as st

from app.config import user_facing_error_message
from app.services import foreshadowing, system_prompts
from web.components.delete_confirmation import render_delete_confirmation, request_delete


def render_header(state, record, prompt_key, *, disabled=False):
    from web.pages import page2
    heading, generate = st.columns([1, 1], vertical_alignment="center")
    heading.subheader("预览" if disabled else "编辑")
    if generate.button("生成事件", key="foreshadow_generate",
                       disabled=disabled or record is None or page2._operation_kind_busy("events")):
        try:
            page2._queue_event_generation(
                final_setting=str(st.session_state.get(prompt_key, record.prompt) or ""),
                record_id=record.id, update_game=False,
            )
            st.rerun()
        except Exception as exc:
            st.error(user_facing_error_message(exc))
    if page2._pending_operations() or st.session_state.get("page2_tts_active"):
        page2._render_generation_toasts()
    if page2._pending_operations() or st.session_state.get("page2_operation_error"):
        page2._render_operation_queue_status()
        page2._render_media_operation_status()


def render_editor(state, record, *, disabled=False):
    if record is None:
        return
    with st.expander("事件", expanded=False, key="foreshadow_expander_" + record.id, on_change="rerun"):
        config = deepcopy(record.foreshadowing)
        enabled = st.toggle("启用事件", value=config.get("enabled", True), disabled=disabled,
                            key=f"foreshadow_enabled_{record.id}")
        if not disabled and enabled != config.get("enabled", True):
            config["enabled"] = enabled
            system_prompts.save_foreshadowing(state, record.id, config)
        st.caption("收起仅隐藏内容。方案与首轮输入独立；修改适用于之后新开的游戏。")
        plans = config.get("plans", [])
        ids = [p["id"] for p in plans]
        select_key = f"foreshadow_plan_{record.id}"
        pending = st.session_state.pop(select_key + "_pending", None)
        if pending:
            st.session_state[select_key] = pending
        if ids:
            preferred = config.get("selected_plan_id")
            if st.session_state.get(select_key) not in ids:
                st.session_state[select_key] = preferred if preferred in ids else ids[0]
            chosen = st.selectbox("事件方案", ids, key=select_key, disabled=disabled,
                                  format_func=lambda i: next(p["title"] for p in plans if p["id"] == i))
            if chosen != config.get("selected_plan_id") and not disabled:
                config["selected_plan_id"] = chosen
                system_prompts.save_foreshadowing(state, record.id, config)
        new_col, delete_col = st.columns(2)
        if new_col.button("新建空白方案", key="fe_new_" + record.id, disabled=disabled):
            plan = foreshadowing.new_plan(f"事件方案 {len(plans) + 1}")
            config.setdefault("plans", []).append(plan)
            config["selected_plan_id"] = plan["id"]
            system_prompts.save_foreshadowing(state, record.id, config)
            st.session_state[select_key + "_pending"] = plan["id"]
            st.rerun()
        plan = foreshadowing.selected_plan(config)
        if delete_col.button("删除当前方案", key="fe_delete_" + record.id, disabled=disabled or not plan):
            config["plans"] = [p for p in plans if p["id"] != plan["id"]]
            config["selected_plan_id"] = config["plans"][0]["id"] if config["plans"] else ""
            system_prompts.save_foreshadowing(state, record.id, config)
            st.rerun()
        if not plan:
            st.caption("暂无事件。可点击上方“生成事件”，或新建空白方案。")
            return
        with st.form("fe_form_" + plan["id"]):
            title = st.text_input("方案名称", plan["title"], disabled=disabled)
            story = st.text_area("故事背景", plan.get("story", ""), height=180, disabled=disabled)
            edited = {"scene_events": [], "time_events": []}
            delete_clicked = None
            for group, label in (("scene_events", "场景事件"), ("time_events", "时间事件")):
                st.markdown(f"**{label} · {len(plan.get(group, []))}条**")
                for index, event in enumerate(plan.get(group, [])):
                    key = "fe_" + plan["id"] + "_" + event["id"]
                    with st.container(border=True):
                        heading, action = st.columns([4, 1], vertical_alignment="center")
                        heading.markdown(f"**{label} {index + 1}**")
                        if action.form_submit_button("删除此条", key=key + "_delete", disabled=disabled):
                            name = event.get("title") or event.get("location") or "未命名"
                            delete_clicked = (event, f"{label} {index + 1} · {name}")
                        value = deepcopy(event)
                        value["enabled"] = st.checkbox("启用此条", value=event.get("enabled", True), key=key + "_enabled", disabled=disabled)
                        if group == "scene_events":
                            value.pop("title", None)
                            value["location"] = st.text_input("目标地点", event.get("location", ""), key=key + "_location", disabled=disabled)
                            patterns = st.text_area("地点表述", "\n".join(event.get("patterns", [])), key=key + "_patterns", disabled=disabled)
                            value["patterns"] = [p.strip() for p in patterns.splitlines() if p.strip()]
                        else:
                            value["title"] = st.text_input(f"{label} {index + 1} 名称", event.get("title", ""), key=key + "_title", disabled=disabled)
                            value["day"] = st.number_input("游戏第几天", min_value=1, value=int(event.get("day", 1)), key=key + "_day", disabled=disabled)
                            value["time"] = st.text_input("发生时间（HH:MM）", event.get("time", "14:00"), key=key + "_time", disabled=disabled)
                            value.pop("date", None)
                        value["event_content"] = st.text_area("事件 user prompt 内容", event["event_content"], height=130, key=key + "_content", disabled=disabled)
                        edited[group].append(value)
            save, add_scene, add_time = st.columns(3)
            saved = save.form_submit_button("保存事件", type="primary", disabled=disabled)
            scene_added = add_scene.form_submit_button("增加场景事件", disabled=disabled)
            time_added = add_time.form_submit_button("增加时间事件", disabled=disabled)

        def _validate(rows):
            foreshadowing.validate_plan(rows)

        def _save(rows):
            rows = foreshadowing.without_event_dates(rows)
            plan.update(rows, title=title.strip() or "未命名方案", story=story)
            config["plans"] = [plan if p["id"] == plan["id"] else p for p in plans]
            system_prompts.save_foreshadowing(state, record.id, config)

        if delete_clicked:
            event, label_text = delete_clicked
            try:
                _validate(edited)
                _save(edited)
            except ValueError:
                pass
            request_delete(f"foreshadow_{record.id}_{plan['id']}", event["id"], label_text)
            st.rerun()
        elif saved or scene_added or time_added:
            try:
                if scene_added or time_added:
                    group = "scene_events" if scene_added else "time_events"
                    event = {"id": uuid4().hex, "enabled": True, "event_content": "请填写事件内容。"}
                    event.update({"location": "待设置地点", "patterns": ["待设置地点"]} if scene_added else {"title": "新事件", "day": 1, "time": "14:00"})
                    edited[group].append(event)
                _validate(edited)
                _save(edited)
                st.session_state["prompt_settings_flash"] = "已保存事件。"
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

        def _delete_one(event_id):
            current_record = system_prompts.get_record(state, record.id)
            source = current_record.foreshadowing if current_record else record.foreshadowing
            current = deepcopy(source)
            for candidate in current.get("plans", []):
                for group in ("scene_events", "time_events"):
                    candidate[group] = [e for e in candidate.get(group, []) if e.get("id") != event_id]
            system_prompts.save_foreshadowing(state, record.id, current)
            st.session_state["prompt_settings_flash"] = "已删除该事件。"

        render_delete_confirmation(f"foreshadow_{record.id}_{plan['id']}", _delete_one)
