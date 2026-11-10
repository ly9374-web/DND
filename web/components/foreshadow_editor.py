"""Spoiler-collapsed authoring UI, separate from the first-input editor."""
from copy import deepcopy
from uuid import uuid4

import streamlit as st

from app.config import user_facing_error_message
from app.services import foreshadowing, page2_service, system_prompts
from web.components.delete_confirmation import render_delete_confirmation, request_delete


def _finish(state, record, work, events):
    config = deepcopy(record.foreshadowing)
    plan = foreshadowing.new_plan(f"事件方案 {len(config.get('plans', [])) + 1}")
    plan.update(events)
    plan["story"] = events["story"]
    plan["questions"] = work.get("questions", "")
    plan["answers"] = work.get("answers", "")
    config.setdefault("plans", []).append(plan)
    config["selected_plan_id"] = plan["id"]
    config.setdefault("enabled", True)
    system_prompts.save_foreshadowing(state, record.id, config)
    st.session_state[f"foreshadow_plan_{record.id}_pending"] = plan["id"]
    st.session_state.pop("foreshadow_work", None)
    st.session_state["prompt_settings_flash"] = "已生成并保存5条场景事件和5条时间事件，可展开“事件”修改。"
    st.rerun()


def _generate_result(work):
    with st.spinner("正在一次生成故事背景与10个事件…"):
        result = foreshadowing.generate(
            "events", work["ctx"], work["source"], work["answers"], questions=work["questions"]
        )
    work["result"] = result
    work["result_nonce"] = uuid4().hex
    work["stage"] = "result"


@st.dialog("生成事件", width="large", dismissible=False)
def _workflow(state, record):
    work = st.session_state["foreshadow_work"]
    nonce = work["id"]
    try:
        if work["stage"] == "questions":
            st.caption("第1轮：选择未来4天的世界发展方向。问卷使用Flash，不开启思考；可以全部或部分回答，也可以自由补充。")
            with st.container(border=True, height=420):
                st.markdown(work["questions"])
            answers = st.text_area("你的回答", height=200, key="fq_" + nonce)
            proceed, cancel = st.columns(2)
            submitted = proceed.button("生成背景与事件", type="primary", use_container_width=True, key="fq_submit_" + nonce)
            cancelled = cancel.button("取消", use_container_width=True, key="fq_cancel_" + nonce)
            if cancelled:
                st.session_state.pop("foreshadow_work", None)
                st.rerun()
            if submitted:
                work["answers"] = answers
                _generate_result(work)
                st.rerun()
        elif work["stage"] == "result":
            st.caption("第2轮已完成：故事背景与5个场景事件、5个时间事件在同一次生成中返回。确定后可逐条编辑事件。")
            result = work["result"]
            story = st.text_area("故事背景", result["story"], height=300, key="fs_" + work["result_nonce"])
            with st.container(border=True, height=320):
                for group, label in (("scene_events", "场景事件"), ("time_events", "时间事件")):
                    st.markdown(f"**{label} · {len(result[group])}条**")
                    for index, event in enumerate(result[group], 1):
                        heading = event["location"] if group == "scene_events" else f"第{event['day']}天 {event['time']} · {event['title']}"
                        st.markdown(f"**{index}. {heading}**")
                        st.write(event["event_content"])
            confirm, regenerate, cancel = st.columns(3)
            confirmed = confirm.button("确定", type="primary", key="fs_confirm_" + nonce)
            regenerated = regenerate.button("重新生成", key="fs_regenerate_" + nonce)
            cancelled = cancel.button("取消", key="fs_cancel_" + nonce)
            if cancelled:
                st.session_state.pop("foreshadow_work", None)
                st.rerun()
            if regenerated:
                _generate_result(work)
                st.rerun()
            if confirmed:
                if not story.strip():
                    st.error("故事背景不能为空。")
                    return
                _finish(state, record, work, {**result, "story": story})
    except Exception as exc:
        st.error(user_facing_error_message(exc))


def render_header(state, record, prompt_key, *, disabled=False):
    heading, generate = st.columns([1, 1], vertical_alignment="center")
    heading.subheader("预览" if disabled else "编辑")
    clicked = generate.button("生成事件", key="foreshadow_generate", disabled=disabled or record is None)
    if clicked:
        source = str(st.session_state.get(prompt_key, record.prompt) or "").strip()
        if not source:
            st.error("请先填写最终设定。")
        else:
            st.session_state["foreshadow_work"] = {
                "id": uuid4().hex, "record_id": record.id, "source": source,
                "stage": "start", "answers": "", "questions": "",
                "ctx": page2_service.load_context_from_settings(),
            }
    work = st.session_state.get("foreshadow_work")
    if work and record and work["record_id"] == record.id and not disabled:
        if work["stage"] not in ("start", "questions", "result"):
            st.session_state.pop("foreshadow_work", None)
            st.info("生成流程已更新为两轮，请重新点击“生成事件”。")
            return
        if work["stage"] == "start":
            try:
                with st.spinner("正在使用Flash生成8道问题…"):
                    work["questions"] = foreshadowing.generate("questions", work["ctx"], work["source"])
                work["stage"] = "questions"
            except Exception as exc:
                st.error(user_facing_error_message(exc))
                st.session_state.pop("foreshadow_work", None)
                return
        _workflow(state, record)


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
                            value["date"] = st.text_input("具体日期（可选，如2月13日；填写后优先于第几天）", event.get("date", ""), key=key + "_date", disabled=disabled)
                        value["event_content"] = st.text_area("事件 user prompt 内容", event["event_content"], height=130, key=key + "_content", disabled=disabled)
                        edited[group].append(value)
            save, add_scene, add_time = st.columns(3)
            saved = save.form_submit_button("保存事件", type="primary", disabled=disabled)
            scene_added = add_scene.form_submit_button("增加场景事件", disabled=disabled)
            time_added = add_time.form_submit_button("增加时间事件", disabled=disabled)

        def _validate(rows):
            for event in rows["scene_events"]:
                foreshadowing.validate_patterns(event["patterns"])
                if not event.get("location", "").strip() or not event["event_content"].strip():
                    raise ValueError("场景事件的地点和内容不能为空。")
            for event in rows["time_events"]:
                foreshadowing.clock_minutes(event["time"])
                if event.get("date") and foreshadowing._calendar(event["date"]) is None:
                    raise ValueError("具体日期格式不正确，请使用例如2月13日或2026-02-13。")
                if not event["title"].strip() or not event["event_content"].strip():
                    raise ValueError("时间事件的名称和内容不能为空。")

        def _save(rows):
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
