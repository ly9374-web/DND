from __future__ import annotations

import html
import re
import tempfile
from pathlib import Path

import streamlit as st

from app.api.media_clients import CloudinaryUploader
from app.config import user_facing_error_message
from app.services import stored_urls
from web.components.delete_confirmation import render_delete_confirmation, request_delete


_CSS = """
<style>
/* URL 收藏：窄长上传组件（Streamlit 1.5x 的 dropzone 内部是 button>div 结构） */
[data-testid="stFileUploader"] {
  margin-bottom: 0.3rem;
  gap: 0 !important;
}
[data-testid="stFileUploader"] > label,
[data-testid="stFileUploader"] [data-testid="stWidgetLabel"] {
  margin: 0 !important;
  padding: 0 !important;
  height: 0 !important;
  min-height: 0 !important;
  overflow: hidden !important;
}
[data-testid="stFileUploader"] > div {
  gap: 0 !important;
}
[data-testid="stFileUploaderDropzone"] {
  min-height: 0 !important;
  height: 48px !important;
  max-height: 48px !important;
  padding: 0.25rem 0.8rem !important;
  border-radius: 12px !important;
  background: rgba(31, 41, 55, 0.55) !important;
  border: 1px dashed rgba(255, 255, 255, 0.20) !important;
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  gap: 10px !important;
  overflow: hidden !important;
}
[data-testid="stFileUploaderDropzone"] > button,
[data-testid="stFileUploaderDropzone"] > div {
  width: 100% !important;
  height: 100% !important;
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  gap: 10px !important;
  background: transparent !important;
  border: 0 !important;
  padding: 0 !important;
}
[data-testid="stFileUploaderDropzone"] button > div,
[data-testid="stFileUploaderDropzone"] > div > div {
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 10px !important;
  width: 100% !important;
}
[data-testid="stFileUploaderDropzoneInstructions"] {
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  gap: 6px !important;
  flex-wrap: nowrap !important;
  white-space: nowrap !important;
  overflow: hidden !important;
}
[data-testid="stFileUploaderDropzoneInstructions"] svg {
  display: none !important;
}
[data-testid="stFileUploaderDropzoneInstructions"] > span {
  font-size: 13px !important;
  white-space: nowrap !important;
}
[data-testid="stFileUploaderDropzoneInstructions"] small {
  font-size: 11px !important;
  color: rgba(229, 231, 235, 0.45) !important;
  white-space: nowrap !important;
}
/* 上传成功后的文件行紧凑一点 */
[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {
  padding: 4px 8px !important;
}
</style>
"""


def _show_error(exc: Exception):
    st.error(user_facing_error_message(exc))


def _hidden_unlocked() -> bool:
    return bool(st.session_state.get("hidden_unlocked", False))


def _safe_id(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(key))


def render_url_display_with_copy(url: str, key: str, label: str = "图片 URL（可复制）") -> None:
    """渲染 URL 显示框，复制按钮通过 flex 布局固定在框右侧（同一 iframe 内，无独立空间）。"""
    text = str(url or "")
    if not text:
        return
    btn_id = f"copybtn_{_safe_id(key)}"
    safe_text = html.escape(text, quote=True)
    st.iframe(
        f"""
<div style="font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC',sans-serif;">
<p style="font-size:13px;color:rgba(229,231,235,0.72);margin:0 0 4px 2px;">{html.escape(label)}</p>
<div style="display:flex;align-items:stretch;gap:8px;">
  <div title="{safe_text}" style="flex:1;min-width:0;display:flex;align-items:center;height:40px;
       padding:0 12px;border-radius:8px;border:1px solid rgba(255,255,255,0.12);
       background:rgba(17,24,39,0.6);overflow:hidden;">
    <span style="font-size:12.5px;color:#d1d5db;white-space:nowrap;overflow:hidden;
          text-overflow:ellipsis;display:block;">{safe_text}</span>
  </div>
  <button id="{btn_id}" type="button" data-text="{safe_text}"
    style="height:40px;min-width:64px;padding:0 14px;border-radius:8px;cursor:pointer;
           border:1px solid rgba(255,255,255,0.14);background:rgba(31,41,55,0.9);
           color:#e5e7eb;font-size:13px;font-weight:600;flex-shrink:0;">复制</button>
</div>
</div>
<script>
(function() {{
  var btn = document.getElementById("{btn_id}");
  var original = btn.textContent;
  btn.addEventListener("click", async function() {{
    var text = btn.getAttribute("data-text");
    var ok = false;
    try {{
      await navigator.clipboard.writeText(text);
      ok = true;
    }} catch (err) {{
      try {{
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      }} catch (err2) {{
        ok = false;
      }}
    }}
    btn.textContent = ok ? "已复制" : "复制失败";
    setTimeout(function() {{ btn.textContent = original; }}, 1500);
  }});
}})();
</script>
        """,
        height=68,
    )


def render_header_with_copy(url: str, key: str, label: str = "收藏列表") -> None:
    """标题行：左侧标题 + 右侧复制按钮，flex 布局固定（同一 iframe 内，按钮无独立空间）。"""
    text = str(url or "")
    if not text:
        st.markdown(
            f'<p style="font-size:14px;font-weight:600;color:#e5e7eb;margin:0 0 4px 2px;">{html.escape(label)}</p>',
            unsafe_allow_html=True,
        )
        return
    btn_id = f"copybtn_{_safe_id(key)}"
    safe_text = html.escape(text, quote=True)
    st.iframe(
        f"""
<div style="font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC',sans-serif;">
<div style="display:flex;align-items:center;gap:8px;">
  <p style="font-size:14px;font-weight:600;color:#e5e7eb;margin:0;flex:1;min-width:0;">{html.escape(label)}</p>
  <button id="{btn_id}" type="button" data-text="{safe_text}"
    style="height:30px;min-width:60px;padding:0 12px;border-radius:8px;cursor:pointer;
           border:1px solid rgba(255,255,255,0.14);background:rgba(31,41,55,0.9);
           color:#e5e7eb;font-size:13px;font-weight:600;flex-shrink:0;">复制</button>
</div>
</div>
<script>
(function() {{
  var btn = document.getElementById("{btn_id}");
  var original = btn.textContent;
  btn.addEventListener("click", async function() {{
    var text = btn.getAttribute("data-text");
    var ok = false;
    try {{
      await navigator.clipboard.writeText(text);
      ok = true;
    }} catch (err) {{
      try {{
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      }} catch (err2) {{
        ok = false;
      }}
    }}
    btn.textContent = ok ? "已复制" : "复制失败";
    setTimeout(function() {{ btn.textContent = original; }}, 1500);
  }});
}})();
</script>
        """,
        height=38,
    )


def get_preview_url(prefix: str) -> str:
    """媒体区读取「显示图片」要预览的收藏 URL（空串表示无）。"""
    return str(st.session_state.get(f"{prefix}_url_preview", "") or "")


def clear_preview_url(prefix: str) -> None:
    st.session_state.pop(f"{prefix}_url_preview", None)
    st.session_state.pop(f"{prefix}_url_preview_media_id", None)


def activate_generated_media_selection(
    prefix: str,
    selected_id_key: str,
    selectbox_key: str,
) -> None:
    """Switch from a favorite URL preview back to the selected generated media."""
    selected_id = str(st.session_state.get(selectbox_key, "") or "")
    current_selected_id = str(st.session_state.get(selected_id_key, "") or "")
    if prefix == "page2" and (not selected_id or selected_id == current_selected_id):
        return
    if selected_id:
        st.session_state[selected_id_key] = selected_id
    clear_preview_url(prefix)


def _render_uploader(prefix: str, url_state) -> None:
    # file_uploader 无法从服务端清空（固定 key 时前端会把同一文件回传、重复上传），
    # 因此上传成功后递增 nonce 换新 key，强制组件重置
    nonce_key = f"{prefix}_url_uploader_nonce"
    nonce = int(st.session_state.get(nonce_key, 0) or 0)
    uploader_key = f"{prefix}_url_uploader_{nonce}"

    uploaded = st.file_uploader(
        "上传图片获取 URL（Cloudinary）",
        type=["png", "jpg", "jpeg", "webp"],
        key=uploader_key,
        label_visibility="collapsed",
    )
    if uploaded is None:
        return

    with st.spinner("上传中..."):
        try:
            suffix = Path(uploaded.name).suffix or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getvalue())
                tmp_path = tmp.name
            secure_url = CloudinaryUploader.upload_image(tmp_path)
        except Exception as exc:
            _show_error(exc)
            return

    try:
        stored_urls.add_url(url_state, secure_url, title=Path(uploaded.name).stem)
    except Exception as exc:
        _show_error(exc)
        return

    # 换新 key 并清掉旧 key 的残留状态（旧组件不再渲染，前端不会回传）
    st.session_state.pop(uploader_key, None)
    st.session_state[nonce_key] = nonce + 1
    st.rerun()


def _render_rename_dialog(prefix: str, record) -> None:
    if hasattr(st, "dialog"):
        @st.dialog("重命名")
        def _dialog():
            new_title = st.text_input("新名称", value=str(record.title or ""), key=f"{prefix}_url_rename_input")
            if st.button("保存", type="primary", use_container_width=True):
                if str(new_title or "").strip():
                    state = stored_urls.load_state(hidden_space=_hidden_unlocked())
                    stored_urls.rename_url(state, record.id, new_title)
                    st.rerun()

        _dialog()
        return

    # 旧版 Streamlit 没有 st.dialog：退化为行内输入
    new_title = st.text_input("新名称", value=str(record.title or ""), key=f"{prefix}_url_rename_input")
    if st.button("保存名称", use_container_width=True):
        if str(new_title or "").strip():
            state = stored_urls.load_state(hidden_space=_hidden_unlocked())
            stored_urls.rename_url(state, record.id, new_title)
            st.rerun()


def render_url_favorites(prefix: str) -> None:
    """渲染「URL 收藏」区块（上传 / 收藏列表 / 删除 / 重命名 / 显示图片 / 复制）。"""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.divider()
    st.subheader("URL 收藏")

    url_state = stored_urls.load_state(hidden_space=_hidden_unlocked())
    _render_uploader(prefix, url_state)

    visible = stored_urls.visible_records(url_state)
    if not visible:
        st.caption("暂无收藏，上传图片后会自动加入收藏列表。")
        return

    ids = [r.id for r in visible]
    if st.session_state.pop(f"{prefix}_url_reset_selection", False):
        st.session_state.pop(f"{prefix}_url_selected_id", None)
        st.session_state.pop(f"{prefix}_url_select", None)

    def _label_of(rid: str) -> str:
        for r in visible:
            if r.id == rid:
                prefix_tag = "隐藏：" if r in url_state.hidden_records else ""
                return f"{prefix_tag}{r.title or r.url}"
        return rid

    # 标题行：收藏列表 + 复制按钮
    # 选中值与下方 selectbox 保持一致：优先读控件自身状态，其次回退到记录 id，最后默认最后一项
    select_state = st.session_state.get(f"{prefix}_url_select")
    selected_id_state = str(st.session_state.get(f"{prefix}_url_selected_id", "") or "")
    if select_state in ids:
        effective_id = select_state
    elif selected_id_state in ids:
        effective_id = selected_id_state
    else:
        effective_id = ids[-1]
    current_record = next((r for r in visible if r.id == effective_id), None)

    # 标题行：左标题 + 右复制按钮（同一 iframe，flex 固定，无独立空间）
    if current_record is not None:
        render_header_with_copy(
            current_record.url,
            key=f"{prefix}_url_copy",
        )
    else:
        st.markdown(
            '<p style="font-size:14px;font-weight:600;color:#e5e7eb;margin:0 0 4px 2px;">收藏列表</p>',
            unsafe_allow_html=True,
        )

    index = ids.index(effective_id) if effective_id in ids else len(ids) - 1
    chosen_id = st.selectbox(
        "收藏列表",
        options=ids,
        index=index,
        format_func=_label_of,
        key=f"{prefix}_url_select",
        label_visibility="collapsed",
    )
    st.session_state[f"{prefix}_url_selected_id"] = chosen_id

    record = next((r for r in visible if r.id == chosen_id), None)
    if record is None:
        return

    def _delete(record_id: str) -> None:
        target = next((item for item in visible if item.id == record_id), None)
        stored_urls.delete_url(url_state, record_id)
        st.session_state[f"{prefix}_url_reset_selection"] = True
        if target is not None and get_preview_url(prefix) == target.url:
            clear_preview_url(prefix)

    action_cols = st.columns([1, 1, 1])
    with action_cols[0]:
        if st.button("删除", use_container_width=True):
            request_delete(f"{prefix}_stored_url", record.id, record.title or record.url)
    with action_cols[1]:
        if st.button("重命名", use_container_width=True):
            _render_rename_dialog(prefix, record)
    with action_cols[2]:
        if st.button("显示图片", use_container_width=True):
            if get_preview_url(prefix) == record.url:
                clear_preview_url(prefix)
            else:
                st.session_state[f"{prefix}_url_preview"] = record.url
            st.rerun()

    render_delete_confirmation(f"{prefix}_stored_url", _delete)
