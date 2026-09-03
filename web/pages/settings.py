from __future__ import annotations

from uuid import uuid4

import streamlit as st

from app.config import AppStorageKeys, settings, user_facing_error_message
from app.models import CHAT_MODEL_LABELS, CHAT_MODEL_OPTIONS
from app.services import prompt_assets, system_prompts
from web.components.delete_confirmation import render_delete_confirmation, request_delete


PROMPT_SECTIONS = ["最终设定", "世界书", "角色卡", "固定模板"]


def _sync_page2_prompt_widget(prompt: str):
    prompt = str(prompt or "").strip()
    if prompt:
        st.session_state["page2_sidebar_system_prompt"] = prompt
    else:
        st.session_state.pop("page2_sidebar_system_prompt", None)


def _record_label(record, index: int, fallback: str) -> str:
    title = str(record.title or "").strip() or f"{fallback} {index + 1}"
    return f"{index + 1}. {title}"


def _safe_radio_value(key: str, ids: list[str], preferred: str = "") -> None:
    if not ids:
        st.session_state.pop(key, None)
        return
    current = str(st.session_state.get(key, "") or "")
    if current not in ids:
        st.session_state[key] = preferred if preferred in ids else ids[0]


def _consume_pending_selection(key: str) -> None:
    pending = str(st.session_state.pop(f"{key}_pending", "") or "")
    if pending:
        st.session_state[key] = pending


def _render_section_buttons() -> str:
    section = str(st.session_state.get("ordinary_prompt_section", "最终设定") or "最终设定")
    if section not in PROMPT_SECTIONS:
        section = "最终设定"
        st.session_state["ordinary_prompt_section"] = section
    columns = st.columns(4)
    for index, label in enumerate(PROMPT_SECTIONS):
        with columns[index]:
            if st.button(
                label,
                type="primary" if section == label else "secondary",
                use_container_width=True,
                key=f"ordinary_prompt_section_{index}",
            ):
                st.session_state["ordinary_prompt_section"] = label
                st.rerun()
    return section


def _render_final_setting_dialog(state) -> None:
    @st.dialog("新建最终设定", dismissible=False, width="large")
    def _dialog():
        templates = prompt_assets.load_fixed_templates()
        template = prompt_assets.selected_fixed_template(templates)
        world_books = prompt_assets.load_world_books()
        character_cards = prompt_assets.load_character_cards()
        named_characters = [record for record in character_cards if str(record.title or "").strip()]

        if template is None:
            st.error("当前没有选中的固定模板，请先到固定模板页面新建并保存一条模板。")
        else:
            st.caption(f"当前固定模板：{template.title or '未命名模板'}")
        if not world_books:
            st.error("暂无世界书，请先创建世界书。")
        if not named_characters:
            st.error("暂无有名称的角色卡，请先创建角色卡并填写角色名称。")

        world_ids = [record.id for record in world_books]
        character_ids = [record.id for record in named_characters]
        world_id = (
            st.selectbox(
                "世界书",
                options=world_ids,
                format_func=lambda record_id: next(
                    (
                        record.title or f"未命名世界书 {index + 1}"
                        for index, record in enumerate(world_books)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                key="final_setting_world_book",
            )
            if world_ids
            else ""
        )
        user_character_id = (
            st.selectbox(
                "用户角色",
                options=character_ids,
                format_func=lambda record_id: next(
                    (record.title for record in named_characters if record.id == record_id),
                    record_id,
                ),
                key="final_setting_user_character",
            )
            if character_ids
            else ""
        )
        if character_ids:
            st.caption("用户角色卡中的“角色性格”章节不会写入新建的最终设定。")

        remaining_characters = [record for record in character_cards if record.id != user_character_id]
        maximum_others = min(4, len(remaining_characters))
        other_count_options = list(range(maximum_others + 1))
        if st.session_state.get("final_setting_other_count") not in other_count_options:
            st.session_state.pop("final_setting_other_count", None)
        other_count = st.selectbox(
            "其他角色人数",
            options=other_count_options,
            key="final_setting_other_count",
        )
        if int(other_count) == 0:
            st.caption("其他角色：无")
        chosen_other_ids: list[str] = []
        for index in range(int(other_count)):
            options = [record.id for record in remaining_characters if record.id not in chosen_other_ids]
            widget_key = f"final_setting_other_character_{index}"
            if st.session_state.get(widget_key) not in options:
                st.session_state.pop(widget_key, None)
            chosen_id = st.selectbox(
                f"其他角色 {index + 1}",
                options=options,
                format_func=lambda record_id: next(
                    (
                        record.title or f"未命名角色 {card_index + 1}"
                        for card_index, record in enumerate(character_cards)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                key=widget_key,
            )
            chosen_other_ids.append(chosen_id)

        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button(
                "确定",
                type="primary",
                use_container_width=True,
                disabled=template is None or not world_ids or not character_ids,
            ):
                try:
                    world_book = next(record for record in world_books if record.id == world_id)
                    user_character = next(
                        record for record in character_cards if record.id == user_character_id
                    )
                    other_characters = [
                        next(record for record in character_cards if record.id == record_id)
                        for record_id in chosen_other_ids
                    ]
                    other_character_names = prompt_assets.other_character_section_names(
                        other_characters
                    )
                    final_prompt = prompt_assets.build_final_prompt(
                        template,
                        world_book,
                        user_character,
                        other_characters,
                    )
                    system_prompts.save_prompt(
                        state,
                        record_id="",
                        title="",
                        prompt=final_prompt,
                        first_input=prompt_assets.DEFAULT_FINAL_FIRST_INPUT,
                        preserve_blank_title=True,
                        final_template_id=template.id,
                        final_world_book_id=world_book.id,
                        final_user_character_id=user_character.id,
                        final_other_character_ids=[record.id for record in other_characters],
                        final_user_character_name=str(user_character.title or "").strip(),
                        final_other_character_names=other_character_names,
                    )
                except Exception as exc:
                    st.error(user_facing_error_message(exc))
                    return
                st.session_state["final_setting_record_select_pending"] = state.selected_record_id
                st.session_state["final_setting_dialog_open"] = False
                _sync_page2_prompt_widget(final_prompt)
                st.session_state["prompt_settings_flash"] = "已创建并保存最终设定。"
                st.rerun()
        with cancel_col:
            if st.button("取消", use_container_width=True):
                st.session_state["final_setting_dialog_open"] = False
                st.rerun()

    _dialog()


def _render_final_settings(is_guest: bool) -> None:
    state = system_prompts.load_state(hidden_space=bool(st.session_state.get("hidden_unlocked", False)))
    legacy_binding_message = ""
    legacy_binding_error = ""
    try:
        if prompt_assets.bind_legacy_final_setting_by_titles(
            state,
            final_title="佣兵世界",
            template_title="默认模板",
            world_book_title="佣兵世界",
            user_character_title="狼影",
            other_character_titles=["水月", "雏田", "莉娅"],
        ):
            legacy_binding_message = (
                "已为旧最终设定“佣兵世界”补齐来源关联，现在可以使用一键同步。"
            )
    except Exception as exc:
        legacy_binding_error = user_facing_error_message(exc)

    if legacy_binding_message:
        st.success(legacy_binding_message)
    if legacy_binding_error:
        st.error(legacy_binding_error)

    records = system_prompts.visible_records(state)
    record_ids = [record.id for record in records]
    _consume_pending_selection("final_setting_record_select")
    _safe_radio_value("final_setting_record_select", record_ids, state.selected_record_id)

    left, right = st.columns([1, 2])
    with left:
        st.subheader("最终设定记录")
        if records:
            chosen_id = st.radio(
                "选择最终设定",
                options=record_ids,
                format_func=lambda record_id: next(
                    (
                        _record_label(record, index, "未命名最终设定")
                        for index, record in enumerate(records)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                label_visibility="collapsed",
                key="final_setting_record_select",
            )
        else:
            st.info("暂无最终设定，请点击新建。")
            chosen_id = ""

    selected_record = next((record for record in records if record.id == chosen_id), None)
    if selected_record is not None and selected_record.id != state.selected_record_id:
        settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, selected_record.id)
        settings.set(AppStorageKeys.SYSTEM_PROMPT, str(selected_record.prompt or ""))
        state.selected_record_id = selected_record.id
        _sync_page2_prompt_widget(selected_record.prompt)

    with right:
        record_key = selected_record.id if selected_record else "new"
        st.subheader("编辑" if not is_guest else "预览")
        title = st.text_input(
            "记录名称",
            value=selected_record.title if selected_record else "",
            disabled=is_guest,
            key=f"system_prompt_title_{record_key}",
        )
        prompt = st.text_area(
            "prompt",
            value=selected_record.prompt if selected_record else "",
            height=360,
            disabled=is_guest,
            key=f"system_prompt_body_{record_key}",
        )
        first_input = st.text_area(
            "首轮输入",
            value=selected_record.first_input if selected_record else prompt_assets.DEFAULT_FINAL_FIRST_INPUT,
            height=180,
            disabled=is_guest,
            key=f"system_prompt_first_input_{record_key}",
            help="新对话没有上文且用户输入“开始”时，这段文字会替代“开始”发送给模型，并写入聊天记录和后续上下文。",
        )

        save_col, sync_col, new_col, delete_col = st.columns(4)
        if save_col.button("保存", type="primary", use_container_width=True, disabled=is_guest):
            system_prompts.save_prompt(
                state,
                record_id=selected_record.id if selected_record else "",
                title=title,
                prompt=prompt,
                first_input=first_input,
                preserve_blank_title=True,
            )
            _sync_page2_prompt_widget(prompt)
            st.session_state["prompt_settings_flash"] = "已保存最终设定。"
            st.rerun()
        sync_error = ""
        if sync_col.button(
            "一键同步",
            use_container_width=True,
            disabled=is_guest or selected_record is None,
        ):
            try:
                updated_labels = prompt_assets.sync_final_setting(
                    state,
                    selected_record,
                    title=title,
                    prompt=prompt,
                    first_input=first_input,
                )
            except Exception as exc:
                sync_error = user_facing_error_message(exc)
            else:
                _sync_page2_prompt_widget(prompt)
                if updated_labels:
                    details = "、".join(updated_labels)
                    st.session_state["prompt_settings_flash"] = (
                        f"已保存最终设定，并同步：{details}。"
                    )
                else:
                    st.session_state["prompt_settings_flash"] = (
                        "已保存最终设定；关联素材内容没有变化。"
                    )
                st.rerun()
        if new_col.button("新建", use_container_width=True, disabled=is_guest):
            st.session_state["final_setting_dialog_open"] = True
        if delete_col.button(
            "删除",
            use_container_width=True,
            disabled=is_guest or selected_record is None,
        ) and selected_record is not None:
            request_delete("final_setting", selected_record.id, selected_record.title or "未命名最终设定")
        if sync_error:
            st.error(sync_error)

    if st.session_state.get("final_setting_dialog_open"):
        _render_final_setting_dialog(state)

    def _delete(record_id: str) -> None:
        system_prompts.delete_record(state, record_id)
        _sync_page2_prompt_widget(settings.get(AppStorageKeys.SYSTEM_PROMPT, ""))
        st.session_state["prompt_settings_flash"] = "已删除最终设定。"

    render_delete_confirmation("final_setting", _delete)


def _refinement_state_key(kind: str, suffix: str) -> str:
    return f"{kind}_refinement_{suffix}"


def _clear_refinement_flow(kind: str) -> None:
    for suffix in ("phase", "payload", "questions", "error", "final_error"):
        st.session_state.pop(_refinement_state_key(kind, suffix), None)


def _render_generation_reasoning_selector(kind: str, model: str) -> str:
    key = f"{kind}_generator_reasoning"
    if model == "grok2":
        options = ["low", "medium", "high", "xhigh"]
        default = "high"
    else:
        options = ["开思考", "关思考"]
        default = "开思考"
    if st.session_state.get(key) not in options:
        st.session_state[key] = default
    selected = st.selectbox("思考模式", options=options, key=key)
    return selected if model == "grok2" else "high" if selected == "开思考" else "off"


def _start_refinement(kind: str, payload: dict) -> None:
    _clear_refinement_flow(kind)
    payload = dict(payload)
    payload["request_id"] = uuid4().hex
    st.session_state[_refinement_state_key(kind, "payload")] = payload
    st.session_state[_refinement_state_key(kind, "phase")] = "request_questions"
    st.session_state[f"{kind}_dialog_open"] = False


def _process_pending_refinement(kind: str) -> None:
    if st.session_state.get(_refinement_state_key(kind, "phase")) != "request_questions":
        return
    payload = st.session_state.get(_refinement_state_key(kind, "payload"))
    if not isinstance(payload, dict):
        _clear_refinement_flow(kind)
        st.session_state[_refinement_state_key(kind, "error")] = (
            "本次生成的临时数据已失效，请重新提交。"
        )
        st.session_state[f"{kind}_dialog_open"] = True
        st.rerun()
    label = "世界书" if kind == "world_book" else "角色卡"
    with st.spinner(f"正在根据{label}简介生成15个细化问题..."):
        try:
            if kind == "world_book":
                questions = prompt_assets.generate_world_book_questions(
                    payload["model"],
                    payload["description"],
                    reasoning_effort=payload["reasoning_effort"],
                )
            else:
                questions = prompt_assets.generate_character_card_questions(
                    payload["model"],
                    payload["identity"],
                    payload["relationship"],
                    payload["personality"],
                    payload["other"],
                    reasoning_effort=payload["reasoning_effort"],
                )
        except Exception as exc:
            st.session_state[_refinement_state_key(kind, "error")] = (
                user_facing_error_message(exc)
            )
            st.session_state.pop(_refinement_state_key(kind, "phase"), None)
            st.session_state.pop(_refinement_state_key(kind, "payload"), None)
            st.session_state[f"{kind}_dialog_open"] = True
            st.rerun()
    st.session_state[_refinement_state_key(kind, "questions")] = questions
    st.session_state[_refinement_state_key(kind, "phase")] = "answer_questions"
    st.rerun()


def _complete_refinement(kind: str, *, use_answers: bool, answers: str = "") -> None:
    payload = st.session_state.get(_refinement_state_key(kind, "payload"))
    questions = str(st.session_state.get(_refinement_state_key(kind, "questions"), "") or "")
    if not isinstance(payload, dict):
        raise ValueError("本次生成的临时数据已失效，请重新新建。")
    selected_questions = questions if use_answers else ""
    selected_answers = str(answers or "") if use_answers else ""
    if kind == "world_book":
        content = prompt_assets.generate_world_book(
            payload["model"],
            payload["description"],
            questions=selected_questions,
            answers=selected_answers,
            reasoning_effort=payload["reasoning_effort"],
        )
        record = prompt_assets.save_world_book("", "", content)
        label = "世界书"
    else:
        content = prompt_assets.generate_character_card(
            payload["model"],
            payload["identity"],
            payload["relationship"],
            payload["personality"],
            payload["other"],
            questions=selected_questions,
            answers=selected_answers,
            reasoning_effort=payload["reasoning_effort"],
        )
        record = prompt_assets.save_character_card("", "", content)
        label = "角色卡"
    st.session_state[f"{kind}_record_select_pending"] = record.id
    _clear_refinement_flow(kind)
    st.session_state["prompt_settings_flash"] = f"已生成并保存{label}。"


def _render_refinement_dialog(kind: str) -> None:
    if st.session_state.get(_refinement_state_key(kind, "phase")) != "answer_questions":
        return
    payload = st.session_state.get(_refinement_state_key(kind, "payload"))
    questions = str(st.session_state.get(_refinement_state_key(kind, "questions"), "") or "")
    if not isinstance(payload, dict) or not questions:
        _clear_refinement_flow(kind)
        st.session_state[_refinement_state_key(kind, "error")] = (
            "本次生成的临时数据已失效，请重新提交。"
        )
        st.session_state[f"{kind}_dialog_open"] = True
        st.rerun()
    label = "世界书" if kind == "world_book" else "角色卡"
    answer_key = f"{kind}_refinement_answers_{payload.get('request_id', '')}"

    @st.dialog(f"细化{label}", dismissible=False, width="large")
    def _dialog():
        st.caption("可以回答全部或部分问题，也可以自由补充选项之外的设定。")
        with st.container(border=True, height=420):
            st.markdown(questions)
        answers = st.text_area("你的回答", height=260, key=answer_key)
        final_error = str(
            st.session_state.pop(_refinement_state_key(kind, "final_error"), "") or ""
        )
        if final_error:
            st.error(final_error)
        generate_col, skip_col = st.columns(2)
        with generate_col:
            if st.button(
                "根据回答生成",
                type="primary",
                use_container_width=True,
                key=f"{kind}_refinement_generate",
            ):
                with st.spinner(f"正在生成{label}..."):
                    try:
                        _complete_refinement(kind, use_answers=True, answers=answers)
                    except Exception as exc:
                        st.session_state[_refinement_state_key(kind, "final_error")] = (
                            user_facing_error_message(exc)
                        )
                        st.rerun()
                st.rerun()
        with skip_col:
            if st.button(
                "跳过问题并生成",
                use_container_width=True,
                key=f"{kind}_refinement_skip",
            ):
                with st.spinner(f"正在生成{label}..."):
                    try:
                        _complete_refinement(kind, use_answers=False)
                    except Exception as exc:
                        st.session_state[_refinement_state_key(kind, "final_error")] = (
                            user_facing_error_message(exc)
                        )
                        st.rerun()
                st.rerun()

    _dialog()


def _render_world_book_dialog() -> None:
    @st.dialog("新建世界书", dismissible=False, width="large")
    def _dialog():
        model = st.selectbox(
            "模型",
            options=list(CHAT_MODEL_OPTIONS),
            format_func=lambda item: CHAT_MODEL_LABELS.get(item, item),
            key="world_book_generator_model",
        )
        reasoning_effort = _render_generation_reasoning_selector("world_book", model)
        error = str(st.session_state.pop(_refinement_state_key("world_book", "error"), "") or "")
        if error:
            st.error(error)
        description = st.text_area("世界背景介绍", height=320, key="world_book_generator_description")
        generate_col, cancel_col = st.columns(2)
        with generate_col:
            if st.button("生成", type="primary", use_container_width=True):
                if not str(description or "").strip():
                    st.error("请先填写世界背景介绍；如果想从零填写，请点击取消创建空白记录。")
                    return
                _start_refinement(
                    "world_book",
                    {
                        "model": model,
                        "reasoning_effort": reasoning_effort,
                        "description": str(description).strip(),
                    },
                )
                st.rerun()
        with cancel_col:
            if st.button("取消", use_container_width=True):
                _clear_refinement_flow("world_book")
                record = prompt_assets.save_world_book("", "", "")
                st.session_state["world_book_record_select_pending"] = record.id
                st.session_state["world_book_dialog_open"] = False
                st.session_state["prompt_settings_flash"] = "已创建空白世界书。"
                st.rerun()

    _dialog()


def _render_character_card_dialog() -> None:
    @st.dialog("新建角色卡", dismissible=False, width="large")
    def _dialog():
        model = st.selectbox(
            "模型",
            options=list(CHAT_MODEL_OPTIONS),
            format_func=lambda item: CHAT_MODEL_LABELS.get(item, item),
            key="character_card_generator_model",
        )
        reasoning_effort = _render_generation_reasoning_selector("character_card", model)
        error = str(
            st.session_state.pop(_refinement_state_key("character_card", "error"), "") or ""
        )
        if error:
            st.error(error)
        identity = st.text_area("角色身份", height=130, key="character_card_generator_identity")
        relationship = st.text_area(
            "角色与用户角色的关系",
            height=130,
            key="character_card_generator_relationship",
        )
        personality = st.text_area("角色性格", height=130, key="character_card_generator_personality")
        other = st.text_area("其他", height=130, key="character_card_generator_other")
        generate_col, cancel_col = st.columns(2)
        with generate_col:
            if st.button("生成", type="primary", use_container_width=True):
                _start_refinement(
                    "character_card",
                    {
                        "model": model,
                        "reasoning_effort": reasoning_effort,
                        "identity": str(identity or "").strip(),
                        "relationship": str(relationship or "").strip(),
                        "personality": str(personality or "").strip(),
                        "other": str(other or "").strip(),
                    },
                )
                st.rerun()
        with cancel_col:
            if st.button("取消", use_container_width=True):
                _clear_refinement_flow("character_card")
                record = prompt_assets.save_character_card("", "", "")
                st.session_state["character_card_record_select_pending"] = record.id
                st.session_state["character_card_dialog_open"] = False
                st.session_state["prompt_settings_flash"] = "已创建空白角色卡。"
                st.rerun()

    _dialog()


def _render_text_asset(
    *,
    kind: str,
    is_guest: bool,
    records,
    title_label: str,
    content_label: str,
    fallback_label: str,
    save_record,
    delete_record,
    dialog_state_key: str,
    dialog_renderer,
) -> None:
    select_key = f"{kind}_record_select"
    record_ids = [record.id for record in records]
    _consume_pending_selection(select_key)
    _safe_radio_value(select_key, record_ids)
    left, right = st.columns([1, 2])
    with left:
        st.subheader(content_label)
        if records:
            chosen_id = st.radio(
                f"选择{content_label}",
                options=record_ids,
                format_func=lambda record_id: next(
                    (
                        _record_label(record, index, fallback_label)
                        for index, record in enumerate(records)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                label_visibility="collapsed",
                key=select_key,
            )
        else:
            st.info(f"暂无{content_label}，请点击新建。")
            chosen_id = ""
    selected_record = next((record for record in records if record.id == chosen_id), None)
    with right:
        record_key = selected_record.id if selected_record else "new"
        st.subheader("编辑" if not is_guest else "预览")
        title = st.text_input(
            title_label,
            value=selected_record.title if selected_record else "",
            disabled=is_guest,
            key=f"{kind}_title_{record_key}",
        )
        content = st.text_area(
            content_label,
            value=selected_record.content if selected_record else "",
            height=520,
            disabled=is_guest,
            key=f"{kind}_content_{record_key}",
        )
        save_col, new_col, delete_col = st.columns(3)
        if save_col.button("保存", type="primary", use_container_width=True, disabled=is_guest):
            record = save_record(selected_record.id if selected_record else "", title, content)
            st.session_state[f"{select_key}_pending"] = record.id
            st.session_state["prompt_settings_flash"] = f"已保存{content_label}。"
            st.rerun()
        if new_col.button("新建", use_container_width=True, disabled=is_guest):
            st.session_state[dialog_state_key] = True
        if delete_col.button(
            "删除",
            use_container_width=True,
            disabled=is_guest or selected_record is None,
        ) and selected_record is not None:
            request_delete(kind, selected_record.id, selected_record.title or fallback_label)

    if st.session_state.get(dialog_state_key):
        dialog_renderer()

    def _delete(record_id: str) -> None:
        delete_record(record_id)
        st.session_state["prompt_settings_flash"] = f"已删除{content_label}。"

    render_delete_confirmation(kind, _delete)


def _render_fixed_templates(is_guest: bool) -> None:
    records = prompt_assets.load_fixed_templates()
    active = prompt_assets.selected_fixed_template(records)
    select_key = "fixed_template_record_select"
    record_ids = [record.id for record in records]
    _consume_pending_selection(select_key)
    _safe_radio_value(select_key, record_ids, active.id if active else "")

    left, right = st.columns([1, 2])
    with left:
        st.subheader("固定模板")
        if active is not None:
            st.success(f"当前使用：{active.title or '未命名模板'}")
        else:
            st.warning("当前没有可用模板。")
        if records:
            chosen_id = st.radio(
                "选择固定模板",
                options=record_ids,
                format_func=lambda record_id: next(
                    (
                        _record_label(record, index, "未命名模板")
                        + (" · 当前使用" if active and record.id == active.id else "")
                        for index, record in enumerate(records)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                label_visibility="collapsed",
                key=select_key,
            )
        else:
            st.info("暂无固定模板，请点击新建。")
            chosen_id = ""

    selected_record = next((record for record in records if record.id == chosen_id), None)
    with right:
        record_key = selected_record.id if selected_record else "new"
        st.subheader("编辑" if not is_guest else "预览")
        title = st.text_input(
            "模板名称",
            value=selected_record.title if selected_record else "",
            disabled=is_guest,
            key=f"fixed_template_title_{record_key}",
        )
        background = st.text_area(
            "背景介绍",
            value=selected_record.background_intro if selected_record else "",
            height=360,
            disabled=is_guest,
            key=f"fixed_template_background_{record_key}",
        )
        dm_core = st.text_area(
            "DM 的核心任务",
            value=selected_record.dm_core_tasks if selected_record else "",
            height=520,
            disabled=is_guest,
            key=f"fixed_template_dm_core_{record_key}",
        )
        response_format = st.text_area(
            "回复格式要求",
            value=selected_record.response_format if selected_record else "",
            height=420,
            disabled=is_guest,
            key=f"fixed_template_response_{record_key}",
        )
        save_col, new_col, delete_col = st.columns(3)
        if save_col.button("保存", type="primary", use_container_width=True, disabled=is_guest):
            record = prompt_assets.save_fixed_template(
                selected_record.id if selected_record else "",
                title,
                background,
                dm_core,
                response_format,
                make_active=True,
            )
            st.session_state[f"{select_key}_pending"] = record.id
            st.session_state["prompt_settings_flash"] = "已保存，并设为当前固定模板。"
            st.rerun()
        if new_col.button("新建", use_container_width=True, disabled=is_guest):
            record = prompt_assets.create_prefilled_fixed_template()
            st.session_state[f"{select_key}_pending"] = record.id
            st.session_state["prompt_settings_flash"] = "已创建预填模板；点击保存后才会设为当前模板。"
            st.rerun()
        if delete_col.button(
            "删除",
            use_container_width=True,
            disabled=is_guest or selected_record is None,
        ) and selected_record is not None:
            request_delete("fixed_template", selected_record.id, selected_record.title or "未命名模板")

    def _delete(record_id: str) -> None:
        prompt_assets.delete_fixed_template(record_id)
        st.session_state["prompt_settings_flash"] = "已删除固定模板。"

    render_delete_confirmation("fixed_template", _delete)


def render():
    st.title("Prompt")
    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    is_guest = mode == "guest"
    section = _render_section_buttons()
    flash = str(st.session_state.pop("prompt_settings_flash", "") or "")
    if flash:
        st.success(flash)

    if section == "最终设定":
        _render_final_settings(is_guest)
    elif section == "世界书":
        _process_pending_refinement("world_book")
        _render_text_asset(
            kind="world_book",
            is_guest=is_guest,
            records=prompt_assets.load_world_books(),
            title_label="世界书名称",
            content_label="世界书",
            fallback_label="未命名世界书",
            save_record=prompt_assets.save_world_book,
            delete_record=prompt_assets.delete_world_book,
            dialog_state_key="world_book_dialog_open",
            dialog_renderer=_render_world_book_dialog,
        )
        _render_refinement_dialog("world_book")
    elif section == "角色卡":
        _process_pending_refinement("character_card")
        _render_text_asset(
            kind="character_card",
            is_guest=is_guest,
            records=prompt_assets.load_character_cards(),
            title_label="角色名称",
            content_label="角色卡",
            fallback_label="未命名角色",
            save_record=prompt_assets.save_character_card,
            delete_record=prompt_assets.delete_character_card,
            dialog_state_key="character_card_dialog_open",
            dialog_renderer=_render_character_card_dialog,
        )
        _render_refinement_dialog("character_card")
    else:
        _render_fixed_templates(is_guest)
