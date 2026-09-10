from __future__ import annotations

import base64
import json
import threading
import uuid
from copy import deepcopy
from functools import partial
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from app.config import AppStorageKeys, OpenRouterConfig, settings, user_facing_error_message
from app.api.media_clients import download_video_bytes, is_openrouter_video_url
from app.video_models import (
    VIDEO_MODELS, WAN_MODEL, WAN_DURATION_FRAMES, WAN_FPS,
    normalize_video_model, video_duration_label,
)
from app.models import (
    CHAT_MODEL_LABELS,
    CHAT_MODEL_OPTIONS,
    GeneratedMediaKind,
    Page2ConversationTurn,
)
from app.services import chat_records, page2_service, quick_inputs, story_brain_history, system_prompts
from app.services.video_prompts import (
    MissingVideoStorySource, latest_image_prompt_source, resolve_video_story,
)
from web.components.video_prompt import render_editor_sizing, render_toast_style
from web.components.quick_input import quick_input_script
from web.nav import get_arg, goto
from web.pages import url_favorites
from web.components.delete_confirmation import render_delete_confirmation, request_delete


_story_brain_text_editor_component = components.declare_component(
    "page2_story_brain_text_editor",
    path=str(Path(__file__).resolve().parents[1] / "components" / "story_brain_text_editor"),
)

_SINGLE_FLIGHT_OPERATION_KINDS = {
    "chat",
    "resend",
    "story_brain",
    "image_prompt",
    "video_prompt",
    "image",
    "video",
}

_EMPTY_SPACE_CONTINUE_MESSAGE = "继续发展"
_EMPTY_SPACE_SUBMISSION_SENTINEL = "\u2063\u2063\u2063"
_MANUAL_STORY_BRAIN_UPDATE_DELAY = 5
_HIDDEN_ASSISTANT_MESSAGE_PREFIXES = (
    "本轮不掷骰子",
    "本轮掷骰子",
)


def _chat_scope() -> str | None:
    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    return "guest" if mode == "guest" else None


def _decode_image_base64(b64: str) -> bytes:
    return base64.b64decode(b64.encode("ascii"))


def _latest_image_media_id(media: list) -> str:
    for item in reversed(media or []):
        kind = item.media_kind.value if hasattr(item.media_kind, "value") else str(item.media_kind)
        if kind == GeneratedMediaKind.IMAGE.value:
            return str(item.id or "")
    return str(media[-1].id or "") if media else ""


def _show_error(exc: Exception):
    st.error(user_facing_error_message(exc))


def _save_video_model_selection():
    settings.set(
        AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER,
        normalize_video_model(st.session_state["page2_video_model"]),
    )


def _render_video_model_selector():
    key = "page2_video_model"
    if st.session_state.get(key) not in VIDEO_MODELS:
        st.session_state[key] = normalize_video_model(
            settings.get(AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER, WAN_MODEL)
        )
    return st.selectbox(
        "视频模型", options=list(VIDEO_MODELS), key=key,
        format_func=lambda model: VIDEO_MODELS[model].label,
        on_change=_save_video_model_selection,
    )


def _render_video_duration_controls(model):
    spec = VIDEO_MODELS[model]
    key = "page2_video_seconds"
    previous = st.session_state.get(key)
    if previous not in spec.durations:
        st.session_state[key] = 5
        if previous is not None:
            st.caption("之前的时长不适用于当前模型，已调整为 5 秒。")
    seconds = st.selectbox(
        "时长（秒）", options=spec.durations, key=key,
        format_func=lambda value: video_duration_label(model, value),
    )
    if model == WAN_MODEL:
        st.caption(f"720p · 约 {WAN_DURATION_FRAMES[seconds] / WAN_FPS:.2f} 秒 · 无声音")
        return seconds, False
    generate_audio = st.toggle("生成声音", value=True, key="page2_video_generate_audio")
    st.caption("720p · 比例跟随输入图片；需要说话时，请在 prompt 中写明台词。")
    return seconds, generate_audio


def _render_video_preview(record):
    url = record.video_url_string
    authenticated = is_openrouter_video_url(url)
    cache_key = "page2_video_preview_bytes"
    identity = (_operation_scope_id(), record.id, url)
    cached = st.session_state.get(cache_key) or {}
    if cached.get("identity") != identity:
        st.session_state.pop(cache_key, None)
        cached = {}
    try:
        api_key = OpenRouterConfig.api_key() if authenticated else ""
    except Exception as exc:
        _show_error(exc)
        return
    # A callable defers retrieval until the user actually clicks Download.
    fetch_video = partial(download_video_bytes, url, api_key=api_key)
    if authenticated:
        if not cached.get("data"):
            if st.button("加载视频播放", key=f"page2_load_video_{record.id}"):
                try:
                    with st.spinner("正在加载视频…"):
                        cached = {"identity": identity, "data": fetch_video()}
                    st.session_state[cache_key] = cached
                except Exception as exc:
                    _show_error(exc)
        if cached.get("data"):
            st.video(cached["data"], format="video/mp4", muted=False, autoplay=False)
    else:
        st.video(url, muted=False, autoplay=False)
    st.download_button(
        "下载视频", data=cached.get("data") or fetch_video,
        file_name=f"video-{record.id}.mp4", mime="video/mp4",
        key=f"page2_download_video_{record.id}", on_click="ignore",
    )
    st.caption("视频不会自动保存；需要保留请点击下载。")


def _render_empty_space_submit_bridge(*, send_disabled: bool = False) -> None:
    """Keep drafts editable while blocking submission, including blank shortcuts."""
    sentinel_json = json.dumps(_EMPTY_SPACE_SUBMISSION_SENTINEL, ensure_ascii=False)
    hidden_space = bool(st.session_state.get("hidden_unlocked", False))
    components.html(
        quick_input_script(
            quick_inputs.quick_input_options(hidden_space=hidden_space),
            scope=f"{hidden_space}:{st.session_state.get('page2_record_id', '')}",
            show_space=hidden_space,
        ) + f"""
        <script>
          (() => {{
            const host = window.parent;
            const doc = host.document;
            const sendDisabled = {json.dumps(send_disabled)};
            const handlerKey = "__dndEmptySpaceSubmitHandler";
            const previousHandler = host[handlerKey];
            if (previousHandler) {{
              doc.removeEventListener("keydown", previousHandler, true);
            }}
            const previousClickHandler = host.__dndChatSubmitClickHandler;
            if (previousClickHandler) {{
              doc.removeEventListener("click", previousClickHandler, true);
            }}
            host.__dndChatSubmitObserver?.disconnect();

            const syncSubmitButton = () => {{
              const button = doc.querySelector(
                ".st-key-page2_chat_input [data-testid='stChatInputSubmitButton']"
              );
              if (!button) return;
              if (sendDisabled) {{
                button.setAttribute("aria-disabled", "true");
                button.style.pointerEvents = "none";
                button.style.opacity = "0.4";
              }} else {{
                button.removeAttribute("aria-disabled");
                button.style.removeProperty("pointer-events");
                button.style.removeProperty("opacity");
              }}
            }};
            const observer = new host.MutationObserver(syncSubmitButton);
            observer.observe(doc.body, {{ childList: true, subtree: true }});
            host.__dndChatSubmitObserver = observer;
            syncSubmitButton();

            const clickHandler = (event) => {{
              if (sendDisabled && event.target.closest?.(
                ".st-key-page2_chat_input [data-testid='stChatInputSubmitButton']"
              )) {{
                event.preventDefault();
                event.stopImmediatePropagation();
              }}
            }};
            host.__dndChatSubmitClickHandler = clickHandler;
            doc.addEventListener("click", clickHandler, true);

            const handler = (event) => {{
              if (host.__dndQuickInput?.handleKey(event)) return;
              const isEnter = event.key === "Enter";
              const isSpace = event.key === " ";
              if (sendDisabled) {{
                if (isEnter && !event.shiftKey && !event.isComposing &&
                    event.keyCode !== 229 && event.target.matches?.(
                      ".st-key-page2_chat_input textarea"
                    )) {{
                  event.preventDefault();
                  event.stopImmediatePropagation();
                }}
                return;
              }}
              if (
                (!isEnter && !isSpace) ||
                event.repeat ||
                event.isComposing ||
                event.keyCode === 229 ||
                event.altKey ||
                event.ctrlKey ||
                event.metaKey ||
                event.shiftKey
              ) {{
                return;
              }}

              const input = doc.querySelector(
                ".st-key-page2_chat_input textarea"
              );
              if (
                !input ||
                event.target !== input ||
                input.disabled ||
                String(input.value || "").trim() !== ""
              ) {{
                return;
              }}

              event.preventDefault();
              event.stopPropagation();

              const valueSetter = Object.getOwnPropertyDescriptor(
                host.HTMLTextAreaElement.prototype,
                "value"
              )?.set;
              if (!valueSetter) {{
                return;
              }}
              valueSetter.call(input, {sentinel_json});
              input.dispatchEvent(new host.Event("input", {{ bubbles: true }}));

              host.requestAnimationFrame(() => {{
                const submitButton = doc.querySelector(
                  ".st-key-page2_chat_input [data-testid='stChatInputSubmitButton']"
                );
                if (submitButton) {{
                  submitButton.click();
                  return;
                }}
                input.dispatchEvent(
                  new host.KeyboardEvent("keydown", {{
                    key: "Enter",
                    code: "Enter",
                    bubbles: true,
                    cancelable: true,
                  }})
                );
              }});
            }};

            host[handlerKey] = handler;
            doc.addEventListener("keydown", handler, true);
          }})();
        </script>
        """,
        height=0,
    )


def _ensure_state():
    st.session_state.setdefault("page2_record_id", "")
    st.session_state.setdefault("page2_loaded_record_id", "")
    st.session_state.setdefault(
        "page2_final_setting_id",
        str(settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or ""),
    )
    st.session_state.setdefault("page2_turns", [])
    st.session_state.setdefault("page2_generated_media", [])
    st.session_state.setdefault("page2_selected_media_id", "")
    st.session_state.setdefault("page2_pending_media_record_select", "")
    st.session_state.setdefault("page2_image_prompt", "")
    st.session_state.setdefault("page2_image_prompt_mode", "normal")
    st.session_state.setdefault("page2_image_prompt_subject", "")
    st.session_state.setdefault("page2_image_prompt_source", {})
    st.session_state.setdefault("page2_video_prompt", "")
    st.session_state.setdefault("page2_video_story_toast", None)
    st.session_state.setdefault("page2_story_brain_archive", {})
    st.session_state.setdefault("page2_story_brain_short", "")
    st.session_state.setdefault("page2_story_brain_enabled", True)
    st.session_state.setdefault("page2_story_brain_short_editor_nonce", 0)
    st.session_state.setdefault("page2_story_brain_short_update_notice", "")
    st.session_state.setdefault("page2_story_brain_retry_pending", False)
    st.session_state.setdefault("page2_story_brain_next_update_turn", None)
    st.session_state.setdefault("page2_story_brain_last_update_turn_id", "")
    st.session_state.setdefault("page2_story_brain_checkpoints", [])
    st.session_state.setdefault("page2_operation_queue", [])
    st.session_state.setdefault("page2_active_operation", None)
    st.session_state.setdefault("page2_operation_scope_id", uuid.uuid4().hex)
    st.session_state.setdefault("page2_operation_notice", "")
    st.session_state.setdefault("page2_operation_error", "")
    st.session_state.setdefault("page2_operation_outputs", {})
    st.session_state.setdefault("page2_chat_edit_target", None)


class _Page2OperationTask:
    """单个普通模式队列任务；后台线程只执行网络请求，不读写 Streamlit 状态。"""

    def __init__(
        self,
        *,
        kind: str,
        label: str,
        payload: dict | None = None,
        scope_id: str,
    ):
        self.id = uuid.uuid4().hex
        self.kind = str(kind or "")
        self.label = str(label or kind or "操作")
        self.payload = dict(payload or {})
        self.scope_id = str(scope_id or "")
        self.meta: dict = {}
        self._fn = None
        self.result = None
        self.error: Exception | None = None
        self._started = False
        self._start_lock = threading.Lock()
        self._done = threading.Event()

    def start(self, fn) -> None:
        with self._start_lock:
            if self._started:
                return
            self._fn = fn
            self._started = True
            threading.Thread(target=self._run, daemon=True).start()

    def fail_before_start(self, exc: Exception) -> None:
        self.error = exc
        self._started = True
        self._done.set()

    def _run(self) -> None:
        try:
            self.result = self._fn()
        except Exception as exc:
            self.error = exc
        finally:
            self._done.set()

    def is_started(self) -> bool:
        return self._started

    def is_done(self) -> bool:
        return self._done.is_set()


def page2_operation_queue_busy() -> bool:
    return bool(
        st.session_state.get("page2_active_operation") is not None
        or st.session_state.get("page2_operation_queue")
    )


def _operation_kind_busy(kind: str) -> bool:
    """Return whether this kind of request is already running or queued."""
    active = st.session_state.get("page2_active_operation")
    kinds = {"chat", "resend"} if kind == "chat" else {kind}
    if active is not None and active.kind in kinds:
        return True
    return any(
        task.kind in kinds
        for task in list(st.session_state.get("page2_operation_queue") or [])
    )


def _story_brain_update_busy() -> bool:
    """保留原有界面锁定判断；现在任何队列操作执行时都禁止破坏性编辑。"""
    return page2_operation_queue_busy()


def _operation_scope_id() -> str:
    scope_id = str(st.session_state.get("page2_operation_scope_id", "") or "")
    if not scope_id:
        scope_id = uuid.uuid4().hex
        st.session_state["page2_operation_scope_id"] = scope_id
    return scope_id


def _enqueue_operation(
    kind: str,
    label: str,
    payload: dict | None = None,
    *,
    first: bool = False,
    allow_same_kind: bool = False,
) -> _Page2OperationTask:
    if kind in _SINGLE_FLIGHT_OPERATION_KINDS and not allow_same_kind:
        existing = _latest_earlier_operation(kind)
        if existing is not None:
            st.session_state["page2_operation_notice"] = (
                f"{existing.label}已在执行或队列中，请等待完成。"
            )
            return existing

    task = _Page2OperationTask(
        kind=kind,
        label=label,
        payload=payload,
        scope_id=_operation_scope_id(),
    )
    queue = list(st.session_state.get("page2_operation_queue") or [])
    if first:
        queue.insert(0, task)
    else:
        queue.append(task)
    st.session_state["page2_operation_queue"] = queue
    st.session_state["page2_operation_notice"] = f"已加入队列：{task.label}"
    return task


def _has_earlier_operation(kind: str) -> bool:
    return _latest_earlier_operation(kind) is not None


def _latest_earlier_operation(kind: str) -> _Page2OperationTask | None:
    operations = []
    active = st.session_state.get("page2_active_operation")
    if active is not None:
        operations.append(active)
    operations.extend(list(st.session_state.get("page2_operation_queue") or []))
    kinds = {"chat", "resend"} if kind == "chat" else {kind}
    matches = [task for task in operations if task.kind in kinds]
    return matches[-1] if matches else None


def _chat_edit_widget_key(turn_id: str, role: str) -> str:
    return f"page2_chat_edit_text_{role}_{turn_id}"


def _chat_edit_target() -> dict:
    target = st.session_state.get("page2_chat_edit_target")
    return target if isinstance(target, dict) else {}


def _chat_edit_active() -> bool:
    target = _chat_edit_target()
    return bool(target.get("turn_id") and target.get("role") in {"user", "assistant"})


def _open_chat_edit(turn_id: str, role: str, text: str) -> bool:
    """Open one native message editor unless Story Brain is updating."""
    if _story_brain_update_busy() or role not in {"user", "assistant"} or not turn_id:
        return False

    st.session_state["page2_chat_edit_target"] = {
        "turn_id": str(turn_id),
        "role": str(role),
    }
    st.session_state[_chat_edit_widget_key(turn_id, role)] = str(text or "")
    return True


def _close_chat_edit() -> None:
    target = _chat_edit_target()
    turn_id = str(target.get("turn_id", "") or "")
    role = str(target.get("role", "") or "")
    if turn_id and role:
        st.session_state.pop(_chat_edit_widget_key(turn_id, role), None)
    st.session_state["page2_chat_edit_target"] = None


def start_new_conversation(reset_settings: bool = False):
    _ensure_state()
    if page2_operation_queue_busy():
        st.session_state["page2_operation_error"] = "队列执行中，请等待所有操作完成后再新建对话。"
        return False
    if reset_settings:
        page2_service.reset_context_settings()
        st.session_state.pop("page2_prompt_record_select", None)
    st.session_state.page2_record_id = ""
    st.session_state.page2_loaded_record_id = ""
    st.session_state.page2_final_setting_id = str(
        settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or ""
    )
    st.session_state.page2_turns = []
    st.session_state.page2_generated_media = []
    st.session_state.page2_selected_media_id = ""
    st.session_state.page2_pending_media_record_select = ""
    url_favorites.clear_preview_url("page2")
    st.session_state.page2_image_prompt = ""
    st.session_state.page2_image_prompt_mode = "normal"
    st.session_state.page2_image_prompt_subject = ""
    st.session_state.page2_image_prompt_source = {}
    st.session_state.page2_video_prompt = ""
    st.session_state.page2_video_story_toast = None
    st.session_state.pop("page2_video_story_dialog", None)
    st.session_state.pop("page2_pending_video_source", None)
    st.session_state.page2_story_brain_archive = {}
    st.session_state.page2_story_brain_short = ""
    st.session_state.page2_story_brain_enabled = True
    st.session_state["page2_story_brain_short_update_notice"] = ""
    st.session_state["page2_story_brain_retry_pending"] = False
    st.session_state["page2_story_brain_next_update_turn"] = None
    st.session_state["page2_story_brain_last_update_turn_id"] = ""
    st.session_state["page2_story_brain_checkpoints"] = []
    st.session_state["page2_operation_queue"] = []
    st.session_state["page2_active_operation"] = None
    st.session_state["page2_operation_scope_id"] = uuid.uuid4().hex
    st.session_state["page2_operation_notice"] = ""
    st.session_state["page2_operation_error"] = ""
    st.session_state["page2_operation_outputs"] = {}
    _close_chat_edit()
    st.session_state.pop("page2_chat_input", None)
    return True


def _load_record_from_nav_if_needed():
    record_id = str(get_arg("record_id", "") or "").strip()
    if not record_id:
        return

    if st.session_state.page2_loaded_record_id == record_id:
        return

    if page2_operation_queue_busy():
        st.warning("队列执行中，当前记录已锁定；队列完成后才能切换记录。")
        return

    record = chat_records.load_record_by_id(record_id, scope=_chat_scope())
    if record is None:
        st.warning("记录不存在或加载失败。")
        return
    # 隐藏账号创建的聊天记录，对普通账号不可见（直接通过链接打开也拦截）。
    if bool(getattr(record, "hidden", False)) and not bool(
        st.session_state.get("hidden_unlocked", False)
    ):
        st.warning("记录不存在或加载失败。")
        return

    st.session_state.page2_record_id = record.id
    st.session_state.page2_loaded_record_id = record.id
    st.session_state["page2_operation_scope_id"] = "record:" + str(record.id)
    st.session_state.page2_turns = list(record.turns or [])
    st.session_state.page2_generated_media = list(record.generated_images or [])
    st.session_state.page2_image_prompt = ""
    st.session_state.page2_image_prompt_source = {}
    st.session_state.page2_video_prompt = ""
    st.session_state.page2_video_story_toast = None
    st.session_state.pop("page2_video_story_dialog", None)
    st.session_state.pop("page2_pending_video_source", None)
    st.session_state.page2_story_brain_archive = record.story_brain
    st.session_state["page2_story_brain_checkpoints"] = deepcopy(record.story_brain_checkpoints)
    st.session_state.page2_story_brain_short = str(getattr(record, "story_brain_short", "") or "").strip()
    final_setting_id = str(getattr(record, "final_setting_id", "") or "")
    if not final_setting_id:
        prompt_state = system_prompts.load_state(
            hidden_space=bool(st.session_state.get("hidden_unlocked", False))
        )
        prompt_matches = [
            item
            for item in system_prompts.visible_records(prompt_state)
            if str(item.prompt or "").strip() == str(record.system_prompt or "").strip()
        ]
        if len(prompt_matches) == 1:
            final_setting_id = prompt_matches[0].id
        else:
            final_setting_id = prompt_state.selected_record_id
    st.session_state.page2_final_setting_id = final_setting_id
    st.session_state["page2_story_brain_next_update_turn"] = getattr(
        record,
        "story_brain_next_update_turn",
        None,
    )
    st.session_state["page2_story_brain_last_update_turn_id"] = str(
        getattr(record, "story_brain_last_update_turn_id", "") or ""
    )
    _refresh_short_story_brain_editor()
    latest_media_id = _latest_image_media_id(st.session_state.page2_generated_media)
    st.session_state.page2_selected_media_id = latest_media_id
    st.session_state.page2_pending_media_record_select = latest_media_id
    url_favorites.clear_preview_url("page2")
    st.session_state["page2_story_brain_retry_pending"] = False
    st.session_state["page2_operation_notice"] = ""
    st.session_state["page2_operation_error"] = ""
    st.session_state["page2_operation_outputs"] = {}
    _close_chat_edit()

    if str(record.system_prompt or "").strip():
        ctx = page2_service.load_context_from_settings()
        ctx.system_prompt = record.system_prompt
        page2_service.save_context_to_settings(ctx)


def _latest_assistant_message(turns: list[Page2ConversationTurn]) -> str:
    for turn in reversed(turns):
        if bool(getattr(turn, "is_context_excluded", False)):
            continue
        if turn.assistant_message:
            return turn.assistant_message
    return ""


def _conversation_started(turns: list[Page2ConversationTurn]) -> bool:
    """用户是否已在本对话中真正发言。"""
    return any(str(turn.user_message or "").strip() for turn in turns)


def _assistant_message_for_display(text: str) -> str:
    """Hide the model's dice-decision marker without changing saved context."""
    display_text = str(text or "")
    for prefix in _HIDDEN_ASSISTANT_MESSAGE_PREFIXES:
        if display_text.startswith(prefix):
            return display_text[len(prefix) :].lstrip(" \t\r\n，,。:：；;")
    return display_text


def _apply_chat_edit(edit: dict) -> bool:
    """更新对应气泡文本；清空保存 = 删除该条消息。"""
    if _story_brain_update_busy():
        return False

    turn_id = str(edit.get("turn_id", "") or "")
    role = str(edit.get("role", "") or "")
    text = str(edit.get("text", "") or "")
    if not turn_id or role not in ("user", "assistant"):
        return False

    turns = list(st.session_state.get("page2_turns") or [])
    target = next((turn for turn in turns if turn.id == turn_id), None)
    if target is None:
        return False

    if role == "user":
        if str(target.user_message or "") == text:
            return False
        target.user_message = text
    else:
        if str(target.assistant_message or "") == text:
            return False
        target.assistant_message = text or None

    # user / assistant 都被清空的 turn 从记录中移除
    st.session_state["page2_turns"] = [
        turn
        for turn in turns
        if str(turn.user_message or "").strip() or str(turn.assistant_message or "").strip()
    ]
    _upsert_record()
    return True


def _render_editable_chat_message(
    *,
    turn: Page2ConversationTurn,
    role: str,
    text: str,
    story_brain_update_busy: bool,
) -> None:
    """Render one message with a native, server-backed editor."""
    target = _chat_edit_target()
    is_editing = (
        str(target.get("turn_id", "") or "") == str(turn.id)
        and str(target.get("role", "") or "") == role
    )

    with st.chat_message(role):
        if is_editing:
            if story_brain_update_busy:
                st.caption("Story Brain 更新中，暂时不能保存或删除消息。")

            editor_key = _chat_edit_widget_key(turn.id, role)
            if editor_key not in st.session_state:
                st.session_state[editor_key] = str(text or "")
            next_text = st.text_area(
                "编辑用户消息" if role == "user" else "编辑 Assistant 消息",
                key=editor_key,
                height=180,
                disabled=story_brain_update_busy,
                label_visibility="collapsed",
            )
            save_col, delete_col, cancel_col = st.columns([1, 1, 1])
            save_clicked = save_col.button(
                "保存",
                key=f"page2_chat_edit_save_{role}_{turn.id}",
                type="primary",
                use_container_width=True,
                disabled=story_brain_update_busy,
            )
            delete_clicked = delete_col.button(
                "删除",
                key=f"page2_chat_edit_delete_{role}_{turn.id}",
                use_container_width=True,
                disabled=story_brain_update_busy,
            )
            cancel_clicked = cancel_col.button(
                "取消",
                key=f"page2_chat_edit_cancel_{role}_{turn.id}",
                use_container_width=True,
            )

            if save_clicked:
                _apply_chat_edit(
                    {"turn_id": turn.id, "role": role, "text": str(next_text)}
                )
                _close_chat_edit()
                st.rerun()
            if delete_clicked:
                request_delete(
                    f"page2_chat_message_{role}_{turn.id}",
                    str(turn.id),
                    "这条用户消息" if role == "user" else "这条 Assistant 消息",
                )

            def _delete_message(_turn_id: str) -> None:
                _apply_chat_edit({"turn_id": _turn_id, "role": role, "text": ""})
                _close_chat_edit()

            render_delete_confirmation(
                f"page2_chat_message_{role}_{turn.id}",
                _delete_message,
            )
            if cancel_clicked:
                _close_chat_edit()
                st.rerun()
            return

        content_col, edit_col = st.columns([12, 1], vertical_alignment="top")
        with content_col:
            display_text = (
                _assistant_message_for_display(text) if role == "assistant" else text
            )
            st.markdown(display_text or "")
        with edit_col:
            edit_clicked = st.button(
                "",
                key=f"page2_chat_edit_open_{role}_{turn.id}",
                help="编辑该条消息",
                icon=":material/edit:",
                type="tertiary",
                disabled=story_brain_update_busy,
            )
        if edit_clicked and _open_chat_edit(turn.id, role, text):
            st.rerun()


def _upsert_record():
    ctx = page2_service.load_context_from_settings()
    st.session_state.page2_record_id = page2_service.upsert_chat_record(
        record_id=st.session_state.page2_record_id,
        turns=st.session_state.page2_turns,
        generated_media=st.session_state.page2_generated_media,
        system_prompt=ctx.system_prompt,
        story_brain=st.session_state.get("page2_story_brain_archive", {}),
        story_brain_short=st.session_state.get("page2_story_brain_short", ""),
        story_brain_next_update_turn=st.session_state.get(
            "page2_story_brain_next_update_turn"
        ),
        story_brain_last_update_turn_id=str(
            st.session_state.get("page2_story_brain_last_update_turn_id", "")
            or ""
        ),
        final_setting_id=str(
            st.session_state.get("page2_final_setting_id", "") or ""
        ),
        story_brain_checkpoints=st.session_state.get("page2_story_brain_checkpoints", []),
        scope=_chat_scope(),
        hidden=bool(st.session_state.get("hidden_unlocked", False)),
    )


def _refresh_short_story_brain_editor():
    st.session_state.page2_story_brain_short_editor_nonce = int(
        st.session_state.get("page2_story_brain_short_editor_nonce", 0) or 0
    ) + 1


def _current_short_story_brain() -> str:
    story_brain = str(st.session_state.get("page2_story_brain_short", "") or "").strip()
    if story_brain in page2_service.INVALID_STORY_BRAIN_TEXTS:
        story_brain = ""
    st.session_state.page2_story_brain_short = story_brain
    return story_brain


def _roll_user_dice_message() -> str:
    return f"掷骰结果：“{page2_service.roll_point()}”"


def _memory_state() -> dict:
    return {
        "short": str(st.session_state.get("page2_story_brain_short", "") or ""),
        "next_update_turn": st.session_state.get("page2_story_brain_next_update_turn"),
        "last_update_turn_id": str(st.session_state.get("page2_story_brain_last_update_turn_id", "") or ""),
        "retry_pending": bool(st.session_state.get("page2_story_brain_retry_pending", False)),
    }


def _set_memory_state(state: dict, checkpoints: list) -> None:
    for name, value in state.items():
        st.session_state[f"page2_story_brain_{name}"] = deepcopy(value)
    st.session_state["page2_story_brain_checkpoints"] = deepcopy(checkpoints[-2:])


def _memory_rewind_plan(remaining_turns: list) -> tuple[dict, list, str]:
    return story_brain_history.rewind(
        _memory_state(), st.session_state.get("page2_story_brain_checkpoints", []),
        list(st.session_state.page2_turns), remaining_turns,
    )


def _undo_last_turn() -> bool:
    if page2_operation_queue_busy() or _chat_edit_active():
        st.session_state["page2_operation_error"] = "后台任务执行或消息编辑中，请完成后再撤回。"
        return False
    turns = list(st.session_state.page2_turns)
    if not turns:
        return False
    memory, checkpoints, notice = _memory_rewind_plan(turns[:-1])
    before_memory, before_checkpoints = _memory_state(), deepcopy(st.session_state.get("page2_story_brain_checkpoints", []))
    st.session_state.page2_turns = turns[:-1]
    _set_memory_state(memory, checkpoints)
    try:
        _upsert_record()
    except Exception:
        st.session_state.page2_turns = turns
        _set_memory_state(before_memory, before_checkpoints)
        raise
    _refresh_short_story_brain_editor()
    st.session_state["page2_story_brain_short_update_notice"] = notice
    st.session_state["page2_operation_notice"] = "已撤回上一轮问答。" + notice
    return True


def _save_short_story_brain_to_current_record(story_brain: str, *, checkpoint: bool = True):
    before = _memory_state()
    story_brain_text = str(story_brain or "").strip()
    if story_brain_text in page2_service.INVALID_STORY_BRAIN_TEXTS:
        story_brain_text = ""
    st.session_state.page2_story_brain_short = story_brain_text
    if checkpoint and before["short"] != story_brain_text:
        turns = list(st.session_state.page2_turns)
        st.session_state["page2_story_brain_checkpoints"] = story_brain_history.record_update(
            st.session_state.get("page2_story_brain_checkpoints", []), before, _memory_state(),
            turns, str(turns[-1].id) if turns else "",
        )
    _upsert_record()


def _render_short_story_brain_text_editor(value: str, *, disabled: bool = False) -> str:
    result = _story_brain_text_editor_component(
        value=str(value or ""),
        disabled=disabled,
        key=(
            "page2_story_brain_text_editor_component_"
            + str(int(st.session_state.get("page2_story_brain_short_editor_nonce", 0) or 0))
        ),
        default=None,
    )
    if not disabled and isinstance(result, str):
        return result
    return str(value or "")


def _completed_turn_count(turns: list[Page2ConversationTurn]) -> int:
    """统计真实完成的用户问答轮次。

    只有用户消息和助手回复都非空才计数，因此自动开场白不会提前触发 Story Brain 更新。
    """
    return sum(
        1
        for turn in turns
        if not bool(getattr(turn, "is_context_excluded", False))
        if str(turn.user_message or "").strip()
        and str(turn.assistant_message or "").strip()
    )


def _completed_story_brain_turns(
    turns: list[Page2ConversationTurn],
) -> list[Page2ConversationTurn]:
    return [
        turn
        for turn in turns
        if not bool(getattr(turn, "is_context_excluded", False))
        if str(turn.user_message or "").strip()
        and str(turn.assistant_message or "").strip()
    ]


def _story_brain_turns_since_last_update(
    turns: list[Page2ConversationTurn],
    last_update_turn_id: str,
    legacy_fallback_turns: int,
) -> list[Page2ConversationTurn]:
    """返回上次成功更新后的完整轮次；旧记录回退到设置的最近轮数。"""
    completed_turns = _completed_story_brain_turns(turns)
    marker = str(last_update_turn_id or "").strip()
    if marker:
        for index, turn in enumerate(completed_turns):
            if str(turn.id or "") == marker:
                return completed_turns[index + 1 :]

    fallback_count = max(1, int(legacy_fallback_turns))
    return completed_turns[-fallback_count:]


def _should_update_story_brain(
    turns: list[Page2ConversationTurn],
    update_interval: int,
    retry_pending: bool = False,
    next_update_turn: int | None = None,
) -> bool:
    completed_turn_count = _completed_turn_count(turns)
    interval = max(1, int(update_interval))
    if completed_turn_count <= 0:
        return False
    if retry_pending:
        return True
    if next_update_turn is not None:
        return completed_turn_count >= max(1, int(next_update_turn))
    return completed_turn_count % interval == 0


def _manual_story_brain_update_context(
    turns: list[Page2ConversationTurn],
) -> tuple[str, str, str]:
    """为手动更新选取最近一个完整问答，并复用自动更新的上下文形式。"""
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if bool(getattr(turn, "is_context_excluded", False)):
            continue
        user_message = str(turn.user_message or "").strip()
        assistant_message = str(turn.assistant_message or "").strip()
        if not user_message or not assistant_message:
            continue

        previous_assistant_message = _latest_assistant_message(turns[:index])
        memory_source_text = "\n\n".join(
            part
            for part in [previous_assistant_message.strip(), user_message]
            if part
        )
        return memory_source_text, assistant_message, str(turn.id or "")

    return "", "", ""






def _render_system_prompt_dialog(ctx, prompt_state, *, is_guest: bool) -> None:
    @st.dialog("System Prompt", width="large")
    def _dialog():
        edited_prompt = st.text_area(
            "System Prompt",
            height=520,
            disabled=is_guest,
            key="page2_system_prompt_dialog_body",
            label_visibility="collapsed",
        )

        if is_guest:
            st.caption("游客仅可查看当前 System Prompt。")
            if st.button("关闭", use_container_width=True):
                st.rerun()
            return

        save_col, cancel_col = st.columns(2)
        with save_col:
            if st.button("保存", type="primary", use_container_width=True):
                ctx.system_prompt = edited_prompt
                synced_record = system_prompts.update_selected_prompt(
                    prompt_state,
                    ctx.system_prompt,
                )
                if synced_record is not None:
                    st.session_state[
                        f"system_prompt_body_{synced_record.id}"
                    ] = synced_record.prompt
                has_current_record = bool(
                    str(st.session_state.get("page2_record_id", "") or "")
                )
                if has_current_record or _conversation_started(st.session_state.page2_turns):
                    _upsert_record()
                st.session_state["page2_system_prompt_notice"] = "System Prompt 已保存。"
                st.rerun()
        with cancel_col:
            if st.button("取消", use_container_width=True):
                st.rerun()

    _dialog()


def render_sidebar_context():
    _ensure_state()
    _load_record_from_nav_if_needed()

    with st.sidebar:
        if st.button(
            "新建对话",
            use_container_width=True,
            disabled=_story_brain_update_busy(),
        ):
            start_new_conversation()
            goto("main", push_history=False)

        if page2_operation_queue_busy():
            active = st.session_state.get("page2_active_operation")
            waiting = len(list(st.session_state.get("page2_operation_queue") or []))
            if active is not None:
                st.info(f"正在执行：{active.label}\n\n等待中：{waiting} 项")
            else:
                st.info(f"队列等待启动：{waiting} 项")

        record_id = str(st.session_state.get("page2_record_id", "") or "")
        if record_id:
            st.caption("记录 ID: " + record_id)

        st.divider()
        st.subheader("上下文设置")
        ctx = page2_service.load_context_from_settings()

        # 提供从 Prompt 页面选择最终设定的入口
        prompt_state = system_prompts.load_state(
            hidden_space=bool(st.session_state.get("hidden_unlocked", False))
        )
        prompt_records = system_prompts.visible_records(prompt_state)
        prompt_labels = []
        prompt_by_label = {}
        selected_prompt_id = str(
            st.session_state.get("page2_final_setting_id", "")
            or settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "")
            or ""
        )
        selected_prompt_index = None
        for index, record in enumerate(prompt_records):
            label = system_prompts.record_label(
                prompt_state,
                record,
                index,
                unnamed="未命名最终设定",
            )
            prompt_labels.append(label)
            prompt_by_label[label] = record
            if record.id == selected_prompt_id:
                selected_prompt_index = index

        if prompt_labels:
            if st.session_state.get("page2_prompt_record_select") not in prompt_labels:
                st.session_state.pop("page2_prompt_record_select", None)
            chosen_prompt_label = st.selectbox(
                "选择最终设定",
                options=prompt_labels,
                index=selected_prompt_index,
                placeholder="选择最终设定",
                key="page2_prompt_record_select",
            )
            chosen_prompt = prompt_by_label.get(chosen_prompt_label)
            if chosen_prompt is not None and chosen_prompt.id != selected_prompt_id:
                ctx.system_prompt = chosen_prompt.prompt
                st.session_state.page2_final_setting_id = chosen_prompt.id
                settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, chosen_prompt.id)
                settings.set(AppStorageKeys.SYSTEM_PROMPT, chosen_prompt.prompt)
                page2_service.save_context_to_settings(ctx)
        else:
            st.caption("暂无最终设定。")

        with st.container(border=True):
            is_guest = _chat_scope() == "guest"
            system_prompt = ctx.system_prompt
            if st.button(
                "查看 System Prompt" if is_guest else "查看 / 编辑 System Prompt",
                use_container_width=True,
                key="page2_system_prompt_dialog_btn",
            ):
                st.session_state["page2_system_prompt_dialog_body"] = ctx.system_prompt
                _render_system_prompt_dialog(ctx, prompt_state, is_guest=is_guest)
            prompt_notice = st.session_state.pop("page2_system_prompt_notice", "")
            if prompt_notice:
                st.success(prompt_notice)
            context_turn_count = st.number_input(
                "上下文轮数",
                min_value=0,
                max_value=50,
                value=int(ctx.context_turn_count),
                key="page2_sidebar_context_turn_count",
            )
            chat_model_options = list(CHAT_MODEL_OPTIONS)
            if st.session_state.get("page2_sidebar_selected_chat_model") not in chat_model_options:
                st.session_state.pop("page2_sidebar_selected_chat_model", None)
            selected_chat_model = st.selectbox(
                "聊天模型",
                options=chat_model_options,
                index=chat_model_options.index(ctx.selected_chat_model)
                if ctx.selected_chat_model in chat_model_options
                else 0,
                format_func=lambda item: CHAT_MODEL_LABELS.get(item, item),
                key="page2_sidebar_selected_chat_model",
            )
            chat_reasoning = st.selectbox(
                "聊天模型思考",
                options=["开思考", "关思考"],
                index=0 if ctx.chat_reasoning_enabled else 1,
                key="page2_sidebar_chat_reasoning",
                label_visibility="collapsed",
            )
            story_brain_update_models = list(page2_service.STORY_BRAIN_UPDATE_MODELS)
            if (
                st.session_state.get("page2_sidebar_story_brain_update_model")
                not in story_brain_update_models
            ):
                st.session_state.pop("page2_sidebar_story_brain_update_model", None)
            story_brain_update_model = st.selectbox(
                "Story Brain 更新模型",
                options=story_brain_update_models,
                index=story_brain_update_models.index(ctx.story_brain_update_model)
                if ctx.story_brain_update_model in story_brain_update_models
                else 0,
                format_func=lambda item: page2_service.STORY_BRAIN_UPDATE_MODEL_LABELS.get(
                    item,
                    item,
                ),
                key="page2_sidebar_story_brain_update_model",
            )
            story_brain_reasoning = st.selectbox(
                "Story Brain 思考",
                options=["开思考", "关思考"],
                index=0 if ctx.story_brain_reasoning_enabled else 1,
                key="page2_sidebar_story_brain_reasoning",
                label_visibility="collapsed",
            )
            story_brain_turns = st.number_input(
                "Story Brain 更新间隔",
                min_value=1,
                step=1,
                value=int(ctx.story_brain_turns),
                key="page2_sidebar_story_brain_turns",
            )
            manual_memory_source, manual_reply, manual_turn_id = (
                _manual_story_brain_update_context(
                    list(st.session_state.get("page2_turns") or [])
                )
            )
            pending_story_brain_turns = _story_brain_turns_since_last_update(
                list(st.session_state.get("page2_turns") or []),
                str(
                    st.session_state.get(
                        "page2_story_brain_last_update_turn_id",
                        "",
                    )
                    or ""
                ),
                int(story_brain_turns),
            )
            waiting_for_chat = _has_earlier_operation("chat")
            manual_update_disabled = (
                _chat_edit_active()
                or _operation_kind_busy("story_brain")
                or not bool(st.session_state.get("page2_story_brain_enabled", True))
                or not bool(pending_story_brain_turns or waiting_for_chat)
            )
            manual_update_help = (
                "暂无自上次成功更新以来的新完整问答。"
                if not pending_story_brain_turns and not waiting_for_chat
                else "使用上次成功更新后的全部新问答更新 Story Brain；成功后下次更新顺延 5 轮。"
            )
            if st.button(
                "更新storybrain",
                key="page2_sidebar_manual_story_brain_update_btn",
                use_container_width=True,
                disabled=manual_update_disabled,
                help=manual_update_help,
            ):
                ctx.story_brain_update_model = story_brain_update_model
                ctx.story_brain_reasoning_enabled = story_brain_reasoning == "开思考"
                ctx.story_brain_turns = int(story_brain_turns)
                page2_service.save_context_to_settings(ctx)
                st.session_state["page2_story_brain_short_update_notice"] = ""
                _schedule_story_brain_update(
                    ctx=ctx,
                    turns=list(st.session_state.page2_turns),
                    memory_source_text="" if waiting_for_chat else manual_memory_source,
                    reply="" if waiting_for_chat else manual_reply,
                    turn_id="" if waiting_for_chat else manual_turn_id,
                    manual=True,
                )
                st.rerun()
            if not pending_story_brain_turns and not waiting_for_chat:
                st.caption("暂无自上次成功更新以来的新完整问答。")
            temperature = st.slider(
                "temperature",
                min_value=0.0,
                max_value=2.0,
                value=float(ctx.temperature),
                step=0.05,
                key="page2_sidebar_temperature",
            )

        st.divider()
        unexpected_event_enabled = st.toggle(
            "意外情况",
            value=bool(ctx.unexpected_event_enabled),
            key="page2_sidebar_unexpected_event_enabled",
        )
        unexpected_event_threshold = st.slider(
            "意外发生点数",
            min_value=0,
            max_value=100,
            value=int(ctx.unexpected_event_threshold),
            step=1,
            key="page2_sidebar_unexpected_event_threshold",
        )

        if st.button(
            "确认",
            type="primary",
            use_container_width=True,
            key="page2_sidebar_confirm_btn",
            disabled=_story_brain_update_busy(),
        ):
            if _chat_scope() != "guest":
                ctx.system_prompt = system_prompt
            ctx.context_turn_count = int(context_turn_count)
            ctx.selected_chat_model = selected_chat_model
            ctx.chat_reasoning_enabled = chat_reasoning == "开思考"
            ctx.story_brain_update_model = story_brain_update_model
            ctx.story_brain_reasoning_enabled = story_brain_reasoning == "开思考"
            ctx.story_brain_turns = int(story_brain_turns)
            ctx.unexpected_event_enabled = bool(unexpected_event_enabled)
            ctx.unexpected_event_threshold = int(unexpected_event_threshold)
            ctx.temperature = float(temperature)
            page2_service.save_context_to_settings(ctx)
            if _chat_scope() != "guest":
                synced_record = system_prompts.update_selected_prompt(
                    prompt_state,
                    ctx.system_prompt,
                )
                if synced_record is not None:
                    st.session_state[
                        f"system_prompt_body_{synced_record.id}"
                    ] = synced_record.prompt
            if str(st.session_state.get("page2_record_id", "") or "") or _conversation_started(
                st.session_state.page2_turns
            ):
                _upsert_record()
            st.success("设置已保存")
            st.rerun()

        if str(st.session_state.get("page2_record_id", "") or "") or _conversation_started(
            st.session_state.page2_turns
        ):
            _upsert_record()


def _apply_short_sb_result(result: str):
    """立即应用并持久化 SHORT 模式结果。"""
    _save_short_story_brain_to_current_record(result, checkpoint=False)
    _refresh_short_story_brain_editor()


def _schedule_story_brain_update(
    *,
    ctx,
    turns: list[Page2ConversationTurn],
    memory_source_text: str,
    reply: str,
    turn_id: str,
    first: bool = False,
    manual: bool = False,
) -> None:
    """把 Story Brain 放入统一队列；自动更新可以插到队首作为消息的后续步骤。"""
    _enqueue_operation(
        "story_brain",
        "更新 Story Brain",
        {
            "ctx": ctx,
            "memory_source_text": str(memory_source_text or ""),
            "reply": str(reply or ""),
            "turn_id": str(turn_id or ""),
            "manual": bool(manual),
        },
        first=first,
    )


def _apply_story_brain_update_task(task: _Page2OperationTask) -> None:
    """主线程应用已完成的 Story Brain 结果。"""
    turn_id = str(task.meta.get("turn_id", "") or "")
    if task.error is not None:
        _record_story_brain_update_error(
            exc=task.error,
            turn_id=turn_id,
        )
        return

    updated_story_brain = str(task.result or "").strip()
    before_memory = _memory_state()
    completed_turn_count = int(task.meta.get("completed_turn_count", 0))
    next_update_delay = max(1, int(task.meta.get("next_update_delay", 1)))
    next_update_turn = completed_turn_count + next_update_delay
    st.session_state["page2_story_brain_next_update_turn"] = next_update_turn
    last_processed_turn_id = str(
        task.meta.get("last_processed_turn_id") or turn_id or ""
    )
    if last_processed_turn_id:
        st.session_state[
            "page2_story_brain_last_update_turn_id"
        ] = last_processed_turn_id
    st.session_state["page2_story_brain_short"] = updated_story_brain
    st.session_state["page2_story_brain_retry_pending"] = False
    st.session_state["page2_story_brain_checkpoints"] = story_brain_history.record_update(
        st.session_state.get("page2_story_brain_checkpoints", []), before_memory, _memory_state(),
        list(st.session_state.page2_turns), last_processed_turn_id,
    )
    _apply_short_sb_result(updated_story_brain)
    if updated_story_brain == str(task.meta.get("previous_short_story_brain", "") or ""):
        notice = (
            f"Story Brain 第 {completed_turn_count} 轮检查完成，本轮内容无变化。"
        )
    else:
        notice = f"Story Brain 已在第 {completed_turn_count} 轮更新并保存。"
    processed_turn_count = int(task.meta.get("processed_turn_count", 0))
    if processed_turn_count > 0:
        notice += f"本次读取了 {processed_turn_count} 轮新问答。"
    notice += f"下次将在第 {next_update_turn} 轮更新。"
    st.session_state["page2_story_brain_short_update_notice"] = notice
    st.session_state["page2_story_brain_retry_pending"] = False


def _record_story_brain_update_error(
    *,
    exc: Exception,
    turn_id: str,
) -> str:
    """记录失败终态，保留旧 Story Brain，让当前串行操作正常结束。"""
    error_message = (
        "Story Brain 更新失败："
        + user_facing_error_message(exc)
        + "；将在下一轮回复成功后重试。"
    )
    st.session_state["page2_story_brain_retry_pending"] = True
    st.session_state["page2_story_brain_short_update_notice"] = error_message
    return error_message


def _is_resend_command(text: str) -> bool:
    return str(text or "").strip() in {"/重发", "／重发"}


def _queue_resend(*, ctx, story_brain_enabled: bool) -> bool:
    if page2_operation_queue_busy() or _chat_edit_active():
        st.session_state["page2_operation_error"] = "后台任务执行或消息编辑中，请完成后再重发。"
        return False
    turns = list(st.session_state.page2_turns)
    if not turns or not str(turns[-1].user_message or "").strip():
        st.session_state["page2_operation_error"] = "没有可重发的上一轮用户消息。"
        return False
    if turns[-1].is_loading:
        st.session_state["page2_operation_error"] = "上一轮尚未结束，请等待完成后再重发。"
        return False
    _enqueue_operation("resend", "重发上一轮（成功后替换）", {
        "ctx": ctx, "story_brain_enabled": story_brain_enabled, "target_turn_id": turns[-1].id,
    })
    return True


def _prepare_resend_operation(task: _Page2OperationTask):
    turns = deepcopy(list(st.session_state.page2_turns))
    if not turns or turns[-1].id != task.payload.get("target_turn_id"):
        raise ValueError("上一轮消息已变化，未执行重发。")
    original = turns[-1]
    if original.is_loading or not str(original.user_message or "").strip():
        raise ValueError("上一轮没有可重发的用户消息，或尚未完成。")
    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    remaining = turns[:-1]
    memory, checkpoints, notice = _memory_rewind_plan(remaining)
    replacement = Page2ConversationTurn(
        user_message=original.user_message,
        is_user_message_hidden=original.is_user_message_hidden,
    )
    task.meta.update({
        "ctx": ctx, "turn_id": replacement.id, "replacement": replacement,
        "original_turns": [turn.to_dict() for turn in turns], "remaining_turns": remaining,
        "rewound_memory": memory, "rewound_checkpoints": checkpoints, "memory_notice": notice,
        "story_brain_enabled": bool(task.payload.get("story_brain_enabled", True)),
        "memory_source_text": "\n\n".join(filter(None, [
            _latest_assistant_message(remaining).strip(), original.user_message,
        ])),
    })
    if notice:
        st.session_state["page2_operation_notice"] = notice
    # Staging only: no live turn, memory or persisted record changes until success.
    return lambda: page2_service.send_message(
        ctx=ctx, turns=remaining, user_message=original.user_message,
        story_brain_short=memory["short"], story_brain_enabled=task.meta["story_brain_enabled"],
    )


def _apply_resend_operation(task: _Page2OperationTask) -> None:
    if task.error is not None or not str(task.result or "").strip():
        reason = user_facing_error_message(task.error) if task.error else "模型返回了空回复。"
        raise RuntimeError("重发失败，原问答和记忆已保留。" + reason)
    if task.scope_id != _operation_scope_id() or [
        turn.to_dict() for turn in st.session_state.page2_turns
    ] != task.meta.get("original_turns"):
        raise RuntimeError("聊天记录已变化，未替换原问答。")
    before_turns = list(st.session_state.page2_turns)
    before_memory = _memory_state()
    before_checkpoints = deepcopy(st.session_state.get("page2_story_brain_checkpoints", []))
    replacement = task.meta["replacement"]
    replacement.assistant_message = str(task.result)
    st.session_state.page2_turns = task.meta["remaining_turns"] + [replacement]
    _set_memory_state(task.meta["rewound_memory"], task.meta["rewound_checkpoints"])
    try:
        _upsert_record()
    except Exception:
        st.session_state.page2_turns = before_turns
        _set_memory_state(before_memory, before_checkpoints)
        raise
    _refresh_short_story_brain_editor()
    st.session_state["page2_story_brain_short_update_notice"] = task.meta.get("memory_notice", "")
    _schedule_chat_followups(task, replacement.assistant_message)


def _prepare_chat_operation(task: _Page2OperationTask):
    current_text = str(task.payload.get("user_text", "") or "").strip()
    if not current_text:
        raise ValueError("发送消息为空。")

    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    story_brain_enabled = bool(task.payload.get("story_brain_enabled", True))
    turns = list(st.session_state.page2_turns)
    if current_text == "开始" and not turns:
        prompt_state = system_prompts.load_state(
            hidden_space=bool(st.session_state.get("hidden_unlocked", False))
        )
        first_input = system_prompts.first_input_for_record(
            prompt_state,
            str(st.session_state.get("page2_final_setting_id", "") or ""),
        )
        if first_input:
            current_text = first_input
    previous_assistant_text = _latest_assistant_message(turns)
    memory_source_text = "\n\n".join(
        part for part in [previous_assistant_text.strip(), current_text] if part
    )
    story_brain_short = _current_short_story_brain()

    new_turn = Page2ConversationTurn(
        user_message=current_text,
        assistant_message=None,
        is_loading=True,
        is_user_message_hidden=bool(task.payload.get("hide_user_message", False)),
    )
    st.session_state.page2_turns = turns + [new_turn]
    _upsert_record()
    task.meta.update(
        {
            "ctx": ctx,
            "turn_id": new_turn.id,
            "memory_source_text": memory_source_text,
            "story_brain_enabled": story_brain_enabled,
            "is_context_excluded": False,
            "is_auto_dice_followup": bool(
                task.payload.get("is_auto_dice_followup", False)
            ),
        }
    )
    return lambda: page2_service.send_message(
        ctx=ctx,
        turns=turns,
        user_message=new_turn.user_message,
        story_brain_short=story_brain_short,
        story_brain_enabled=story_brain_enabled,
    )


def _prepare_story_brain_operation(task: _Page2OperationTask):
    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    turns_snapshot = list(st.session_state.page2_turns)
    memory_source_text = str(task.payload.get("memory_source_text", "") or "")
    reply = str(task.payload.get("reply", "") or "")
    turn_id = str(task.payload.get("turn_id", "") or "")
    if not turn_id or not reply:
        memory_source_text, reply, turn_id = _manual_story_brain_update_context(
            turns_snapshot
        )
    if not turn_id:
        raise ValueError("暂无可用于更新 Story Brain 的完整问答。")

    completed_turn_count = _completed_turn_count(turns_snapshot)
    update_turns = _story_brain_turns_since_last_update(
        turns_snapshot,
        str(
            st.session_state.get(
                "page2_story_brain_last_update_turn_id",
                "",
            )
            or ""
        ),
        int(ctx.story_brain_turns),
    )
    if not update_turns:
        raise ValueError("暂无自上次成功更新以来的新完整问答。")
    task.meta.update(
        {
            "turn_id": turn_id,
            "completed_turn_count": completed_turn_count,
            "processed_turn_count": len(update_turns),
            "last_processed_turn_id": str(update_turns[-1].id or ""),
            "next_update_delay": (
                _MANUAL_STORY_BRAIN_UPDATE_DELAY
                if bool(task.payload.get("manual", False))
                else max(1, int(ctx.story_brain_turns))
            ),
        }
    )
    previous_story_brain = _current_short_story_brain()
    task.meta["previous_short_story_brain"] = previous_story_brain
    prompt_state = system_prompts.load_state(
        hidden_space=bool(st.session_state.get("hidden_unlocked", False))
    )
    long_term_memory_prompt = system_prompts.long_term_memory_for_record(
        prompt_state,
        str(st.session_state.get("page2_final_setting_id", "") or ""),
    )
    return lambda: page2_service.generate_short_story_brain(
        ctx=ctx,
        turns=update_turns,
        story_brain_short=previous_story_brain,
        long_term_memory_prompt=long_term_memory_prompt,
        history_turn_count=len(update_turns),
    )


def _prepare_image_prompt_operation(task: _Page2OperationTask):
    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    task.meta["image_prompt_source"] = latest_image_prompt_source(list(st.session_state.page2_turns))
    conversation_text = page2_service.build_recent_image_prompt_conversation(
        st.session_state.page2_turns,
    )
    mode = str(task.payload.get("mode", "normal") or "normal")
    subject = str(task.payload.get("subject", "") or "")
    return lambda: page2_service.generate_image_prompt(
        conversation_text,
        ctx=ctx,
        mode=mode,
        subject=subject,
    )


def _prepare_image_operation(task: _Page2OperationTask):
    prompt_operation_id = str(task.payload.get("prompt_operation_id", "") or "")
    if prompt_operation_id:
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        if prompt_operation_id not in outputs:
            raise RuntimeError("前置的图片 prompt 任务未成功，无法继续生成图片。")
        prompt = str(outputs.get(prompt_operation_id, "") or "")
        source = dict(outputs.get(prompt_operation_id + ":source") or {})
    else:
        prompt = str(task.payload.get("prompt", "") or "")
        source = dict(task.payload.get("image_prompt_source") or {})
    task.meta["image_prompt_source"] = source
    provider = str(task.payload.get("provider", "") or "")
    image_urls = list(task.payload.get("image_urls") or [])
    aspect_ratio = task.payload.get("aspect_ratio", page2_service.IMAGE_ASPECT_RATIO)
    return lambda: page2_service.generate_image(
        provider=provider,
        prompt=prompt,
        image_urls=image_urls,
        aspect_ratio=aspect_ratio,
    )


def _video_operation_source(task: _Page2OperationTask):
    media = list(st.session_state.page2_generated_media)
    source = None
    image_operation_id = str(task.payload.get("image_operation_id", "") or "")
    if image_operation_id:
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        source_id = str(outputs.get(image_operation_id, "") or "")
        if not source_id:
            raise RuntimeError("前置的生图任务未成功，无法继续生成视频。")
        source = next((item for item in media if item.id == source_id), None)
    else:
        source_id = str(task.payload.get("source_id", "") or "")
        source = next((item for item in media if item.id == source_id), None)
    if source is None:
        raise ValueError("队列执行到生成视频时，没有找到可用的输入图片。")
    return source


def _prepare_video_prompt_operation(task: _Page2OperationTask):
    source = _video_operation_source(task)
    task.meta["source_id"] = source.id
    task.meta["previous_video_prompt"] = source.video_prompt
    try:
        story_text = resolve_video_story(source, list(st.session_state.page2_turns))
    except MissingVideoStorySource:
        st.session_state.page2_video_story_toast = source.id
        raise
    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    image_prompt = source.prompt
    return lambda: page2_service.generate_video_prompt(
        ctx=ctx, image_prompt=image_prompt, story_text=story_text,
    )


def _prepare_video_operation(task: _Page2OperationTask):
    source = _video_operation_source(task)

    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    prompt = str(task.payload.get("prompt", "") or "")
    seconds = task.payload.get("seconds", 5)
    return lambda: page2_service.generate_video_from_image(
        ctx=ctx,
        source_record=source,
        prompt=prompt,
        seconds=seconds,
        generate_audio=bool(task.payload.get("generate_audio", True)),
    )


def _prepare_operation(task: _Page2OperationTask):
    if task.scope_id != _operation_scope_id():
        raise RuntimeError("当前记录已变更，为避免写入错误记录，任务已终止。")
    if task.kind == "chat":
        return _prepare_chat_operation(task)
    if task.kind == "resend":
        return _prepare_resend_operation(task)
    if task.kind == "story_brain":
        return _prepare_story_brain_operation(task)
    if task.kind == "image_prompt":
        return _prepare_image_prompt_operation(task)
    if task.kind == "image":
        return _prepare_image_operation(task)
    if task.kind == "video_prompt":
        return _prepare_video_prompt_operation(task)
    if task.kind == "video":
        return _prepare_video_operation(task)
    raise ValueError("未知队列操作：" + task.kind)


def _apply_chat_operation(task: _Page2OperationTask) -> None:
    reply = (
        "请求失败，请稍后重试。\n" + user_facing_error_message(task.error)
        if task.error is not None
        else str(task.result or "")
    )
    turn_id = str(task.meta.get("turn_id", "") or "")
    target_turn = next(
        (turn for turn in st.session_state.page2_turns if str(turn.id) == turn_id),
        None,
    )
    if target_turn is None:
        raise RuntimeError("消息任务完成后未找到待写回的对话记录。")
    target_turn.assistant_message = reply
    target_turn.is_loading = False
    _upsert_record()

    _schedule_chat_followups(task, reply)


def _schedule_chat_followups(task: _Page2OperationTask, reply: str) -> None:
    turn_id = str(task.meta.get("turn_id", "") or "")

    ctx = task.meta.get("ctx") or page2_service.load_context_from_settings()
    story_brain_enabled = bool(task.meta.get("story_brain_enabled", True))
    is_context_excluded = bool(task.meta.get("is_context_excluded", False))
    if story_brain_enabled and task.error is None and not is_context_excluded:
        retry_pending = bool(st.session_state.get("page2_story_brain_retry_pending", False))
        if _should_update_story_brain(
            st.session_state.page2_turns,
            ctx.story_brain_turns,
            retry_pending=retry_pending,
            next_update_turn=st.session_state.get(
                "page2_story_brain_next_update_turn"
            ),
        ):
            _schedule_story_brain_update(
                ctx=ctx,
                turns=st.session_state.page2_turns,
                memory_source_text=str(task.meta.get("memory_source_text", "") or ""),
                reply=reply,
                turn_id=turn_id,
                first=True,
            )
    if (
        task.error is None
        and not is_context_excluded
        and not bool(task.meta.get("is_auto_dice_followup", False))
        and page2_service.should_auto_roll_dice(reply)
    ):
        _enqueue_operation(
            "chat",
            "自动掷骰并发送",
            {
                "ctx": ctx,
                "user_text": _roll_user_dice_message(),
                "hide_user_message": False,
                "story_brain_enabled": story_brain_enabled,
                "is_auto_dice_followup": True,
            },
            first=True,
            allow_same_kind=True,
        )
    st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex


def _apply_operation(task: _Page2OperationTask) -> None:
    if task.kind == "resend":
        _apply_resend_operation(task)
        return
    if task.kind == "chat":
        _apply_chat_operation(task)
        return
    if task.kind == "story_brain":
        _apply_story_brain_update_task(task)
        return
    if task.error is not None:
        raise task.error
    if task.kind == "image_prompt":
        st.session_state.page2_image_prompt = str(task.result or "")
        source = dict(task.meta.get("image_prompt_source") or {})
        st.session_state.page2_image_prompt_source = source
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        outputs[task.id] = st.session_state.page2_image_prompt
        outputs[task.id + ":source"] = source
        st.session_state["page2_operation_outputs"] = outputs
        return
    if task.kind == "video_prompt":
        source = _find_video_source(task.meta.get("source_id", ""))
        if source is None:
            raise ValueError("输入图片已不存在，无法保存视频 prompt。")
        if source.video_prompt != task.meta.get("previous_video_prompt", ""):
            raise ValueError("视频 prompt 已被修改，未覆盖你的编辑，请重新生成。")
        _save_image_video_field(source.id, "video_prompt", str(task.result or ""))
        return
    if task.kind in {"image", "video"}:
        record = task.result
        if task.kind == "image":
            source = dict(task.meta.get("image_prompt_source") or {})
            record.source_turn_id = str(source.get("source_turn_id") or "")
            record.previous_source_turn_id = str(source.get("previous_source_turn_id") or "")
            record.video_prompt = str(task.payload.get("pending_video_prompt") or "")
            st.session_state["page2_pending_video_source"] = record.id
        st.session_state.page2_generated_media = list(
            st.session_state.page2_generated_media
        ) + [record]
        st.session_state.page2_selected_media_id = record.id
        st.session_state.page2_pending_media_record_select = record.id
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        outputs[task.id] = record.id
        st.session_state["page2_operation_outputs"] = outputs
        url_favorites.clear_preview_url("page2")
        _upsert_record()
        return
    raise ValueError("未知队列操作：" + task.kind)


def _dismiss_operation_error() -> None:
    st.session_state["page2_operation_error"] = ""


@st.fragment(run_every=1)
def _render_operation_queue_status() -> None:
    """串行启动队首任务，并在主线程完成结果写回。"""
    error = str(st.session_state.get("page2_operation_error", "") or "")
    if error:
        st.error(error)
        st.button(
            "关闭错误提示",
            key="page2_dismiss_operation_error",
            on_click=_dismiss_operation_error,
        )
    active = st.session_state.get("page2_active_operation")
    queue = list(st.session_state.get("page2_operation_queue") or [])
    if active is None and queue:
        active = queue.pop(0)
        st.session_state["page2_active_operation"] = active
        st.session_state["page2_operation_queue"] = queue

    if active is None:
        notice = str(st.session_state.pop("page2_operation_notice", "") or "")
        if notice and not error:
            st.success(notice)
        return

    if not active.is_started():
        try:
            fn = _prepare_operation(active)
            active.start(fn)
        except Exception as exc:
            active.fail_before_start(exc)
        st.rerun()

    if not active.is_done():
        waiting = len(list(st.session_state.get("page2_operation_queue") or []))
        suffix = f"；后面还有 {waiting} 项等待" if waiting else ""
        st.info(f"正在执行：{active.label}{suffix}")
        if active.kind == "resend" and active.meta.get("memory_notice"):
            st.warning(active.meta["memory_notice"])
        return

    try:
        _apply_operation(active)
        if active.error is None:
            st.session_state["page2_operation_notice"] = f"已完成：{active.label}" + (
                str(active.meta.get("memory_notice", "")) if active.kind == "resend" else ""
            )
        elif active.kind == "chat":
            st.session_state["page2_operation_error"] = (
                f"{active.label}失败，队列将继续："
                + user_facing_error_message(active.error)
            )
    except Exception as exc:
        st.session_state["page2_operation_error"] = (
            f"{active.label}失败，队列将继续："
            + user_facing_error_message(exc)
        )
    finally:
        st.session_state["page2_active_operation"] = None
    st.rerun()


def _render_chat_column():
    story_brain_update_busy = _story_brain_update_busy()
    turns = st.session_state.page2_turns
    edit_target = _chat_edit_target()
    if edit_target:
        target_turn_id = str(edit_target.get("turn_id", "") or "")
        target_role = str(edit_target.get("role", "") or "")
        target_exists = any(
            str(turn.id) == target_turn_id
            and (
                (target_role == "user" and bool(turn.user_message))
                or (target_role == "assistant" and turn.assistant_message is not None)
            )
            for turn in turns
        )
        if not target_exists:
            _close_chat_edit()
    ctx = page2_service.load_context_from_settings()
    chat_edit_active = _chat_edit_active()
    chat_busy = _operation_kind_busy("chat")
    scroll_to_bottom_request = str(
        st.session_state.pop("page2_chat_scroll_to_bottom_request", "") or ""
    )

    with st.container(key="page2_chat_canvas"):
        history = st.container(
            key="page2_chat_history",
            border=False,
        )
        with history:
            for turn in turns:
                if turn.user_message and not bool(
                    getattr(turn, "is_user_message_hidden", False)
                ):
                    _render_editable_chat_message(
                        turn=turn,
                        role="user",
                        text=turn.user_message,
                        story_brain_update_busy=story_brain_update_busy,
                    )
                if turn.assistant_message is not None:
                    _render_editable_chat_message(
                        turn=turn,
                        role="assistant",
                        text=turn.assistant_message or "",
                        story_brain_update_busy=story_brain_update_busy,
                    )
            if scroll_to_bottom_request:
                with st.container(key="page2_chat_scroll_trigger"):
                    components.html(
                        f"""
                        <script>
                          (() => {{
                            const requestId = "{scroll_to_bottom_request}";
                            const scrollToBottom = () => {{
                              try {{
                                const history = window.parent.document.querySelector(
                                  ".st-key-page2_chat_history"
                                );
                                if (history) {{
                                  history.scrollTop = history.scrollHeight;
                                }}
                              }} catch (error) {{}}
                            }};
                            requestAnimationFrame(() =>
                              requestAnimationFrame(scrollToBottom)
                            );
                            setTimeout(scrollToBottom, 100);
                          }})();
                        </script>
                        """,
                        height=0,
                    )

        input_placeholder = (
            "正在等待回复，可以先输入下一条消息"
            if chat_busy
            else "输入消息并回车发送" if turns else "输入“开始”以开始游戏"
        )
        with st.container(key="page2_chat_composer"):
            user_text = st.chat_input(
                input_placeholder,
                key="page2_chat_input",
                disabled=chat_edit_active,
            )
            _render_empty_space_submit_bridge(send_disabled=chat_busy or chat_edit_active)
            dice_clicked = st.button(
                "",
                key="page2_chat_dice_btn",
                help="掷一次 0–24 点骰子并直接发送",
                icon=":material/casino:",
                type="tertiary",
                disabled=chat_edit_active or _operation_kind_busy("chat"),
            )

    if dice_clicked:
        user_text = _roll_user_dice_message()

    undo_clicked = st.button(
        "",
        key="page2_chat_undo_btn",
        help=None,
        icon=":material/undo:",
        type="tertiary",
        disabled=story_brain_update_busy or chat_edit_active or not bool(turns),
        use_container_width=True,
    )

    story_brain_enabled = bool(st.session_state.get("page2_story_brain_enabled", True))
    story_brain_clicked = st.button(
        "点击关闭story brain" if story_brain_enabled else "点击开启story brain",
        key="page2_story_brain_btn",
        use_container_width=True,
        disabled=story_brain_update_busy or chat_edit_active,
    )
    if story_brain_clicked:
        story_brain_enabled = not story_brain_enabled
        st.session_state["page2_story_brain_enabled"] = story_brain_enabled

    if story_brain_enabled:
        st.subheader("Story Brain")
        if story_brain_update_busy:
            st.caption("队列执行中，Story Brain 可查看，暂时不能编辑。")
        else:
            notice = str(st.session_state.pop("page2_story_brain_short_update_notice", "") or "").strip()
            if notice:
                if notice.startswith("Story Brain 更新失败"):
                    st.error(notice)
                else:
                    st.success(notice)
        story_brain_short = _current_short_story_brain()
        next_story_brain_short = _render_short_story_brain_text_editor(
            story_brain_short, disabled=story_brain_update_busy
        )
        if not story_brain_update_busy and next_story_brain_short != story_brain_short:
            _save_short_story_brain_to_current_record(next_story_brain_short)
            _refresh_short_story_brain_editor()
            st.rerun()

    if undo_clicked and turns:
        _undo_last_turn()
        st.rerun()

    if user_text is not None and _is_resend_command(str(user_text)):
        _queue_resend(ctx=ctx, story_brain_enabled=story_brain_enabled)
        st.rerun()

    if user_text is None or chat_busy or chat_edit_active:
        return

    submitted_text = str(user_text)
    hide_user_message = submitted_text == _EMPTY_SPACE_SUBMISSION_SENTINEL
    current_text = (
        _EMPTY_SPACE_CONTINUE_MESSAGE if hide_user_message else submitted_text.strip()
    )
    if not current_text:
        return
    _enqueue_operation(
        "chat",
        "发送消息",
        {
            "ctx": ctx,
            "user_text": current_text,
            "hide_user_message": hide_user_message,
            "story_brain_enabled": story_brain_enabled,
        },
    )


def _render_url_preview_image(url: str) -> None:
    """显示「URL 收藏」里点「显示图片」选中的图片。"""
    try:
        st.image(url)
    except Exception:
        st.error("无法显示该 URL 的图片。")


def _save_image_prompt_field(widget_key: str, state_key: str) -> None:
    st.session_state[state_key] = st.session_state[widget_key]
    if state_key == "page2_image_prompt" and not str(st.session_state[state_key] or "").strip():
        st.session_state.page2_image_prompt_source = {}


def _find_video_source(source_id: str):
    return next((item for item in st.session_state.page2_generated_media
                 if item.id == source_id and item.media_kind == GeneratedMediaKind.IMAGE), None)


def _activate_media_selection() -> None:
    url_favorites.activate_generated_media_selection(
        "page2", "page2_selected_media_id", "page2_media_record_select",
    )
    source_id = st.session_state.get("page2_media_record_select", "")
    if _find_video_source(source_id) is not None:
        st.session_state["page2_pending_video_source"] = source_id


def _save_image_video_field(source_id: str, field: str, text: str) -> None:
    source = _find_video_source(source_id)
    if source is None:
        raise ValueError("输入图片已不存在，请重新选择。")
    previous = getattr(source, field)
    setattr(source, field, text)
    try:
        _upsert_record()
    except Exception:
        setattr(source, field, previous)
        raise


def _save_video_prompt_field(source_id: str, image_task_id: str) -> None:
    text = str(st.session_state.get("_page2_video_prompt") or "")
    if source_id:
        try:
            _save_image_video_field(source_id, "video_prompt", text)
        except Exception as exc:
            st.session_state.page2_operation_error = "视频 prompt 保存失败：" + user_facing_error_message(exc)
    elif image_task_id:
        task = _latest_earlier_operation("image")
        if task is not None and task.id == image_task_id:
            task.payload["pending_video_prompt"] = text


def _close_video_story_dialog() -> None:
    st.session_state.pop("page2_video_story_dialog", None)


@st.dialog("补录图片对应的故事", on_dismiss=_close_video_story_dialog)
def _render_video_story_dialog(source_id: str) -> None:
    source = _find_video_source(source_id)
    if source is None:
        st.error("这张图片已不存在。")
        return
    st.caption("对应图片：" + (source.prompt[:80] or "未保存图片 prompt"))
    text = st.text_area(
        "粘贴对应那一轮的助手故事正文",
        value=source.video_story_text, height=220,
        key=f"page2_video_story_input_{source_id}",
    )
    st.caption("仅保存为这张图片的故事来源，不会添加或修改聊天记录。")
    if st.button("保存故事", key="page2_save_video_story", disabled=page2_operation_queue_busy()):
        if not str(text or "").strip():
            st.error("请先粘贴助手的故事正文。")
            return
        try:
            _save_image_video_field(source_id, "video_story_text", text.strip())
        except Exception as exc:
            _show_error(exc)
            return
        st.session_state.page2_video_story_toast = None
        _close_video_story_dialog()
        st.session_state.page2_operation_error = ""
        st.session_state.page2_operation_notice = "故事已补录，请点击“生视频prompt”。"
        st.rerun()


def _render_video_story_toast() -> None:
    source_id = st.session_state.get("page2_video_story_toast")
    source = _find_video_source(source_id) if source_id else None
    if source is None:
        return
    with st.container(key="page2_video_story_toast"):
        st.markdown("**这张图片缺少来源轮次**")
        st.caption("请补录对应那一轮的助手故事正文。图片：" + source.prompt[:36])
        paste, dismiss = st.columns([3, 1])
        with paste:
            if st.button("粘贴对应故事", key="page2_paste_video_story", disabled=page2_operation_queue_busy()):
                st.session_state["page2_video_story_dialog"] = source.id
        with dismiss:
            if st.button("关闭", key="page2_dismiss_video_story"):
                st.session_state.page2_video_story_toast = None
                st.rerun()


def _render_video_prompt_controls(media: list, video_model: str) -> None:
    render_toast_style()
    images = [item for item in media if item.media_kind == GeneratedMediaKind.IMAGE]
    image_dependency = _latest_earlier_operation("image")
    by_id = {item.id: item for item in images}
    options = list(by_id)
    pending_id = "pending:" + image_dependency.id if image_dependency is not None else ""
    if pending_id:
        options.append(pending_id)
    preferred = st.session_state.pop("page2_pending_video_source", "")
    if preferred in options:
        st.session_state["page2_video_source"] = preferred
    elif st.session_state.get("page2_video_source") not in options:
        selected = st.session_state.get("page2_selected_media_id", "")
        st.session_state["page2_video_source"] = selected if selected in options else (options[-1] if options else None)
    selected_id = st.selectbox(
        "选择输入图片", options=options, key="page2_video_source",
        format_func=lambda value: "待生成的新图片（等待前面的生图任务）" if value == pending_id else (
            f"{by_id[value].provider} • {by_id[value].created_at} • {by_id[value].prompt[:24]}"
        ),
        placeholder="暂无可用图片", disabled=not options,
    )
    source = by_id.get(selected_id)
    use_pending = bool(pending_id and selected_id == pending_id)
    source_id = source.id if source is not None else ""
    image_task_id = image_dependency.id if use_pending else ""
    draft = source.video_prompt if source is not None else (
        str(image_dependency.payload.get("pending_video_prompt") or "") if use_pending else ""
    )
    seconds, generate_audio = _render_video_duration_controls(video_model)
    st.session_state["_page2_video_prompt"] = draft
    prompt = st.text_area(
        "视频 prompt", key="_page2_video_prompt", height=68,
        disabled=not options or _operation_kind_busy("video_prompt"),
        on_change=_save_video_prompt_field, args=(source_id, image_task_id),
    )
    render_editor_sizing()
    ctx = page2_service.load_context_from_settings()
    ctx.selected_video_generation_provider = video_model
    kind = "video" if str(prompt or "").strip() else "video_prompt"
    label = "生视频" if kind == "video" else "生视频prompt"
    if st.button(
        label, key="page2_generate_video", use_container_width=True,
        disabled=not options or _operation_kind_busy("video") or _operation_kind_busy("video_prompt") or _chat_edit_active(),
    ):
        try:
            if kind == "video_prompt" and source is not None:
                resolve_video_story(source, list(st.session_state.page2_turns))
        except MissingVideoStorySource:
            st.session_state.page2_video_story_toast = source.id
        except ValueError as exc:
            st.error(str(exc))
        else:
            _enqueue_operation(kind, label, {
                "ctx": ctx, "source_id": source_id, "image_operation_id": image_task_id,
                "prompt": prompt, "seconds": seconds, "generate_audio": generate_audio,
            })
    if source is not None and source.video_story_text:
        if st.button("编辑补录故事", key="page2_edit_video_story", disabled=page2_operation_queue_busy()):
            st.session_state["page2_video_story_dialog"] = source.id
    _render_video_story_toast()
    dialog_source = st.session_state.get("page2_video_story_dialog")
    if dialog_source:
        _render_video_story_dialog(dialog_source)


def _render_media_column():
    media = st.session_state.page2_generated_media
    url_preview = url_favorites.get_preview_url("page2")

    if not media:
        if url_preview:
            with st.container(key="page2_media_preview"):
                _render_url_preview_image(url_preview)
        else:
            st.caption("暂无媒体记录。")
    else:
        media_ids = []
        labels_by_id = {}
        for item in media:
            kind = item.media_kind.value if hasattr(item.media_kind, "value") else str(item.media_kind)
            label = f"{kind.upper()} • {item.provider} • {str(item.created_at or '')}"
            media_ids.append(item.id)
            labels_by_id[item.id] = label

        preview_container = st.container(key="page2_media_preview")
        prompt_container = st.container()

        selected_media_id = str(st.session_state.get("page2_selected_media_id", "") or "")
        desired_media_id = (
            selected_media_id
            if selected_media_id in media_ids
            else _latest_image_media_id(media)
        )

        media_selectbox_key = "page2_media_record_select"
        pending_media_id = str(
            st.session_state.pop("page2_pending_media_record_select", "") or ""
        )
        current_media_id = st.session_state.get(media_selectbox_key)
        if pending_media_id in media_ids:
            st.session_state[media_selectbox_key] = pending_media_id
        elif current_media_id not in media_ids:
            st.session_state[media_selectbox_key] = desired_media_id

        selected_id = st.selectbox(
            "选择记录",
            options=media_ids,
            key=media_selectbox_key,
            format_func=lambda media_id: labels_by_id.get(media_id, media_id),
            on_change=_activate_media_selection,
            label_visibility="collapsed",
        )
        st.session_state.page2_selected_media_id = selected_id
        url_preview = url_favorites.get_preview_url("page2")

        selected = None
        for item in media:
            if item.id == selected_id:
                selected = item
                break

        if url_preview:
            with preview_container:
                _render_url_preview_image(url_preview)
        elif selected is not None:
            kind = selected.media_kind.value if hasattr(selected.media_kind, "value") else str(selected.media_kind)
            with preview_container:
                if kind == GeneratedMediaKind.IMAGE.value:
                    if selected.image_data_base64:
                        st.image(_decode_image_base64(selected.image_data_base64))
                    elif selected.image_url_string:
                        st.image(selected.image_url_string)
                else:
                    url = selected.video_url_string or ""
                    if url:
                        _render_video_preview(selected)
                    else:
                        st.warning("该视频记录没有 URL。")

        if not url_preview and selected is not None:
            kind = selected.media_kind.value if hasattr(selected.media_kind, "value") else str(selected.media_kind)
            with prompt_container:
                if kind == GeneratedMediaKind.VIDEO.value:
                    model = selected.video_generation_provider or selected.provider
                    label = VIDEO_MODELS[model].label if model in VIDEO_MODELS else model
                    duration = selected.duration_seconds
                    suffix = f" · {duration:g} 秒" if isinstance(duration, (int, float)) else ""
                    st.caption(f"视频 · {label}{suffix}")
                else:
                    st.caption(f"{kind} • provider={selected.provider}")
                st.caption("prompt")
                st.code(
                    str(selected.prompt or ""),
                    language=None,
                    wrap_lines=True,
                    height=120,
                )

            if kind == GeneratedMediaKind.IMAGE.value:
                url = selected.image_url_string or ""
                if url:
                    url_favorites.render_url_display_with_copy(
                        url,
                        key=f"page2_media_url_copy_{selected.id}",
                        label="图片 URL（可复制）",
                    )
            else:
                url = selected.video_url_string or ""
                if url:
                    url_favorites.render_url_display_with_copy(
                        url,
                        key=f"page2_media_url_copy_{selected.id}",
                        label="视频 URL（可复制）",
                    )

            if st.button(
                "删除当前记录",
                use_container_width=True,
                disabled=page2_operation_queue_busy(),
            ):
                request_delete(
                    "page2_generated_media",
                    selected_id,
                    labels_by_id.get(selected_id, "当前媒体记录"),
                )

            def _delete_media(record_id: str) -> None:
                st.session_state.page2_generated_media = [
                    item for item in st.session_state.page2_generated_media if item.id != record_id
                ]
                _upsert_record()

            render_delete_confirmation("page2_generated_media", _delete_media)

    st.divider()
    st.subheader("生成图片")

    image_prompt_conversation = page2_service.build_recent_image_prompt_conversation(
        st.session_state.page2_turns,
    )
    if not image_prompt_conversation:
        st.caption("先在左侧生成一条助手回复，然后可以点击“生成图片prompt”。")

    # Separate widget state from the draft so background results can update it
    # after rendering, and hiding a field does not discard the saved draft.
    st.session_state["_page2_image_prompt_mode"] = st.session_state.page2_image_prompt_mode
    mode = st.selectbox(
        "prompt 模式",
        options=["normal", "first_person", "closeup"],
        key="_page2_image_prompt_mode",
        on_change=_save_image_prompt_field,
        args=("_page2_image_prompt_mode", "page2_image_prompt_mode"),
    )
    subject = ""
    if mode in ["first_person", "closeup"]:
        st.session_state["_page2_image_prompt_subject"] = st.session_state.page2_image_prompt_subject
        subject = st.text_input(
            "主体",
            key="_page2_image_prompt_subject",
            on_change=_save_image_prompt_field,
            args=("_page2_image_prompt_subject", "page2_image_prompt_subject"),
        )

    if st.button(
        "生成图片prompt",
        use_container_width=True,
        disabled=(
            _operation_kind_busy("image_prompt")
            or (not image_prompt_conversation and not _has_earlier_operation("chat"))
        ),
    ):
        _enqueue_operation(
            "image_prompt",
            "生成图片 prompt",
            {"mode": mode, "subject": subject},
        )

    st.session_state["_page2_image_prompt"] = st.session_state.page2_image_prompt
    prompt_text = st.text_area(
        "图片prompt（可编辑）",
        key="_page2_image_prompt",
        height=160,
        on_change=_save_image_prompt_field,
        args=("_page2_image_prompt", "page2_image_prompt"),
    )

    provider = st.selectbox(
        "图片生成 provider",
        options=["grok", "grokpro2k", "grokPro", "flux", "nanoPro", "nano"],
        index=0,
    )
    saved_ratios = settings.get(AppStorageKeys.PAGE2_IMAGE_ASPECT_RATIOS, {})
    saved_ratios = dict(saved_ratios) if isinstance(saved_ratios, dict) else {}
    saved_ratio = saved_ratios.get(provider, page2_service.IMAGE_ASPECT_RATIO)
    if saved_ratio not in page2_service.IMAGE_ASPECT_RATIOS:
        saved_ratio = page2_service.IMAGE_ASPECT_RATIO
    aspect_ratio = st.selectbox(
        "图片尺寸",
        options=page2_service.IMAGE_ASPECT_RATIOS,
        index=page2_service.IMAGE_ASPECT_RATIOS.index(saved_ratio),
        key=f"page2_image_aspect_ratio_{provider}",
    )
    if aspect_ratio != saved_ratio:
        saved_ratios[provider] = aspect_ratio
        settings.set(AppStorageKeys.PAGE2_IMAGE_ASPECT_RATIOS, saved_ratios)
    image_urls_raw = st.text_area("参考图片 URL（每行一个，可选）", value="", height=90)
    image_urls = [line.strip() for line in image_urls_raw.splitlines() if line.strip()]

    if st.button(
        "生成图片",
        type="primary",
        use_container_width=True,
        disabled=_operation_kind_busy("image"),
    ):
        prompt_dependency = _latest_earlier_operation("image_prompt")
        _enqueue_operation(
            "image",
            "生成图片",
            {
                "provider": provider,
                "prompt": prompt_text,
                "image_urls": image_urls,
                "aspect_ratio": aspect_ratio,
                "prompt_operation_id": prompt_dependency.id if prompt_dependency else "",
                "image_prompt_source": dict(
                    st.session_state.get("page2_image_prompt_source")
                    or latest_image_prompt_source(list(st.session_state.page2_turns))
                ),
            },
        )

    st.divider()
    st.subheader("图生视频")
    video_model = _render_video_model_selector()
    st.caption("从上面的媒体列表里选择一张图片作为输入。")

    _render_video_prompt_controls(media, video_model)

    url_favorites.render_url_favorites("page2")


def render():
    _ensure_state()
    _load_record_from_nav_if_needed()
    st.html(
        """
<style>
/* Page2: use the space released by the transparent, zero-height app header. */
section[data-testid="stMain"] .block-container {
  padding-top: 20px !important;
  padding-bottom: 20px !important;
}

/* Keep the image fullscreen control inside the preview's top-right corner. */
.st-key-page2_media_preview
  [data-testid="stElementToolbar"]:has(button[aria-label="Fullscreen"]) {
  top: 8px !important;
  right: 8px !important;
  padding: 0 !important;
  opacity: 0 !important;
  visibility: hidden !important;
  pointer-events: none !important;
  transition: opacity 150ms ease, visibility 150ms ease !important;
  z-index: 10 !important;
}

.st-key-page2_media_preview
  [data-testid="stFullScreenFrame"]:hover
  [data-testid="stElementToolbar"]:has(button[aria-label="Fullscreen"]),
.st-key-page2_media_preview
  [data-testid="stFullScreenFrame"]:focus-within
  [data-testid="stElementToolbar"]:has(button[aria-label="Fullscreen"]) {
  top: 8px !important;
  opacity: 1 !important;
  visibility: visible !important;
  pointer-events: auto !important;
}
</style>
        """,
    )

    # Keep the chat canvas and portrait image preview balanced on a full-screen layout.
    left, right = st.columns([1.00, 1.00])
    with left:
        _render_chat_column()
    with right:
        _render_media_column()

    _render_operation_queue_status()
