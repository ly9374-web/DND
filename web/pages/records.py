from collections.abc import Callable
import uuid

import streamlit as st

from app.services import chat_records
from web.components.delete_confirmation import render_delete_confirmation, request_delete
from web.components.records_list import records_list
from web.nav import goto


_RECORDS_CSS = """
<style>
section[data-testid="stMain"] .block-container {
  padding-top: 60px !important;
}

.st-key-records_actions {
  margin-bottom: 16px;
}
</style>
"""


def _open_record(page: str, record_id: str):
    st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex
    goto(page, record_id=record_id)


def _rename_record_dialog(item, store, scope):
    @st.dialog("重命名")
    def editor():
        with st.form("records_rename_form", enter_to_submit=True):
            new_title = st.text_input("记录名称", key="records_rename_title")
            if st.form_submit_button("确认", type="primary", use_container_width=True):
                store.rename_record(item.id, new_title, scope=scope)
                st.rerun()

    editor()


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
    st.html(_RECORDS_CSS)

    if "records_debug_record_id" not in st.session_state:
        st.session_state.records_debug_record_id = ""

    with st.sidebar:
        st.subheader(sidebar_title)
        st.caption("提示：隐藏账号中产生的聊天记录会自动对普通账号隐藏。")

    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    scope = "guest" if mode == "guest" else None
    items = store.load_index_sorted(scope=scope)
    if not bool(st.session_state.get("hidden_unlocked", False)):
        # 普通账号：隐藏隐藏账号创建的记录（hidden 标记），并兼容旧的「隐藏：」标题前缀。
        items = [
            i
            for i in items
            if not bool(getattr(i, "hidden", False))
            and not str(i.title or "").strip().startswith("隐藏：")
        ]

    if not items:
        st.info(empty_message)
        return

    items_by_id = {item.id: item for item in items}
    selected_id = st.session_state.get("records_selected_id")
    if selected_id not in items_by_id:
        selected_id = items[0].id
    st.session_state.records_selected_id = selected_id

    # Reserve the toolbar above the list while resolving its selected record first.
    actions = st.container(key="records_actions")
    event = records_list(items, selected_id)
    if event and event.get("event_id") != st.session_state.get("records_last_event_id"):
        st.session_state.records_last_event_id = event.get("event_id")
        if event.get("record_id") in items_by_id:
            selected_id = event["record_id"]
            st.session_state.records_selected_id = selected_id
            if event.get("action") == "open":
                _open_record(open_page, selected_id)
            # Send the acknowledged selection back to the component immediately.
            st.rerun()
    item = items_by_id[selected_id]

    has_extra_action = bool(extra_action_label and extra_renderer)
    with actions:
        action_columns = st.columns([1, 1, 1, 1] if has_extra_action else [1, 1, 1])
    with action_columns[0]:
        if st.button("打开", type="primary", use_container_width=True):
            _open_record(open_page, item.id)
    with action_columns[1]:
        if st.button("重命名", use_container_width=True):
            st.session_state.records_rename_title = item.title or ""
            _rename_record_dialog(item, store, scope)
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
