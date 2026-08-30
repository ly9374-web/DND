from __future__ import annotations

from collections.abc import Callable

import streamlit as st


_PENDING_KEY = "ordinary_delete_confirmation"


def request_delete(scope: str, target_id: str, label: str) -> None:
    st.session_state[_PENDING_KEY] = {
        "scope": str(scope or ""),
        "target_id": str(target_id or ""),
        "label": str(label or "该项目"),
    }


def render_delete_confirmation(
    scope: str,
    on_confirm: Callable[[str], None],
    *,
    warning: str = "删除后无法恢复。",
) -> None:
    pending = st.session_state.get(_PENDING_KEY)
    if not isinstance(pending, dict) or pending.get("scope") != str(scope or ""):
        return

    @st.dialog("确认删除", dismissible=False)
    def _dialog():
        st.write(f"确定要删除“{pending.get('label') or '该项目'}”吗？")
        if warning:
            st.warning(warning)
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("确认删除", type="primary", use_container_width=True, key=f"confirm_delete_{scope}"):
                target_id = str(pending.get("target_id") or "")
                st.session_state.pop(_PENDING_KEY, None)
                on_confirm(target_id)
                st.rerun()
        with cancel_col:
            if st.button("取消", use_container_width=True, key=f"cancel_delete_{scope}"):
                st.session_state.pop(_PENDING_KEY, None)
                st.rerun()

    _dialog()
