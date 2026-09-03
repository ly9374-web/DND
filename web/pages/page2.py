from __future__ import annotations

import base64
import json
import threading
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from app.config import AppStorageKeys, settings, user_facing_error_message
from app.models import (
    CHAT_MODEL_LABELS,
    CHAT_MODEL_OPTIONS,
    GeneratedMediaKind,
    Page2ConversationTurn,
)
from app.services import chat_records, page2_service, system_prompts
from web.nav import get_arg, goto
from web.pages import url_favorites
from web.components.delete_confirmation import render_delete_confirmation, request_delete


_story_brain_text_editor_component = components.declare_component(
    "page2_story_brain_text_editor",
    path=str(Path(__file__).resolve().parents[1] / "components" / "story_brain_text_editor"),
)

_SINGLE_FLIGHT_OPERATION_KINDS = {
    "chat",
    "story_brain",
    "image_prompt",
    "image",
    "video",
}

_EMPTY_SPACE_CONTINUE_MESSAGE = "继续发展"
_EMPTY_SPACE_SUBMISSION_SENTINEL = "\u2063\u2063\u2063"


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


def _render_empty_space_submit_bridge() -> None:
    """Turn a Space press on an empty chat box into an invisible submission."""
    sentinel_json = json.dumps(_EMPTY_SPACE_SUBMISSION_SENTINEL, ensure_ascii=False)
    components.html(
        f"""
        <script>
          (() => {{
            const host = window.parent;
            const doc = host.document;
            const handlerKey = "__dndEmptySpaceSubmitHandler";
            const previousHandler = host[handlerKey];
            if (previousHandler) {{
              doc.removeEventListener("keydown", previousHandler, true);
            }}

            const handler = (event) => {{
              if (
                event.key !== " " ||
                event.repeat ||
                event.isComposing ||
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
    st.session_state.setdefault("page2_turns", [])
    st.session_state.setdefault("page2_generated_media", [])
    st.session_state.setdefault("page2_selected_media_id", "")
    st.session_state.setdefault("page2_pending_media_record_select", "")
    st.session_state.setdefault("page2_image_prompt", "")
    st.session_state.setdefault("page2_image_prompt_mode", "normal")
    st.session_state.setdefault("page2_image_prompt_subject", "")
    st.session_state.setdefault("page2_video_prompt", "动起来")
    st.session_state.setdefault("page2_story_brain_archive", {})
    st.session_state.setdefault("page2_story_brain_short", "")
    st.session_state.setdefault("page2_story_brain_enabled", True)
    st.session_state.setdefault("page2_story_brain_short_editor_nonce", 0)
    st.session_state.setdefault("page2_story_brain_short_update_notice", "")
    st.session_state.setdefault("page2_story_brain_retry_pending", False)
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
    if active is not None and active.kind == kind:
        return True
    return any(
        task.kind == kind
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
) -> _Page2OperationTask:
    if kind in _SINGLE_FLIGHT_OPERATION_KINDS:
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
    matches = [task for task in operations if task.kind == kind]
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
    st.session_state.page2_turns = []
    st.session_state.page2_generated_media = []
    st.session_state.page2_selected_media_id = ""
    st.session_state.page2_pending_media_record_select = ""
    url_favorites.clear_preview_url("page2")
    st.session_state.page2_image_prompt = ""
    st.session_state.page2_image_prompt_mode = "normal"
    st.session_state.page2_image_prompt_subject = ""
    st.session_state.page2_video_prompt = "动起来"
    st.session_state.page2_story_brain_archive = {}
    st.session_state.page2_story_brain_short = ""
    st.session_state.page2_story_brain_enabled = True
    st.session_state["page2_story_brain_short_update_notice"] = ""
    st.session_state["page2_story_brain_retry_pending"] = False
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

    st.session_state.page2_record_id = record.id
    st.session_state.page2_loaded_record_id = record.id
    st.session_state["page2_operation_scope_id"] = "record:" + str(record.id)
    st.session_state.page2_turns = list(record.turns or [])
    st.session_state.page2_generated_media = list(record.generated_images or [])
    st.session_state.page2_story_brain_archive = record.story_brain
    st.session_state.page2_story_brain_short = str(getattr(record, "story_brain_short", "") or "").strip()
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
        if turn.assistant_message:
            return turn.assistant_message
    return ""


def _conversation_started(turns: list[Page2ConversationTurn]) -> bool:
    """用户是否已在本对话中真正发言。"""
    return any(str(turn.user_message or "").strip() for turn in turns)


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
            st.markdown(text or "")
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
        scope=_chat_scope(),
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


def _save_short_story_brain_to_current_record(story_brain: str):
    story_brain_text = str(story_brain or "").strip()
    if story_brain_text in page2_service.INVALID_STORY_BRAIN_TEXTS:
        story_brain_text = ""
    st.session_state.page2_story_brain_short = story_brain_text
    _upsert_record()


def _render_short_story_brain_text_editor(value: str) -> str:
    result = _story_brain_text_editor_component(
        value=str(value or ""),
        key=(
            "page2_story_brain_text_editor_component_"
            + str(int(st.session_state.get("page2_story_brain_short_editor_nonce", 0) or 0))
        ),
        default=None,
    )
    if isinstance(result, str):
        return result
    return str(value or "")


def _completed_turn_count(turns: list[Page2ConversationTurn]) -> int:
    """统计真实完成的用户问答轮次。

    只有用户消息和助手回复都非空才计数，因此自动开场白不会提前触发 Story Brain 更新。
    """
    return sum(
        1
        for turn in turns
        if str(turn.user_message or "").strip()
        and str(turn.assistant_message or "").strip()
    )


def _should_update_story_brain(
    turns: list[Page2ConversationTurn],
    update_interval: int,
    retry_pending: bool = False,
) -> bool:
    completed_turn_count = _completed_turn_count(turns)
    interval = max(1, int(update_interval))
    return completed_turn_count > 0 and (
        bool(retry_pending) or completed_turn_count % interval == 0
    )


def _manual_story_brain_update_context(
    turns: list[Page2ConversationTurn],
) -> tuple[str, str, str]:
    """为手动更新选取最近一个完整问答，并复用自动更新的上下文形式。"""
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
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
        selected_prompt_id = str(settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or "")
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
            manual_update_disabled = (
                _chat_edit_active()
                or _operation_kind_busy("story_brain")
                or not bool(st.session_state.get("page2_story_brain_enabled", True))
                or not bool(manual_turn_id or _has_earlier_operation("chat"))
            )
            if st.button(
                "更新storybrain",
                key="page2_sidebar_manual_story_brain_update_btn",
                use_container_width=True,
                disabled=manual_update_disabled,
                help="立即使用最近一个完整问答更新当前 Story Brain。",
            ):
                ctx.story_brain_update_model = story_brain_update_model
                ctx.story_brain_reasoning_enabled = story_brain_reasoning == "开思考"
                ctx.story_brain_turns = int(story_brain_turns)
                page2_service.save_context_to_settings(ctx)
                st.session_state["page2_story_brain_short_update_notice"] = ""
                wait_for_chat = _has_earlier_operation("chat")
                _schedule_story_brain_update(
                    ctx=ctx,
                    turns=list(st.session_state.page2_turns),
                    memory_source_text="" if wait_for_chat else manual_memory_source,
                    reply="" if wait_for_chat else manual_reply,
                    turn_id="" if wait_for_chat else manual_turn_id,
                )
                st.rerun()
            temperature = st.slider(
                "temperature",
                min_value=0.0,
                max_value=2.0,
                value=float(ctx.temperature),
                step=0.05,
                key="page2_sidebar_temperature",
            )
            video_provider = st.selectbox(
                "图生视频",
                options=["domoai", "zhipu", "wan22Fast"],
                index=["domoai", "zhipu", "wan22Fast"].index(ctx.selected_video_generation_provider)
                if ctx.selected_video_generation_provider in ["domoai", "zhipu", "wan22Fast"]
                else 0,
                format_func=lambda item: (
                    "Wan 2.2 I2V Fast" if item == "wan22Fast" else item
                ),
                key="page2_sidebar_video_provider",
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
            ctx.selected_video_generation_provider = video_provider
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
    _save_short_story_brain_to_current_record(result)
    _refresh_short_story_brain_editor()


def _schedule_story_brain_update(
    *,
    ctx,
    turns: list[Page2ConversationTurn],
    memory_source_text: str,
    reply: str,
    turn_id: str,
    first: bool = False,
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
    _apply_short_sb_result(updated_story_brain)
    if updated_story_brain == str(task.meta.get("previous_short_story_brain", "") or ""):
        notice = (
            f"Story Brain 第 {int(task.meta.get('completed_turn_count', 0))} 轮检查完成，"
            "本轮内容无变化。"
        )
    else:
        notice = f"Story Brain 已在第 {int(task.meta.get('completed_turn_count', 0))} 轮更新并保存。"
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
        first_input = system_prompts.selected_first_input(prompt_state)
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
    task.meta.update(
        {
            "turn_id": turn_id,
            "completed_turn_count": completed_turn_count,
        }
    )
    previous_story_brain = _current_short_story_brain()
    task.meta["previous_short_story_brain"] = previous_story_brain
    return lambda: page2_service.generate_short_story_brain(
        ctx=ctx,
        turns=turns_snapshot,
        story_brain_short=previous_story_brain,
    )


def _prepare_image_prompt_operation(task: _Page2OperationTask):
    conversation_text = page2_service.build_recent_image_prompt_conversation(
        st.session_state.page2_turns,
    )
    mode = str(task.payload.get("mode", "normal") or "normal")
    subject = str(task.payload.get("subject", "") or "")
    return lambda: page2_service.generate_image_prompt(
        conversation_text,
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
    else:
        prompt = str(task.payload.get("prompt", "") or "")
    provider = str(task.payload.get("provider", "") or "")
    image_urls = list(task.payload.get("image_urls") or [])
    return lambda: page2_service.generate_image(
        provider=provider,
        prompt=prompt,
        image_urls=image_urls,
    )


def _prepare_video_operation(task: _Page2OperationTask):
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

    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    prompt = str(task.payload.get("prompt", "") or "")
    seconds = int(task.payload.get("seconds", 5) or 5)
    return lambda: page2_service.generate_video_from_image(
        ctx=ctx,
        source_record=source,
        prompt=prompt,
        seconds=seconds,
    )


def _prepare_operation(task: _Page2OperationTask):
    if task.scope_id != _operation_scope_id():
        raise RuntimeError("当前记录已变更，为避免写入错误记录，任务已终止。")
    if task.kind == "chat":
        return _prepare_chat_operation(task)
    if task.kind == "story_brain":
        return _prepare_story_brain_operation(task)
    if task.kind == "image_prompt":
        return _prepare_image_prompt_operation(task)
    if task.kind == "image":
        return _prepare_image_operation(task)
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

    ctx = task.meta.get("ctx") or page2_service.load_context_from_settings()
    story_brain_enabled = bool(task.meta.get("story_brain_enabled", True))
    if story_brain_enabled and task.error is None:
        retry_pending = bool(st.session_state.get("page2_story_brain_retry_pending", False))
        if _should_update_story_brain(
            st.session_state.page2_turns,
            ctx.story_brain_turns,
            retry_pending=retry_pending,
        ):
            _schedule_story_brain_update(
                ctx=ctx,
                turns=st.session_state.page2_turns,
                memory_source_text=str(task.meta.get("memory_source_text", "") or ""),
                reply=reply,
                turn_id=turn_id,
                first=True,
            )
    st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex


def _apply_operation(task: _Page2OperationTask) -> None:
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
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        outputs[task.id] = st.session_state.page2_image_prompt
        st.session_state["page2_operation_outputs"] = outputs
        return
    if task.kind in {"image", "video"}:
        record = task.result
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


@st.fragment(run_every=1)
def _render_operation_queue_status() -> None:
    """串行启动队首任务，并在主线程完成结果写回。"""
    active = st.session_state.get("page2_active_operation")
    queue = list(st.session_state.get("page2_operation_queue") or [])
    if active is None and queue:
        active = queue.pop(0)
        st.session_state["page2_active_operation"] = active
        st.session_state["page2_operation_queue"] = queue

    if active is None:
        notice = str(st.session_state.pop("page2_operation_notice", "") or "")
        error = str(st.session_state.pop("page2_operation_error", "") or "")
        if error:
            st.error(error)
        elif notice:
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
        return

    try:
        _apply_operation(active)
        if active.error is None:
            st.session_state["page2_operation_notice"] = f"已完成：{active.label}"
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

        input_placeholder = "输入消息并回车发送" if turns else "输入“开始”以开始游戏"
        with st.container(key="page2_chat_composer"):
            user_text = st.chat_input(
                input_placeholder,
                key="page2_chat_input",
                disabled=chat_edit_active or _operation_kind_busy("chat"),
            )
            _render_empty_space_submit_bridge()
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
            st.caption("队列执行中，Story Brain 编辑暂时锁定。")
        else:
            notice = str(st.session_state.pop("page2_story_brain_short_update_notice", "") or "").strip()
            if notice:
                if notice.startswith("Story Brain 更新失败"):
                    st.error(notice)
                else:
                    st.success(notice)
            story_brain_short = _current_short_story_brain()
            next_story_brain_short = _render_short_story_brain_text_editor(story_brain_short)
            if next_story_brain_short != story_brain_short:
                _save_short_story_brain_to_current_record(next_story_brain_short)
                _refresh_short_story_brain_editor()
                st.rerun()

    if undo_clicked and turns:
        st.session_state.page2_turns = turns[:-1]
        _upsert_record()
        st.rerun()

    if user_text is None:
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
            on_change=url_favorites.activate_generated_media_selection,
            args=(
                "page2",
                "page2_selected_media_id",
                media_selectbox_key,
            ),
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
                        st.video(url)
                    else:
                        st.warning("该视频记录没有 URL。")

        if not url_preview and selected is not None:
            kind = selected.media_kind.value if hasattr(selected.media_kind, "value") else str(selected.media_kind)
            with prompt_container:
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

    mode = st.selectbox(
        "prompt 模式",
        options=["normal", "first_person", "closeup"],
        index=["normal", "first_person", "closeup"].index(st.session_state.page2_image_prompt_mode),
    )
    st.session_state.page2_image_prompt_mode = mode
    subject = ""
    if mode in ["first_person", "closeup"]:
        subject = st.text_input("主体", value=st.session_state.page2_image_prompt_subject)
        st.session_state.page2_image_prompt_subject = subject

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

    prompt_text = st.text_area("图片prompt（可编辑）", value=st.session_state.page2_image_prompt, height=160)
    st.session_state.page2_image_prompt = prompt_text

    provider = st.selectbox(
        "图片生成 provider",
        options=["grok", "grokQuality", "grokPro", "flux", "nanoPro", "nano"],
        index=0,
    )
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
                "prompt_operation_id": prompt_dependency.id if prompt_dependency else "",
            },
        )

    st.divider()
    st.subheader("图生视频")
    st.caption("从上面的媒体列表里选择一张图片作为输入。")

    image_candidates = [m for m in media if (m.media_kind.value if hasattr(m.media_kind, "value") else str(m.media_kind)) == GeneratedMediaKind.IMAGE.value]
    image_dependency = _latest_earlier_operation("image")
    queued_image_dependency = image_dependency is not None
    source_id = ""
    if not image_candidates:
        if queued_image_dependency:
            st.caption("将在前面的生图任务完成后，使用新图片生成视频。")
        else:
            st.caption("暂无可用图片。")
    else:
        candidate_labels = []
        label_to_id = {}
        for item in image_candidates:
            label = f"{item.provider} • {str(item.created_at or '')} • {item.prompt[:24] if item.prompt else ''}"
            candidate_labels.append(label)
            label_to_id[label] = item.id

        selected_media_id = str(st.session_state.get("page2_selected_media_id", "") or "")
        selected_image_index = len(image_candidates) - 1
        if selected_media_id:
            for index, item in enumerate(image_candidates):
                if item.id == selected_media_id:
                    selected_image_index = index
                    break

        selected_label = st.selectbox(
            "选择输入图片",
            options=candidate_labels,
            index=selected_image_index,
        )
        source_id = label_to_id.get(selected_label, "")

    video_prompt = st.text_input("视频 prompt", key="page2_video_prompt")
    ctx = page2_service.load_context_from_settings()
    if str(ctx.selected_video_generation_provider or "") == "zhipu":
        seconds = st.selectbox("时长（秒）", options=[5, 10], index=0)
    elif str(ctx.selected_video_generation_provider or "") == "wan22Fast":
        seconds = 5
        st.caption("Wan 2.2 I2V Fast：720p · 81 帧 · 16 FPS · 约 5.1 秒")
    else:
        seconds = st.number_input("时长（秒）", min_value=1, max_value=10, value=5)

    if st.button(
        "生成视频",
        use_container_width=True,
        disabled=(
            _operation_kind_busy("video")
            or not bool(image_candidates or queued_image_dependency)
        ),
    ):
        _enqueue_operation(
            "video",
            "生成视频",
            {
                "ctx": ctx,
                "source_id": source_id,
                "image_operation_id": image_dependency.id if image_dependency else "",
                "prompt": video_prompt,
                "seconds": int(seconds),
            },
        )

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
