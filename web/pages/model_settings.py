import html
import re

import streamlit as st

from app.config import (
    AppStorageKeys,
    has_invalid_streamlit_secret,
    has_streamlit_secret,
    is_latin1_api_value,
    settings,
)
from web.nav import back
from web.components.delete_confirmation import render_delete_confirmation, request_delete


FIELDS = [
    (
        "输入 Grok 聊天 API Key（会本地持久化保存）",
        AppStorageKeys.XAI_CHAT_API_KEY,
        "GROK_CHAT_API_KEY",
        "GROK_CHAT_API_KEY",
    ),
    (
        "输入 Grok 生图 API Key（会本地持久化保存）",
        AppStorageKeys.XAI_IMAGE_API_KEY,
        "GROK_IMAGE_API_KEY",
        "GROK_IMAGE_API_KEY",
    ),
    (
        "输入 Replicate API Token（会本地持久化保存）",
        AppStorageKeys.REPLICATE_API_TOKEN,
        "REPLICATE_API_TOKEN",
        "REPLICATE_API_TOKEN",
    ),
    (
        "输入 DeepSeek API Key（会本地持久化保存）",
        AppStorageKeys.DEEPSEEK_API_KEY,
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_API_KEY",
    ),
    (
        "输入 DomoAI API Key（会本地持久化保存）",
        AppStorageKeys.DOMOAI_API_KEY,
        "DOMOAI_API_KEY",
        "DOMOAI_API_KEY",
    ),
    (
        "输入 智谱 API Key（会本地持久化保存）",
        AppStorageKeys.ZHIPU_API_KEY,
        "ZHIPU_API_KEY",
        "ZHIPU_API_KEY",
    ),
    (
        "输入 Cloudinary API Key（会本地持久化保存）",
        AppStorageKeys.CLOUDINARY_API_KEY,
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_KEY",
    ),
    (
        "输入 Cloudinary API Secret（会本地持久化保存）",
        AppStorageKeys.CLOUDINARY_API_SECRET,
        "CLOUDINARY_API_SECRET",
        "CLOUDINARY_API_SECRET",
    ),
]


def _pending_key(storage_key: str) -> str:
    return f"model_settings_pending_{storage_key}"


def _flash_key(storage_key: str) -> str:
    return f"model_settings_flash_{storage_key}"


def _key_status(storage_key: str, secret_name: str) -> str:
    saved = str(settings.get(storage_key, "") or "").strip()
    if saved:
        if not is_latin1_api_value(saved):
            return "已填入（字符异常）"
        return "已填入"

    if has_invalid_streamlit_secret(secret_name):
        return "默认 Key 字符异常"

    if has_streamlit_secret(secret_name):
        return "已配置默认 Key"

    return "未填写"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value))


def _render_copy_button(value: str, storage_key: str):
    text = str(value or "")
    button_id = f"copy_api_key_{_safe_id(storage_key)}"
    safe_text = html.escape(text, quote=True)
    disabled = "disabled" if not text else ""

    st.iframe(
        f"""
<style>
html, body {{ margin: 0; padding: 0; background: transparent; }}
.copy-button {{
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  background: rgba(31, 41, 55, 0.9);
  color: #e5e7eb;
  cursor: pointer;
}}
.copy-button:hover:not(:disabled) {{ border-color: rgba(255, 255, 255, 0.28); }}
.copy-button:disabled {{ cursor: not-allowed; opacity: 0.38; }}
</style>
<button id="{button_id}" class="copy-button" type="button" data-text="{safe_text}"
        title="复制 API" aria-label="复制 API" {disabled}>
  <svg class="copy-icon" width="19" height="19" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
       aria-hidden="true">
    <rect x="9" y="9" width="13" height="13" rx="2"></rect>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
  </svg>
</button>
<script>
(function() {{
  var btn = document.getElementById("{button_id}");
  if (!btn || btn.disabled) return;
  var original = btn.innerHTML;
  btn.addEventListener("click", async function() {{
    var text = btn.getAttribute("data-text") || "";
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
      }} catch (fallbackError) {{
        ok = false;
      }}
    }}
    btn.innerHTML = ok
      ? '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"></path></svg>'
      : original;
    btn.title = ok ? "已复制" : "复制失败";
    setTimeout(function() {{ btn.innerHTML = original; btn.title = "复制 API"; }}, 1500);
  }});
}})();
</script>
        """,
        height=40,
    )


def _save_single(storage_key: str):
    pending = str(st.session_state.get(_pending_key(storage_key), "") or "").strip()
    if not pending:
        return
    settings.set(storage_key, pending)
    st.session_state[_flash_key(storage_key)] = "saved"


def _delete_single(storage_key: str):
    settings.set(storage_key, "")
    st.session_state[_pending_key(storage_key)] = ""
    st.session_state[_flash_key(storage_key)] = "deleted"


def _save_debug_enabled():
    settings.set(
        AppStorageKeys.DEBUG_LOG_ENABLED,
        bool(st.session_state.get("model_settings_debug_log_enabled", False)),
    )


def render():
    st.title("APIkey")

    st.caption("已保存的 Key 会在本页始终以明文显示；可直接修改、复制或删除。")

    st.checkbox(
        "打印调试日志",
        value=settings.bool(AppStorageKeys.DEBUG_LOG_ENABLED, False),
        help="关闭后不会在控制台打印请求体、响应体和调试信息，可减少卡顿。",
        key="model_settings_debug_log_enabled",
        on_change=_save_debug_enabled,
    )

    st.subheader("API Keys")
    st.caption("在输入框里按回车会立即保存该 Key。")

    for label_text, key, placeholder, secret_name in FIELDS:
        st.markdown(f"**{label_text}**")
        input_key = _pending_key(key)
        if input_key not in st.session_state:
            st.session_state[input_key] = str(settings.get(key, "") or "")

        c1, c2, c3, c4 = st.columns([3, 0.35, 1, 1])
        with c1:
            st.text_input(
                "输入 API（回车保存）",
                placeholder=placeholder,
                label_visibility="collapsed",
                key=input_key,
                on_change=_save_single,
                args=(key,),
            )
        with c2:
            _render_copy_button(str(st.session_state.get(input_key, "") or ""), key)
        with c3:
            st.caption(_key_status(key, secret_name))
        with c4:
            if st.button(
                "删除",
                use_container_width=True,
                type="secondary",
                key=f"model_settings_delete_btn_{key}",
            ):
                request_delete(f"api_key_{key}", key, placeholder)

        render_delete_confirmation(
            f"api_key_{key}",
            lambda storage_key: _delete_single(storage_key),
            warning="这会清除本地保存的值；Streamlit Secrets 中的默认值不会被删除。",
        )

        flash = st.session_state.pop(_flash_key(key), None)
        if flash == "saved":
            st.success("已保存")
        elif flash == "deleted":
            st.success("已删除")

        st.divider()

    st.subheader("其他")
    if st.button("返回", use_container_width=True):
        back()
