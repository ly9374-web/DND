from __future__ import annotations

from uuid import uuid4

import streamlit as st

from app.config import AppStorageKeys, settings, user_facing_error_message
from app.models import CHAT_MODEL_LABELS, CHAT_MODEL_OPTIONS
from app.services import prompt_assets, quick_inputs, system_prompts
from web.components.delete_confirmation import render_delete_confirmation, request_delete


PROMPT_SECTIONS = ["最终设定", "世界书", "角色卡", "固定模板", "长期记忆", "快捷输入"]


def _hidden_space() -> bool:
    """当前会话是否为隐藏账号（1369 登录）。"""
    return bool(st.session_state.get("hidden_unlocked", False))


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
    columns = st.columns(len(PROMPT_SECTIONS))
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
        hidden_space = _hidden_space()
        templates = prompt_assets.load_fixed_templates(hidden_space=hidden_space)
        active_template = prompt_assets.selected_fixed_template(templates)
        world_books = prompt_assets.load_world_books(hidden_space=hidden_space)
        character_cards = prompt_assets.load_character_cards(hidden_space=hidden_space)
        long_term_memories = prompt_assets.load_long_term_memories(
            hidden_space=hidden_space
        )
        named_characters = [record for record in character_cards if str(record.title or "").strip()]

        template_ids = [record.id for record in templates]
        template_key = "final_setting_fixed_template"
        if template_ids and st.session_state.get(template_key) not in template_ids:
            st.session_state[template_key] = (
                active_template.id
                if active_template and active_template.id in template_ids
                else template_ids[0]
            )
        template_id = (
            st.selectbox(
                "固定模板",
                options=template_ids,
                format_func=lambda record_id: next(
                    (
                        record.title or f"未命名模板 {index + 1}"
                        for index, record in enumerate(templates)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                key=template_key,
            )
            if template_ids
            else ""
        )
        template = next((record for record in templates if record.id == template_id), None)

        if not templates:
            st.error("暂无固定模板，请先到固定模板页面新建并保存一条模板。")
        if not world_books:
            st.error("暂无世界书，请先创建世界书。")
        if not named_characters:
            st.error("暂无有名称的角色卡，请先创建角色卡并填写角色名称。")
        if not long_term_memories:
            st.error("暂无长期记忆，请先创建长期记忆。")

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
        memory_ids = [record.id for record in long_term_memories]
        long_term_memory_id = (
            st.selectbox(
                "长期记忆",
                options=memory_ids,
                format_func=lambda record_id: next(
                    (
                        record.title or f"未命名长期记忆 {index + 1}"
                        for index, record in enumerate(long_term_memories)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                key="final_setting_long_term_memory",
            )
            if memory_ids
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
                disabled=(
                    template is None
                    or not world_ids
                    or not character_ids
                    or not memory_ids
                ),
            ):
                try:
                    world_book = next(record for record in world_books if record.id == world_id)
                    user_character = next(
                        record for record in character_cards if record.id == user_character_id
                    )
                    long_term_memory = next(
                        record
                        for record in long_term_memories
                        if record.id == long_term_memory_id
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
                        long_term_memory=long_term_memory.content,
                        preserve_blank_title=True,
                        final_template_id=template.id,
                        final_world_book_id=world_book.id,
                        final_long_term_memory_id=long_term_memory.id,
                        final_user_character_id=user_character.id,
                        final_other_character_ids=[record.id for record in other_characters],
                        final_user_character_name=str(user_character.title or "").strip(),
                        final_other_character_names=other_character_names,
                    )
                except Exception as exc:
                    st.error(user_facing_error_message(exc))
                    return
                st.session_state["final_setting_record_select_pending"] = state.selected_record_id
                st.session_state["page2_final_setting_id"] = state.selected_record_id
                st.session_state["final_setting_dialog_open"] = False
                _sync_page2_prompt_widget(final_prompt)
                st.session_state["prompt_settings_flash"] = "已创建并保存最终设定。"
                st.rerun()
        with cancel_col:
            if st.button("取消", use_container_width=True):
                st.session_state["final_setting_dialog_open"] = False
                st.rerun()

    _dialog()


def _render_first_input_editor(state, selected_record, *, is_guest: bool) -> None:
    if selected_record is None:
        return

    selected_record.__post_init__()
    create_option = "__create_first_input__"
    select_key = f"system_prompt_first_input_select_{selected_record.id}"
    pending_key = f"{select_key}_pending"
    option_ids = [item.id for item in selected_record.first_inputs]
    pending = str(st.session_state.pop(pending_key, "") or "")
    if pending in option_ids:
        st.session_state[select_key] = pending
    if st.session_state.get(select_key) not in option_ids + [create_option]:
        st.session_state[select_key] = selected_record.selected_first_input_id

    title_col, select_col, name_col, delete_col = st.columns(
        [1.05, 2.1, 2.8, 0.8],
        vertical_alignment="bottom",
    )
    with title_col:
        st.markdown("**首轮输入**")
    with select_col:
        chosen_id = st.selectbox(
            "选择首轮输入",
            options=option_ids + [create_option],
            format_func=lambda item_id: (
                "新建"
                if item_id == create_option
                else next(
                    (
                        item.title or f"未命名首轮输入 {index + 1}"
                        for index, item in enumerate(selected_record.first_inputs)
                        if item.id == item_id
                    ),
                    item_id,
                )
            ),
            label_visibility="collapsed",
            disabled=is_guest,
            key=select_key,
        )

    if chosen_id == create_option and not is_guest:
        created = system_prompts.create_first_input(state, selected_record.id)
        st.session_state[pending_key] = created.id
        st.session_state.pop(select_key, None)
        st.rerun()

    if chosen_id != selected_record.selected_first_input_id and chosen_id in option_ids:
        system_prompts.select_first_input(state, selected_record.id, chosen_id)

    selected_input = next(
        (
            item
            for item in selected_record.first_inputs
            if item.id == selected_record.selected_first_input_id
        ),
        selected_record.first_inputs[0],
    )
    with name_col:
        first_input_title = st.text_input(
            "首轮输入名称",
            value=selected_input.title,
            label_visibility="collapsed",
            disabled=is_guest,
            key=f"system_prompt_first_input_title_{selected_record.id}_{selected_input.id}",
            placeholder="首轮输入名称",
        )
    with delete_col:
        delete_clicked = st.button(
            "删除",
            use_container_width=True,
            disabled=is_guest or len(selected_record.first_inputs) <= 1,
            key=f"system_prompt_first_input_delete_{selected_record.id}_{selected_input.id}",
        )
    first_input_content = st.text_area(
        "首轮输入内容",
        value=selected_input.content,
        height=180,
        disabled=is_guest,
        label_visibility="collapsed",
        key=f"system_prompt_first_input_body_{selected_record.id}_{selected_input.id}",
        help="新对话没有上文且用户输入“开始”时，当前选中项会替代“开始”发送给模型，并写入聊天记录和后续上下文。",
    )

    if delete_clicked:
        next_id = system_prompts.delete_first_input(
            state,
            selected_record.id,
            selected_input.id,
        )
        st.session_state[pending_key] = next_id
        st.session_state.pop(select_key, None)
        st.rerun()

    if (
        not is_guest
        and (
            first_input_title != selected_input.title
            or first_input_content != selected_input.content
        )
    ):
        system_prompts.update_first_input(
            state,
            selected_record.id,
            selected_input.id,
            title=first_input_title,
            content=first_input_content,
        )
    if not is_guest:
        st.caption("首轮输入的选择、名称和正文会自动保存。")


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
        st.session_state["page2_final_setting_id"] = selected_record.id
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
        long_term_memory = st.text_area(
            "长期记忆",
            value=(
                selected_record.long_term_memory
                if selected_record
                else prompt_assets.current_default_long_term_memory(
                    hidden_space=_hidden_space()
                )
            ),
            height=360,
            disabled=is_guest,
            key=f"system_prompt_long_term_memory_{record_key}",
            help="Story Brain 生成和更新时使用的规则。",
        )
        _render_first_input_editor(state, selected_record, is_guest=is_guest)

        save_col, sync_col, new_col, delete_col = st.columns(4)
        if save_col.button("保存", type="primary", use_container_width=True, disabled=is_guest):
            system_prompts.save_prompt(
                state,
                record_id=selected_record.id if selected_record else "",
                title=title,
                prompt=prompt,
                long_term_memory=long_term_memory,
                preserve_blank_title=True,
            )
            st.session_state["page2_final_setting_id"] = state.selected_record_id
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
                    long_term_memory=long_term_memory,
                    first_inputs=list(selected_record.first_inputs),
                    selected_first_input_id=selected_record.selected_first_input_id,
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
        st.session_state["page2_final_setting_id"] = str(
            settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or ""
        )
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
        record = prompt_assets.save_world_book("", "", content, hidden_space=_hidden_space())
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
        record = prompt_assets.save_character_card("", "", content, hidden_space=_hidden_space())
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
                record = prompt_assets.save_world_book("", "", "", hidden_space=_hidden_space())
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
                record = prompt_assets.save_character_card("", "", "", hidden_space=_hidden_space())
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
    hidden_space = _hidden_space()
    records = prompt_assets.load_fixed_templates(hidden_space=hidden_space)
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
                hidden_space=hidden_space,
            )
            st.session_state[f"{select_key}_pending"] = record.id
            st.session_state["prompt_settings_flash"] = "已保存，并设为当前固定模板。"
            st.rerun()
        if new_col.button("新建", use_container_width=True, disabled=is_guest):
            record = prompt_assets.create_prefilled_fixed_template(hidden_space=hidden_space)
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


def _render_long_term_memories(is_guest: bool) -> None:
    hidden_space = _hidden_space()
    records = prompt_assets.load_long_term_memories(hidden_space=hidden_space)
    default_record = prompt_assets.default_long_term_memory_record(
        hidden_space=hidden_space
    )
    if default_record is None and hidden_space:
        default_record = prompt_assets.default_long_term_memory_record()
    select_key = "long_term_memory_record_select"
    record_ids = [record.id for record in records]
    _consume_pending_selection(select_key)
    _safe_radio_value(select_key, record_ids)

    left, right = st.columns([1, 2])
    with left:
        st.subheader("长期记忆")
        if records:
            chosen_id = st.radio(
                "选择长期记忆",
                options=record_ids,
                format_func=lambda record_id: next(
                    (
                        _record_label(record, index, "未命名长期记忆")
                        + (
                            " · 当前默认"
                            if default_record is not None
                            and record.id == default_record.id
                            else ""
                        )
                        for index, record in enumerate(records)
                        if record.id == record_id
                    ),
                    record_id,
                ),
                label_visibility="collapsed",
                key=select_key,
            )
        else:
            st.info("暂无长期记忆，请点击新建。")
            chosen_id = ""

    selected_record = next((item for item in records if item.id == chosen_id), None)
    with right:
        record_key = selected_record.id if selected_record else "new"
        st.subheader("编辑" if not is_guest else "预览")
        title = st.text_input(
            "长期记忆名称",
            value=selected_record.title if selected_record else "",
            disabled=is_guest,
            key=f"long_term_memory_title_{record_key}",
        )
        content = st.text_area(
            "长期记忆",
            value=(
                selected_record.content
                if selected_record
                else prompt_assets.current_default_long_term_memory(
                    hidden_space=hidden_space
                )
            ),
            height=620,
            disabled=is_guest,
            key=f"long_term_memory_content_{record_key}",
            help="这段内容会作为 Story Brain 生成和更新时的规则。",
        )
        save_col, new_col, delete_col = st.columns(3)
        if save_col.button(
            "保存",
            type="primary",
            use_container_width=True,
            disabled=is_guest,
            key="long_term_memory_save",
        ):
            record = prompt_assets.save_long_term_memory(
                selected_record.id if selected_record else "",
                title,
                content,
                hidden_space=hidden_space,
            )
            st.session_state[f"{select_key}_pending"] = record.id
            default_key = (
                AppStorageKeys.HIDDEN_DEFAULT_LONG_TERM_MEMORY_RECORD_ID
                if hidden_space
                and record.id
                == str(
                    settings.get(
                        AppStorageKeys.HIDDEN_DEFAULT_LONG_TERM_MEMORY_RECORD_ID,
                        "",
                    )
                    or ""
                )
                else AppStorageKeys.DEFAULT_LONG_TERM_MEMORY_RECORD_ID
            )
            is_current_default = record.id == str(settings.get(default_key, "") or "")
            st.session_state["prompt_settings_flash"] = (
                "已保存长期记忆，并更新为最新默认规则。"
                if is_current_default
                else "已保存长期记忆。"
            )
            st.rerun()
        if new_col.button(
            "新建",
            use_container_width=True,
            disabled=is_guest,
            key="long_term_memory_new",
        ):
            record = prompt_assets.create_prefilled_long_term_memory(
                hidden_space=hidden_space
            )
            st.session_state[f"{select_key}_pending"] = record.id
            st.session_state["prompt_settings_flash"] = (
                "已新建长期记忆，并填入默认规则。"
            )
            st.rerun()
        if delete_col.button(
            "删除",
            use_container_width=True,
            disabled=is_guest or selected_record is None,
            key="long_term_memory_delete",
        ) and selected_record is not None:
            request_delete(
                "long_term_memory",
                selected_record.id,
                selected_record.title or "未命名长期记忆",
            )

    def _delete(record_id: str) -> None:
        prompt_assets.delete_long_term_memory(record_id)
        st.session_state["prompt_settings_flash"] = "已删除长期记忆。"

    render_delete_confirmation("long_term_memory", _delete)


def _render_quick_inputs(is_guest: bool) -> None:
    hidden_space = _hidden_space()
    options = quick_inputs.quick_input_options(hidden_space=hidden_space)
    records = {item["id"]: item for item in options}
    select_key = "quick_input_record_select"
    # Scope widget drafts too, so an account switch cannot reuse private edits.
    scope = "private" if hidden_space else "ordinary"
    new_key = f"quick_input_new_{scope}"
    _consume_pending_selection(select_key)
    if st.session_state.get(new_key):
        st.session_state[select_key] = None
    else:
        _safe_radio_value(select_key, list(records))
    left, right = st.columns([1, 2])
    with left:
        st.subheader("快捷输入")
        if records:
            chosen_id = st.radio(
                "选择快捷输入", options=list(records),
                format_func=lambda record_id: records[record_id]["title"] + (
                    f" · {records[record_id]['space']}" if hidden_space else ""
                ),
                label_visibility="collapsed", key=select_key,
                index=None if st.session_state.get(new_key) else 0,
                on_change=lambda: st.session_state.pop(new_key, None),
            )
        else:
            chosen_id = ""
            st.info("暂无快捷输入，请填写名称和内容后保存。")
    selected = None if st.session_state.get(new_key) else records.get(chosen_id)
    with right:
        st.subheader("预览" if is_guest else "编辑" if selected else "新建快捷输入")
        record_key = f"{scope}_{selected['id'] if selected else 'new'}"
        title = st.text_input(
            "快捷输入名称", value=selected["title"] if selected else "",
            key=f"quick_input_title_{record_key}", disabled=is_guest,
        )
        content = st.text_area(
            "快捷输入内容", value=selected["content"] if selected else "",
            key=f"quick_input_content_{record_key}", height=420, disabled=is_guest,
            help="聊天时输入 / 或 ／ 选择条目，只填入内容，不会直接发送。",
        )
        save_col, new_col, delete_col = st.columns(3)
        if save_col.button("保存", type="primary", use_container_width=True,
                           disabled=is_guest, key="quick_input_save"):
            try:
                record = quick_inputs.save_quick_input(
                    selected["id"] if selected else "", title, content,
                    hidden_space=hidden_space,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop(new_key, None)
                st.session_state[f"{select_key}_pending"] = record.id
                st.session_state["prompt_settings_flash"] = "已保存快捷输入。"
                st.rerun()
        if new_col.button("新建", use_container_width=True, disabled=is_guest,
                          key="quick_input_new"):
            st.session_state[new_key] = True
            st.session_state.pop(f"quick_input_title_{scope}_new", None)
            st.session_state.pop(f"quick_input_content_{scope}_new", None)
            st.rerun()
        if delete_col.button("删除", use_container_width=True,
                             disabled=is_guest or selected is None, key="quick_input_delete"):
            request_delete("quick_input", selected["id"], selected["title"])

    def _delete(record_id: str) -> None:
        try:
            quick_inputs.delete_quick_input(record_id, hidden_space=hidden_space)
        except ValueError as exc:
            st.session_state["prompt_settings_flash"] = str(exc)
        else:
            st.session_state["prompt_settings_flash"] = "已删除快捷输入。"

    render_delete_confirmation("quick_input", _delete)


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
        hidden_space = _hidden_space()
        _render_text_asset(
            kind="world_book",
            is_guest=is_guest,
            records=prompt_assets.load_world_books(hidden_space=hidden_space),
            title_label="世界书名称",
            content_label="世界书",
            fallback_label="未命名世界书",
            save_record=lambda record_id, title, content: prompt_assets.save_world_book(
                record_id, title, content, hidden_space=hidden_space
            ),
            delete_record=prompt_assets.delete_world_book,
            dialog_state_key="world_book_dialog_open",
            dialog_renderer=_render_world_book_dialog,
        )
        _render_refinement_dialog("world_book")
    elif section == "角色卡":
        _process_pending_refinement("character_card")
        hidden_space = _hidden_space()
        _render_text_asset(
            kind="character_card",
            is_guest=is_guest,
            records=prompt_assets.load_character_cards(hidden_space=hidden_space),
            title_label="角色名称",
            content_label="角色卡",
            fallback_label="未命名角色",
            save_record=lambda record_id, title, content: prompt_assets.save_character_card(
                record_id, title, content, hidden_space=hidden_space
            ),
            delete_record=prompt_assets.delete_character_card,
            dialog_state_key="character_card_dialog_open",
            dialog_renderer=_render_character_card_dialog,
        )
        _render_refinement_dialog("character_card")
    elif section == "固定模板":
        _render_fixed_templates(is_guest)
    elif section == "长期记忆":
        _render_long_term_memories(is_guest)
    elif section == "快捷输入":
        _render_quick_inputs(is_guest)
