from collections.abc import Callable

import streamlit as st

from app.services import chat_records
from web.components.delete_confirmation import render_delete_confirmation, request_delete
from web.nav import goto


def _label(item):
    title = item.title or "未命名聊天"
    updated_at = item.updated_at or ""
    return f"{title}    {updated_at}"


def render(
    *,
    page_title: str = "记录",
    sidebar_title: str = "聊天记录",
    store=chat_records,
    open_page: str = "main",
    empty_message: str = "暂无聊天记录。去「开始」发一条消息后会自动保存。",
    extra_action_label: str = "",
    extra_renderer: Callable | None = None,
    confirm_delete: bool = True,
):
    st.title(page_title)

    if "records_debug_record_id" not in st.session_state:
        st.session_state.records_debug_record_id = ""

    with st.sidebar:
        st.subheader(sidebar_title)
        st.caption("提示：把聊天标题改成以「隐藏：」开头，可在未解锁时隐藏。")

    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    scope = "guest" if mode == "guest" else None
    items = store.load_index_sorted(scope=scope)
    if not bool(st.session_state.get("hidden_unlocked", False)):
        items = [i for i in items if not str(i.title or "").strip().startswith("隐藏：")]

    if not items:
        st.info(empty_message)
        return

    labels = [_label(item) for item in items]
    label_to_item = {labels[i]: items[i] for i in range(len(items))}

    selected = st.radio(
        "选择记录",
        options=labels,
        label_visibility="collapsed",
    )
    item = label_to_item.get(selected)
    if item is None:
        return

    has_extra_action = bool(extra_action_label and extra_renderer)
    action_columns = st.columns([1, 1, 1, 1] if has_extra_action else [1, 1, 1])
    with action_columns[0]:
        if st.button("打开", type="primary", use_container_width=True):
            goto(open_page, record_id=item.id)
    with action_columns[1]:
        new_title = st.text_input("重命名", value=item.title or "", key="records_rename_title")
        if st.button("保存名称", use_container_width=True):
            store.rename_record(item.id, new_title, scope=scope)
            st.success("已重命名")
            st.rerun()
    with action_columns[2]:
        if st.button("删除", use_container_width=True):
            if confirm_delete:
                request_delete("ordinary_chat_record", item.id, item.title or "未命名聊天")
            else:
                store.delete_record(item.id, scope=scope)
                st.rerun()

    def _delete(record_id: str) -> None:
        store.delete_record(record_id, scope=scope)

    if confirm_delete:
        render_delete_confirmation("ordinary_chat_record", _delete)

    if has_extra_action:
        with action_columns[3]:
            if st.button(extra_action_label, use_container_width=True):
                st.session_state.records_debug_record_id = item.id

        if st.session_state.records_debug_record_id == item.id:
            extra_renderer(item, scope)
