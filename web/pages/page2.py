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

from app.config import (
    DATA_DIR,
    AppStorageKeys,
    CloudinaryConfig,
    OpenRouterConfig,
    get_effective_api_key,
    settings,
    user_facing_error_message,
)
from app.default_prompts.internal_catalog import prompt, render as render_internal_prompt
from app.api.media_clients import (
    ImagePollingStopped,
    download_image_bytes,
    download_video_bytes,
    is_openrouter_video_url,
)
from app.api.speech_client import EMOTION_TAGS, MODEL as TTS_MODEL, VOICES, SpeechError
from app.video_models import (
    VIDEO_MODELS, WAN_MODEL, WAN3_MODEL, WAN3_ASPECT_RATIOS,
    WAN3_RESOLUTIONS, WAN_DURATION_FRAMES, WAN_FPS,
    normalize_video_model, video_duration_label,
)
from app.models import (
    CHAT_MODEL_LABELS,
    CHAT_MODEL_OPTIONS,
    GeneratedMediaKind,
    Page2ConversationTurn,
    chat_model_supports_reasoning,
)
from app.services import chat_records, page2_service, quick_inputs, story_brain_history, system_prompts
from app.services import media_store, image_references
from app.services.speech_generation import ACTIVE, SpeechJobs, extract_quoted_lines, owner_key
from app.services.video_prompts import (
    MissingVideoStorySource, latest_image_prompt_source, resolve_video_story,
)
from web.components.video_prompt import render_editor_sizing, render_toast_style
from web.components.media_button_guard import render_media_button_guard
from web.components.quick_input import quick_input_script
from web import prompt_draft_guard
from app.image_models import (
    DEFAULT_IMAGE_PROVIDER,
    IMAGE25_PROVIDER, IMAGE_QUALITY_PROVIDERS,
    IMAGE25_QUALITIES, IMAGE_DEFAULT_QUALITY,
    IMAGE_PROVIDERS, image_aspect_ratios, image_aspect_ratio_label,
    normalize_image_provider, validate_image_options,
)
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

_EMPTY_SPACE_CONTINUE_MESSAGE = prompt("chat", "empty_enter")
_EMPTY_SPACE_SUBMISSION_SENTINEL = "\u2063\u2063\u2063"
_MANUAL_STORY_BRAIN_UPDATE_DELAY = 5
def _chat_scope() -> str | None:
    mode = str(st.session_state.get("auth_mode", "") or "").strip().lower()
    return "guest" if mode == "guest" else None


def _decode_image_base64(b64: str) -> bytes:
    return base64.b64decode(b64.encode("ascii"))


def _image_download_suffix(url: str) -> str:
    clean_url = str(url or "").split("?", 1)[0].lower()
    for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if clean_url.endswith(suffix):
            return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def _delete_media_record(record_id: str) -> None:
    record = next((item for item in st.session_state.page2_generated_media if item.id == record_id), None)
    record = record or _find_video_source(record_id)
    if record is not None:
        media_store.delete(record)
    st.session_state.page2_generated_media = [
        item for item in st.session_state.page2_generated_media
        if item.id != record_id
    ]
    if str(st.session_state.get("page2_selected_media_id", "") or "") == record_id:
        remaining_images = [
            item for item in st.session_state.page2_generated_media
            if item.media_kind == GeneratedMediaKind.IMAGE and media_store.available(item)
        ]
        generated_images = [item for item in remaining_images if item.provider != "local"]
        fallback = (generated_images or remaining_images)[-1] if remaining_images else None
        if fallback is not None:
            _select_media_preview(fallback.id)
        else:
            st.session_state.page2_selected_media_id = ""
            st.session_state.pop("page2_pending_media_record_select", None)
            st.session_state["page2_media_selection_nonce"] = int(
                st.session_state.get("page2_media_selection_nonce", 0)
            ) + 1
    _upsert_record()


def _save_image_record(record) -> None:
    media_store.save(record)
    _upsert_record()


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
    saved_durations = settings.get(AppStorageKeys.PAGE2_VIDEO_DURATIONS, {})
    saved_durations = dict(saved_durations) if isinstance(saved_durations, dict) else {}
    saved_audio = settings.get(AppStorageKeys.PAGE2_VIDEO_AUDIO_BY_MODEL, {})
    saved_audio = dict(saved_audio) if isinstance(saved_audio, dict) else {}
    previous = st.session_state.get(key)
    if st.session_state.get("_page2_video_controls_model") != model:
        chosen = saved_durations.get(model, 5)
        st.session_state[key] = chosen if chosen in spec.durations else 5
        audio_choice = saved_audio.get(model, True)
        st.session_state["page2_video_generate_audio"] = (
            audio_choice if isinstance(audio_choice, bool) else True
        )
        st.session_state["_page2_video_controls_model"] = model
    elif previous not in spec.durations:
        st.session_state[key] = 5
    if previous is not None and previous not in spec.durations:
        st.caption("之前的时长不适用于当前模型，已调整为 5 秒。")
    seconds = st.selectbox(
        "时长（秒）", options=spec.durations, key=key,
        format_func=lambda value: video_duration_label(model, value),
    )
    if seconds != saved_durations.get(model, 5):
        saved_durations[model] = seconds
        settings.set(AppStorageKeys.PAGE2_VIDEO_DURATIONS, saved_durations)
    if model == WAN_MODEL:
        st.caption(f"720p · 约 {WAN_DURATION_FRAMES[seconds] / WAN_FPS:.2f} 秒 · 无声音")
        return seconds, False
    generate_audio = st.toggle("生成声音", value=True, key="page2_video_generate_audio")
    if generate_audio != saved_audio.get(model, True):
        saved_audio[model] = generate_audio
        settings.set(AppStorageKeys.PAGE2_VIDEO_AUDIO_BY_MODEL, saved_audio)
    if model != WAN3_MODEL:
        st.caption("720p · 比例跟随输入图片；需要说话时，请在 prompt 中写明台词。")
    return seconds, generate_audio


def _render_wan3_resolution_selector() -> str:
    saved = settings.get(AppStorageKeys.PAGE2_WAN3_RESOLUTION, WAN3_RESOLUTIONS[0])
    if saved not in WAN3_RESOLUTIONS:
        saved = WAN3_RESOLUTIONS[0]
    resolution = st.selectbox(
        "分辨率", options=WAN3_RESOLUTIONS,
        index=WAN3_RESOLUTIONS.index(saved), key="page2_wan3_resolution",
    )
    if resolution != saved:
        settings.set(AppStorageKeys.PAGE2_WAN3_RESOLUTION, resolution)
    return resolution


def _render_wan3_input_mode(selected_id: str | None) -> bool:
    key = "page2_wan3_input_mode"
    saved = settings.get(AppStorageKeys.PAGE2_WAN3_INPUT_MODE)
    if st.session_state.get(key) not in {"图片首帧", "纯文字"}:
        st.session_state[key] = (
            saved if isinstance(saved, str) and saved in {"图片首帧", "纯文字"}
            else ("图片首帧" if selected_id else "纯文字")
        )
    chosen = st.selectbox("输入方式", options=("图片首帧", "纯文字"), key=key)
    if chosen != saved:
        settings.set(AppStorageKeys.PAGE2_WAN3_INPUT_MODE, chosen)
    return chosen == "纯文字"


def _render_wan3_aspect_ratio_selector() -> str:
    saved = settings.get(AppStorageKeys.PAGE2_WAN3_ASPECT_RATIO, WAN3_ASPECT_RATIOS[0])
    if saved not in WAN3_ASPECT_RATIOS:
        saved = WAN3_ASPECT_RATIOS[0]
    ratio = st.selectbox(
        "画面比例", options=WAN3_ASPECT_RATIOS,
        index=WAN3_ASPECT_RATIOS.index(saved), key="page2_wan3_aspect_ratio",
    )
    if ratio != saved:
        settings.set(AppStorageKeys.PAGE2_WAN3_ASPECT_RATIO, ratio)
    return ratio


def _render_video_preview(record):
    if media_store.local_path(record):
        st.video(media_store.read_bytes(record), format="video/mp4", muted=False, autoplay=False)
        return
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
    st.caption("正在尝试存入本地；成功后保留 48 小时，点击保存可永久保留。")


def _render_empty_space_submit_bridge(*, send_disabled: bool = False) -> None:
    """Keep drafts editable while blocking submission, including blank shortcuts."""
    sentinel_json = json.dumps(_EMPTY_SPACE_SUBMISSION_SENTINEL, ensure_ascii=False)
    hidden_space = bool(st.session_state.get("hidden_unlocked", False))
    st.iframe(
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
        height=1,
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
    st.session_state.setdefault("page2_media_selection_nonce", 0)
    st.session_state.setdefault("page2_image_prompt", "")
    st.session_state.setdefault("page2_image_prompt_mode", _saved_image_prompt_mode())
    st.session_state.setdefault("page2_image_prompt_subject", "")
    st.session_state.setdefault("page2_image_prompt_source", {})
    st.session_state.setdefault("page2_refiner_prompt", "")
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
    st.session_state.setdefault("page2_auto_image_next_turn", None)
    st.session_state.setdefault("page2_auto_image_anchor_turn_count", None)
    st.session_state.setdefault("page2_story_brain_checkpoints", [])
    st.session_state.setdefault("page2_operation_queue", [])
    st.session_state.setdefault("page2_active_operation", None)
    st.session_state.setdefault("page2_active_media_operations", {})
    st.session_state.setdefault("page2_media_completions", [])
    st.session_state.setdefault("page2_operation_scope_id", uuid.uuid4().hex)
    st.session_state.setdefault("page2_operation_notice", "")
    st.session_state.setdefault("page2_operation_error", "")
    st.session_state.setdefault("page2_operation_outputs", {})
    st.session_state.setdefault("page2_chat_edit_target", None)
    st.session_state.setdefault("page2_tts_active", None)
    st.session_state.setdefault("page2_tts_dialog_turn_id", "")


_CHAT_SPEECH_RETENTION_SECONDS = 48 * 60 * 60


@st.cache_resource
def chat_speech_jobs(version: int = 2) -> SpeechJobs:
    """Durable speech jobs for chat messages; separate from the 生成语音 page."""
    return SpeechJobs(
        DATA_DIR / "ChatSpeechJobs",
        DATA_DIR / "ChatSpeech",
    )


def _chat_speech_owner() -> str:
    return owner_key("chat_tts_" + str(st.session_state.get("user_id") or "1"))


def _dashscope_api_key() -> str:
    try:
        return get_effective_api_key(
            settings.get(AppStorageKeys.DASHSCOPE_API_KEY, ""),
            "DASHSCOPE_API_KEY",
        )
    except ValueError as exc:
        raise SpeechError("DashScope Key 包含无效字符，请到 APIkey 页面重新填写。") from exc


def _has_dashscope_key() -> bool:
    try:
        return bool(_dashscope_api_key())
    except SpeechError:
        return False


def _tts_audio_path(filename: str) -> Path:
    return chat_speech_jobs().output / Path(filename).name


def _cleanup_expired_chat_speech() -> None:
    """Delete chat speech WAV files older than the retention window."""
    import time
    output = chat_speech_jobs().output
    if not output.is_dir():
        return
    cutoff = time.time() - _CHAT_SPEECH_RETENTION_SECONDS
    for path in output.glob("*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _turn_has_audio(turn: Page2ConversationTurn) -> bool:
    for clip in getattr(turn, "assistant_audio_clips", []) or []:
        if isinstance(clip, dict) and _tts_audio_path(str(clip.get("filename", ""))).is_file():
            return True
    filename = str(getattr(turn, "assistant_audio_filename", "") or "")
    if not filename:
        return False
    return _tts_audio_path(filename).is_file()


def _start_turn_tts(turn: Page2ConversationTurn, clips: list[dict]) -> bool:
    """Start independent quote files for one Assistant turn."""
    try:
        api_key = _dashscope_api_key()
    except SpeechError as exc:
        st.session_state["page2_tts_error"] = str(exc)
        return False
    jobs = chat_speech_jobs()
    owner = _chat_speech_owner()
    try:
        job_id = jobs.start_quotes(owner, clips, api_key)
    except (SpeechError, OSError) as exc:
        st.session_state["page2_tts_error"] = str(exc)
        return False
    st.session_state["page2_tts_active"] = {
        "turn_id": str(turn.id), "job_id": job_id,
        "source_text": str(turn.assistant_message or ""),
    }
    st.session_state.pop("page2_tts_error", None)
    st.session_state.pop("page2_tts_failed", None)
    return True


def _apply_active_tts_result() -> None:
    """Append a finished TTS batch to its assistant turn."""
    active = st.session_state.get("page2_tts_active")
    if not isinstance(active, dict):
        return
    turn_id = str(active.get("turn_id", "") or "")
    job_id = str(active.get("job_id", "") or "")
    if not turn_id or not job_id:
        st.session_state["page2_tts_active"] = None
        return
    jobs = chat_speech_jobs()
    owner = _chat_speech_owner()
    try:
        job = jobs.get(job_id, owner)
    except (SpeechError, OSError, ValueError):
        st.session_state["page2_tts_active"] = None
        return
    status = job.get("status")
    if status in ACTIVE:
        return  # still running; leave active state in place
    st.session_state["page2_tts_active"] = None
    if status != "completed":
        st.session_state["page2_tts_failed"] = {
            "turn_id": turn_id, "job_id": job_id,
            "error": str(job.get("error", "") or "语音生成失败。"),
            "source_text": str(active.get("source_text", "")),
        }
        return
    clips = [clip for clip in job.get("clips", [])
             if _tts_audio_path(str(clip.get("filename", ""))).is_file()]
    if not clips:
        return
    for turn in st.session_state.get("page2_turns", []):
        if str(turn.id) == turn_id:
            if str(turn.assistant_message or "") != str(active.get("source_text", "")):
                return
            existing = list(getattr(turn, "assistant_audio_clips", []) or [])
            filenames = {str(clip.get("filename", "")) for clip in existing
                         if isinstance(clip, dict)}
            for clip in clips:
                if str(clip.get("filename", "")) not in filenames:
                    existing.append(clip)
                    filenames.add(str(clip.get("filename", "")))
            turn.assistant_audio_clips = existing
            break
    _upsert_record()


def _tts_stage_caption(job: dict) -> str:
    phase = job.get("phase", "synthesizing")
    description = {
        "preparing": "正在准备",
        "synthesizing": "正在合成",
        "downloading": "正在下载音频",
        "saving": "正在保存片段",
        "merging": "正在合并保存",
    }.get(phase, "正在处理")
    if phase != "merging":
        description = (
            f"第 {job.get('current_chunk', job['completed'] + 1)} / {job['total']} 段 · {description}"
        )
    return description


@st.fragment(run_every=1)
def _render_turn_tts_status(turn: Page2ConversationTurn) -> None:
    """Render TTS progress and all saved audio for an assistant turn."""
    active_before = st.session_state.get("page2_tts_active") or {}
    _apply_active_tts_result()
    active = st.session_state.get("page2_tts_active") or {}
    if str(active_before.get("turn_id", "")) == str(turn.id) and not active:
        st.rerun()
    if str(active.get("turn_id", "")) == str(turn.id):
        jobs = chat_speech_jobs()
        owner = _chat_speech_owner()
        try:
            job = jobs.get(str(active.get("job_id", "")), owner)
        except (SpeechError, OSError, ValueError):
            job = None
        if job and job.get("status") in ACTIVE:
            completed = int(job.get("completed", 0))
            total = max(1, int(job.get("total", 1)))
            st.progress(completed / total, text=f"语音合成中 {completed} / {total} 段")
            st.caption(_tts_stage_caption(job))
        elif not _turn_has_audio(turn):
            st.rerun()
    failed = st.session_state.get("page2_tts_failed") or {}
    if str(failed.get("turn_id", "")) == str(turn.id):
        st.error(str(failed.get("error", "语音生成失败。")))
        if st.button("继续生成", key=f"page2_tts_retry_{turn.id}"):
            try:
                chat_speech_jobs().retry(
                    str(failed["job_id"]), _chat_speech_owner(), _dashscope_api_key(),
                )
                st.session_state["page2_tts_active"] = {
                    "turn_id": str(turn.id), "job_id": str(failed["job_id"]),
                    "source_text": str(failed.get("source_text", "")),
                }
                st.session_state.pop("page2_tts_failed", None)
                st.rerun()
            except (SpeechError, OSError) as exc:
                st.error(str(exc))
    clips = getattr(turn, "assistant_audio_clips", []) or []
    legacy_filename = str(getattr(turn, "assistant_audio_filename", "") or "")
    shown = False
    if legacy_filename:
        path = _tts_audio_path(legacy_filename)
        if path.is_file():
            shown = True
            st.audio(str(path), format="audio/wav")
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        path = _tts_audio_path(str(clip.get("filename", "")))
        if not path.is_file():
            continue
        shown = True
        st.audio(str(path), format="audio/wav")
    if (clips or legacy_filename) and not shown:
        st.caption("语音文件已失效，可重新生成。")


def _close_tts_quote_dialog() -> None:
    st.session_state["page2_tts_dialog_turn_id"] = ""


def _render_tts_quote_dialog(default_voice: str) -> None:
    turn_id = str(st.session_state.get("page2_tts_dialog_turn_id", "") or "")
    if not turn_id:
        return
    turn = next((item for item in st.session_state.get("page2_turns", [])
                 if str(item.id) == turn_id), None)
    if turn is None:
        st.session_state["page2_tts_dialog_turn_id"] = ""
        return
    nonce = str(st.session_state.get("page2_tts_dialog_nonce", ""))

    @st.dialog("选择台词生成语音", width="large", on_dismiss=_close_tts_quote_dialog)
    def _dialog():
        quotes = extract_quoted_lines(str(turn.assistant_message or ""))
        if not quotes:
            st.info("这条回复没有成对双引号中的台词。")
        st.html("""<style>
        div[class*="st-key-page2_tts_select_"] button[kind="primary"] {
            background: rgba(22,131,74,.28) !important; border-color: #16834a; color: white;
        }
        </style>""")
        selected = []
        for index, quote in enumerate(quotes):
            selected_key = f"page2_tts_selected_{nonce}_{index}"
            voice_key = f"page2_tts_voice_{nonce}_{index}"
            emotion_key = f"page2_tts_emotion_{nonce}_{index}"
            preview_key = f"page2_tts_preview_{nonce}_{index}"
            if voice_key not in st.session_state:
                st.session_state[voice_key] = default_voice
            col_quote, col_emotion, col_voice = st.columns([5, 2, 2], vertical_alignment="center")
            with col_quote:
                st.button(
                    f"{index + 1}. {quote}",
                    key=f"page2_tts_select_{nonce}_{index}",
                    type="primary" if st.session_state.get(selected_key, False) else "secondary",
                    use_container_width=True,
                    on_click=lambda key=selected_key: st.session_state.__setitem__(
                        key, not st.session_state.get(key, False)),
                )
            with col_emotion:
                emotion = st.selectbox(
                    "语气", list(EMOTION_TAGS), format_func=EMOTION_TAGS.get,
                    key=emotion_key, label_visibility="collapsed",
                )
            with col_voice:
                voice = st.selectbox(
                    "音色", list(VOICES), format_func=VOICES.get,
                    key=voice_key, label_visibility="collapsed",
                )
            if preview_key not in st.session_state:
                st.session_state[preview_key] = voice
            elif st.session_state[preview_key] != voice:
                st.session_state[preview_key] = voice
                suffix = voice.removeprefix(TTS_MODEL + "-")
                preview_path = Path(__file__).resolve().parents[2] / "试听" / f"{suffix}.wav"
                if preview_path.is_file():
                    st.audio(str(preview_path), format="audio/wav", autoplay=True)
                else:
                    st.caption("此音色暂时没有本地试听文件。")
            if st.session_state.get(selected_key, False):
                selected.append({"quote": quote.strip(), "emotion": emotion, "voice": voice})
        error = st.session_state.get("page2_tts_error")
        if error:
            st.error(str(error))
        generate_col, cancel_col = st.columns(2)
        if generate_col.button("生成", type="primary", disabled=not selected,
                               use_container_width=True):
            if _start_turn_tts(turn, selected):
                st.session_state["page2_tts_dialog_turn_id"] = ""
                st.rerun()
        if cancel_col.button("取消", use_container_width=True):
            st.session_state["page2_tts_dialog_turn_id"] = ""
            st.rerun()

    _dialog()


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
        self.payload = deepcopy(payload or {})
        self.scope_id = str(scope_id or "")
        self.meta: dict = {}
        self._fn = None
        self.result = None
        self.error: Exception | None = None
        self._started = False
        self._start_lock = threading.Lock()
        self._done = threading.Event()
        self.image_cancel = threading.Event()
        self._progress_lock = threading.Lock()
        self._image_attempt = 0

    def set_image_attempt(self, attempt: int, total: int) -> None:
        with self._progress_lock:
            self._image_attempt = min(max(1, int(attempt)), max(1, int(total)))

    def image_attempt(self) -> int:
        with self._progress_lock:
            return self._image_attempt

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


def _operation_lane(task: _Page2OperationTask) -> str:
    return task.kind if task.kind in {"image", "video"} else "chat"


def _active_operations() -> list[_Page2OperationTask]:
    # Keep the existing slot for chat and for an operation already running when
    # this version is loaded. New image/video tasks use their own slots.
    active = st.session_state.get("page2_active_operation")
    return ([active] if active is not None else []) + list(
        (st.session_state.get("page2_active_media_operations") or {}).values()
    )


def _pending_operations() -> list[_Page2OperationTask]:
    return _active_operations() + list(st.session_state.get("page2_operation_queue") or [])


def page2_operation_queue_busy() -> bool:
    return bool(_pending_operations())


def _operation_kind_busy(kind: str) -> bool:
    """Return whether this kind of request is already running or queued."""
    kinds = {"chat", "resend"} if kind == "chat" else {kind}
    return any(task.kind in kinds for task in _pending_operations())


def _one_click_video_busy() -> bool:
    return any(task.payload.get("workflow_id") for task in _pending_operations())


def _media_button_task(action: str) -> _Page2OperationTask | None:
    """Lock a logical button, including queued dependencies and changing video labels."""
    for task in reversed(_pending_operations()):
        owner = task.payload.get("media_button") or (
            "workflow" if task.payload.get("workflow_id") else
            "video" if task.kind == "video_prompt" else task.kind
        )
        if owner == action:
            return task
    return None


def _enqueue_media_operation(kind: str, label: str, payload: dict, *, action: str):
    existing = _media_button_task(action)
    if existing is not None:
        return existing
    return _enqueue_operation(kind, label, {**payload, "media_button": action}, allow_same_kind=True)


def _story_brain_update_busy() -> bool:
    """队列执行时锁定 Story Brain 等依赖当前记录的操作。"""
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
    kinds = {"chat", "resend"} if kind == "chat" else {kind}
    matches = [task for task in _pending_operations() if task.kind in kinds]
    return matches[-1] if matches else None


def _chat_edit_widget_key(turn_id: str, role: str) -> str:
    return f"page2_chat_edit_text_{role}_{turn_id}"


def _chat_edit_target() -> dict:
    target = st.session_state.get("page2_chat_edit_target")
    return target if isinstance(target, dict) else {}


def _chat_edit_active() -> bool:
    target = _chat_edit_target()
    return bool(target.get("turn_id") and target.get("role") in {"user", "assistant"})


def _open_chat_edit(turn_id: str, role: str) -> bool:
    """Open one native message editor while background work continues."""
    if role not in {"user", "assistant"} or not turn_id:
        return False

    st.session_state["page2_chat_edit_target"] = {
        "turn_id": str(turn_id),
        "role": str(role),
    }
    st.session_state.pop(_chat_edit_widget_key(turn_id, role), None)
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
    selected_prompt = system_prompts.refresh_selected_prompt(
        hidden_space=bool(st.session_state.get("hidden_unlocked", False))
    )
    st.session_state.page2_record_id = ""
    st.session_state.page2_loaded_record_id = ""
    st.session_state.page2_final_setting_id = str(
        selected_prompt.id
        if selected_prompt is not None
        else settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or ""
    )
    st.session_state.page2_turns = []
    st.session_state.page2_generated_media = []
    st.session_state.page2_selected_media_id = ""
    st.session_state.page2_pending_media_record_select = ""
    url_favorites.clear_preview_url("page2")
    st.session_state.page2_image_prompt = ""
    st.session_state.page2_image_prompt_mode = _saved_image_prompt_mode()
    st.session_state.page2_image_prompt_subject = ""
    st.session_state.page2_image_prompt_source = {}
    st.session_state.page2_refiner_prompt = ""
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
    st.session_state["page2_auto_image_next_turn"] = None
    st.session_state["page2_auto_image_anchor_turn_count"] = 0
    st.session_state["page2_story_brain_checkpoints"] = []
    st.session_state["page2_operation_queue"] = []
    st.session_state["page2_active_operation"] = None
    st.session_state["page2_active_media_operations"] = {}
    st.session_state["page2_media_completions"] = []
    st.session_state["page2_operation_scope_id"] = uuid.uuid4().hex
    st.session_state["page2_operation_notice"] = ""
    st.session_state["page2_operation_error"] = ""
    st.session_state["page2_operation_outputs"] = {}
    _close_chat_edit()
    st.session_state.pop("page2_chat_input", None)
    return True


def _current_conversation_has_content() -> bool:
    return bool(
        st.session_state.page2_turns
        or st.session_state.page2_generated_media
        or str(st.session_state.get("page2_story_brain_short", "") or "").strip()
        or st.session_state.get("page2_story_brain_archive")
    )


def branch_or_start_new_conversation(
    *, source_title: str | None = None, branch_title: str | None = None
):
    """Branch a non-empty conversation; preserve the old empty-chat behavior."""
    _ensure_state()
    if page2_operation_queue_busy():
        st.session_state["page2_operation_error"] = "队列执行中，请等待所有操作完成后再新建对话。"
        return False

    if not _current_conversation_has_content():
        return start_new_conversation()

    try:
        _upsert_record()
        titles = {}
        if source_title is not None:
            titles["source_title"] = source_title
        if branch_title is not None:
            titles["branch_title"] = branch_title
        branch = chat_records.create_branch(
            st.session_state.page2_record_id,
            scope=_chat_scope(),
            **titles,
        )
    except Exception as exc:
        st.session_state["page2_operation_error"] = (
            "创建会话分支失败：" + user_facing_error_message(exc)
        )
        return False

    st.session_state.page2_record_id = branch.id
    st.session_state.page2_loaded_record_id = branch.id
    st.session_state["page2_operation_scope_id"] = "record:" + str(branch.id)
    st.session_state["page2_media_completions"] = []
    st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex
    st.session_state["page2_operation_notice"] = f"已创建会话分支 {branch.branch_number}。"
    st.session_state["page2_operation_error"] = ""
    _close_chat_edit()
    return True


def _new_conversation_dialog() -> None:
    """Let the user name both snapshots before creating the next branch."""
    try:
        if not st.session_state.page2_record_id:
            _upsert_record()
        current_title, source_default, branch_default = chat_records.branch_title_defaults(
            st.session_state.page2_record_id, scope=_chat_scope()
        )
    except Exception as exc:
        st.session_state["page2_operation_error"] = (
            "准备新建对话失败：" + user_facing_error_message(exc)
        )
        return

    st.session_state["page2_new_source_title"] = current_title
    st.session_state["page2_new_branch_title"] = branch_default

    @st.dialog("新建对话")
    def editor():
        with st.form("page2_new_conversation_form"):
            source_col, branch_col = st.columns(2)
            with source_col:
                source_title = st.text_input("当前对话名称", key="page2_new_source_title")
            with branch_col:
                branch_title = st.text_input("新对话名称", key="page2_new_branch_title")
            st.caption("点击取消会忽略输入的名称，并按默认编号创建新对话。")
            confirm_col, cancel_col = st.columns(2)
            with confirm_col:
                confirmed = st.form_submit_button("确认", type="primary", use_container_width=True)
            with cancel_col:
                cancelled = st.form_submit_button("取消", use_container_width=True)

        if confirmed or cancelled:
            if confirmed and (not source_title.strip() or not branch_title.strip()):
                st.error("两个对话名称都不能为空。")
                return
            chosen_source = source_title if confirmed else source_default
            chosen_branch = branch_title if confirmed else branch_default
            if branch_or_start_new_conversation(
                source_title=chosen_source, branch_title=chosen_branch
            ):
                goto("main", push_history=False)
            else:
                st.error(st.session_state.get("page2_operation_error", "新建对话失败。"))

    editor()


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
    st.session_state["page2_media_completions"] = []
    st.session_state.pop("page2_video_source", None)
    st.session_state.page2_turns = list(record.turns or [])
    # Direct/deep-linked record loads should also reveal the newest message.
    st.session_state["page2_chat_scroll_to_bottom_request"] = uuid.uuid4().hex
    st.session_state.page2_generated_media = list(record.generated_images or [])
    st.session_state.page2_image_prompt = ""
    st.session_state.page2_image_prompt_source = {}
    st.session_state.page2_refiner_prompt = ""
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
    selected_prompt = system_prompts.refresh_selected_prompt(
        hidden_space=bool(st.session_state.get("hidden_unlocked", False)),
        record_id=final_setting_id,
    )
    if selected_prompt is not None:
        final_setting_id = selected_prompt.id
    st.session_state.page2_final_setting_id = final_setting_id
    st.session_state["page2_story_brain_next_update_turn"] = getattr(
        record,
        "story_brain_next_update_turn",
        None,
    )
    st.session_state["page2_story_brain_last_update_turn_id"] = str(
        getattr(record, "story_brain_last_update_turn_id", "") or ""
    )
    auto_image_next_turn = getattr(record, "auto_image_next_turn", None)
    completed_auto_image_turns = _completed_auto_image_turn_count(
        list(st.session_state.page2_turns)
    )
    auto_image_ctx = page2_service.load_context_from_settings()
    if auto_image_next_turn is None:
        if auto_image_ctx.auto_image_enabled:
            auto_image_next_turn = completed_auto_image_turns + auto_image_ctx.auto_image_interval
    auto_image_anchor = getattr(record, "auto_image_anchor_turn_count", None)
    if auto_image_anchor is None:
        auto_image_anchor = (
            min(
                completed_auto_image_turns,
                max(0, int(auto_image_next_turn) - auto_image_ctx.auto_image_interval),
            )
            if auto_image_next_turn is not None
            else completed_auto_image_turns
        )
        # Older records can have a stale target after an undo. Repair it when
        # the inferred last-image position is beyond the remaining chat turns.
        if auto_image_next_turn is not None and (
            int(auto_image_next_turn) - auto_image_ctx.auto_image_interval
            > completed_auto_image_turns
        ):
            auto_image_next_turn = auto_image_anchor + auto_image_ctx.auto_image_interval
    st.session_state["page2_auto_image_next_turn"] = auto_image_next_turn
    st.session_state["page2_auto_image_anchor_turn_count"] = auto_image_anchor
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
    return page2_service.strip_dice_decision_prefix(text)


def _apply_chat_edit(edit: dict) -> bool:
    """更新对应气泡文本；清空保存 = 删除该条消息。"""
    turn_id = str(edit.get("turn_id", "") or "")
    role = str(edit.get("role", "") or "")
    text = str(edit.get("text", "") or "")
    if not turn_id or role not in ("user", "assistant"):
        return False

    turns = list(st.session_state.get("page2_turns") or [])
    target = next((turn for turn in turns if turn.id == turn_id), None)
    if target is None or target.is_loading:
        return False

    if role == "user":
        if str(target.user_message or "") == text:
            return False
        target.user_message = text
    else:
        if str(target.assistant_message or "") == text:
            return False
        target.assistant_message = text or None
        target.assistant_audio_filename = ""
        target.assistant_audio_clips = []
        failed = st.session_state.get("page2_tts_failed") or {}
        if str(failed.get("turn_id", "")) == str(target.id):
            st.session_state.pop("page2_tts_failed", None)

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
    tts_voice: str = "",
    has_dashscope_key: bool = False,
) -> None:
    """Render one message with a native, server-backed editor."""
    target = _chat_edit_target()
    is_editing = (
        str(target.get("turn_id", "") or "") == str(turn.id)
        and str(target.get("role", "") or "") == role
    )
    edit_disabled = bool(turn.is_loading)

    with st.chat_message(role):
        if is_editing:
            editor_key = _chat_edit_widget_key(turn.id, role)
            # Include the original in every render, even if the first update is missed.
            next_text = st.text_area(
                "编辑用户消息" if role == "user" else "编辑 Assistant 消息",
                value=str(text or ""),
                key=editor_key,
                height=180,
                disabled=edit_disabled,
                label_visibility="collapsed",
            )
            save_col, delete_col, cancel_col = st.columns([1, 1, 1])
            save_clicked = save_col.button(
                "保存",
                key=f"page2_chat_edit_save_{role}_{turn.id}",
                type="primary",
                use_container_width=True,
                disabled=edit_disabled,
            )
            delete_clicked = delete_col.button(
                "删除",
                key=f"page2_chat_edit_delete_{role}_{turn.id}",
                use_container_width=True,
                disabled=edit_disabled,
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
            if role == "assistant":
                _render_turn_tts_status(turn)
        with edit_col:
            edit_clicked = st.button(
                "",
                key=f"page2_chat_edit_open_{role}_{turn.id}",
                help="编辑该条消息",
                icon=":material/edit:",
                type="tertiary",
                disabled=edit_disabled,
            )
            tts_clicked = False
            if role == "assistant":
                active = st.session_state.get("page2_tts_active") or {}
                tts_disabled = (
                    story_brain_update_busy
                    or not has_dashscope_key
                    or bool(active)
                )
                has_audio = _turn_has_audio(turn)
                tts_clicked = st.button(
                    "",
                    key=f"page2_tts_generate_{turn.id}",
                    help="选择台词生成语音" if not has_audio else "继续添加语音",
                    icon=":material/volume_up:",
                    type="tertiary",
                    disabled=tts_disabled,
                )
        if edit_clicked and _open_chat_edit(turn.id, role):
            st.rerun()
        if role == "assistant" and tts_clicked:
            st.session_state["page2_tts_dialog_turn_id"] = str(turn.id)
            st.session_state["page2_tts_dialog_nonce"] = uuid.uuid4().hex
            st.session_state.pop("page2_tts_error", None)
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
        auto_image_next_turn=st.session_state.get("page2_auto_image_next_turn"),
        auto_image_anchor_turn_count=st.session_state.get(
            "page2_auto_image_anchor_turn_count"
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
    return render_internal_prompt("chat", "dice_result", point=page2_service.roll_point())


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


def _rewind_auto_image_schedule(turns: list, remaining_turns: list) -> None:
    """Remove an undone counted turn from the current image interval."""
    before_count = _completed_auto_image_turn_count(turns)
    after_count = _completed_auto_image_turn_count(remaining_turns)
    next_turn = st.session_state.get("page2_auto_image_next_turn")
    if before_count == after_count or next_turn is None:
        return

    interval = page2_service.load_context_from_settings().auto_image_interval
    anchor = st.session_state.get("page2_auto_image_anchor_turn_count")
    if anchor is None:
        # Existing records only stored the next absolute turn number.
        anchor = min(before_count, max(0, int(next_turn) - interval))
        retry_pending = False
    else:
        anchor = max(0, int(anchor))
        retry_pending = int(next_turn) != anchor + interval

    if before_count <= anchor:
        anchor = max(0, anchor - (before_count - after_count))
    st.session_state["page2_auto_image_anchor_turn_count"] = anchor
    st.session_state["page2_auto_image_next_turn"] = (
        after_count + 1 if retry_pending else anchor + interval
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
    before_auto_image_schedule = (
        st.session_state.get("page2_auto_image_next_turn"),
        st.session_state.get("page2_auto_image_anchor_turn_count"),
    )
    st.session_state.page2_turns = turns[:-1]
    _set_memory_state(memory, checkpoints)
    try:
        _rewind_auto_image_schedule(turns, turns[:-1])
        _upsert_record()
    except Exception:
        st.session_state.page2_turns = turns
        _set_memory_state(before_memory, before_checkpoints)
        (
            st.session_state["page2_auto_image_next_turn"],
            st.session_state["page2_auto_image_anchor_turn_count"],
        ) = before_auto_image_schedule
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


def _completed_auto_image_turn_count(turns: list[Page2ConversationTurn]) -> int:
    return sum(
        1 for turn in turns
        if not turn.is_context_excluded
        and not turn.is_auto_dice_followup
        and str(turn.user_message or "").strip()
        and str(turn.assistant_message or "").strip()
        and not turn.is_loading
        and not str(turn.assistant_message).startswith("请求失败，请稍后重试。")
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
    @st.dialog("System Prompt", width="large", dismissible=False)
    def _dialog():
        prompt_draft_guard.protect(
            "page2_system_prompt_dialog_body", ctx.system_prompt, "System Prompt"
        )
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
        prompt_draft_guard.save_action("page2_system_prompt_save", "page2_system_prompt_dialog_body")
        with save_col:
            if st.button("保存", type="primary", use_container_width=True, key="page2_system_prompt_save"):
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
                prompt_draft_guard.saved("page2_system_prompt_dialog_body")
                st.session_state["page2_system_prompt_notice"] = "System Prompt 已保存。"
                st.rerun()
        with cancel_col:
            if st.button("放弃改动并取消", use_container_width=True):
                st.rerun()

    _dialog()


def _apply_sidebar_context_values(
    ctx,
    *,
    context_turn_count,
    selected_chat_model,
    chat_reasoning,
    story_brain_update_model,
    story_brain_reasoning,
    story_brain_turns,
    selected_tts_voice,
    image_poll_attempts=None,
    temperature,
    unexpected_event_enabled,
    unexpected_event_threshold,
    auto_image_enabled=None,
    auto_image_interval=None,
) -> bool:
    """Copy sidebar widget values into ctx and report whether they changed."""
    previous = (
        ctx.context_turn_count,
        ctx.selected_chat_model,
        ctx.chat_reasoning_enabled,
        ctx.story_brain_update_model,
        ctx.story_brain_reasoning_enabled,
        ctx.story_brain_turns,
        ctx.selected_tts_voice,
        ctx.image_poll_attempts,
        ctx.temperature,
        ctx.unexpected_event_enabled,
        ctx.unexpected_event_threshold,
        ctx.auto_image_enabled,
        ctx.auto_image_interval,
    )

    ctx.context_turn_count = int(context_turn_count)
    ctx.selected_chat_model = selected_chat_model
    ctx.chat_reasoning_enabled = (
        chat_model_supports_reasoning(selected_chat_model)
        and chat_reasoning == "开思考"
    )
    ctx.story_brain_update_model = story_brain_update_model
    ctx.story_brain_reasoning_enabled = (
        chat_model_supports_reasoning(story_brain_update_model)
        and story_brain_reasoning == "开思考"
    )
    ctx.story_brain_turns = int(story_brain_turns)
    ctx.selected_tts_voice = selected_tts_voice if selected_tts_voice in VOICES else next(iter(VOICES))
    if image_poll_attempts is not None:
        ctx.image_poll_attempts = min(30, max(1, int(image_poll_attempts)))
    ctx.temperature = float(temperature)
    ctx.unexpected_event_enabled = bool(unexpected_event_enabled)
    ctx.unexpected_event_threshold = int(unexpected_event_threshold)
    if auto_image_enabled is not None:
        ctx.auto_image_enabled = bool(auto_image_enabled)
    if auto_image_interval is not None:
        ctx.auto_image_interval = int(auto_image_interval)

    current = (
        ctx.context_turn_count,
        ctx.selected_chat_model,
        ctx.chat_reasoning_enabled,
        ctx.story_brain_update_model,
        ctx.story_brain_reasoning_enabled,
        ctx.story_brain_turns,
        ctx.selected_tts_voice,
        ctx.image_poll_attempts,
        ctx.temperature,
        ctx.unexpected_event_enabled,
        ctx.unexpected_event_threshold,
        ctx.auto_image_enabled,
        ctx.auto_image_interval,
    )
    return current != previous


def render_sidebar_context():
    _ensure_state()
    _load_record_from_nav_if_needed()

    with st.sidebar:
        if st.button(
            "新建对话",
            use_container_width=True,
            disabled=_story_brain_update_busy(),
        ):
            if _current_conversation_has_content():
                _new_conversation_dialog()
            elif branch_or_start_new_conversation():
                goto("main", push_history=False)

        if page2_operation_queue_busy():
            active = _active_operations()
            waiting = len(list(st.session_state.get("page2_operation_queue") or []))
            if active:
                labels = "、".join(task.label for task in active)
                st.info(f"正在执行：{labels}\n\n等待中：{waiting} 项")
            else:
                st.info(f"队列等待启动：{waiting} 项")

        st.subheader("上下文设置")
        ctx = page2_service.load_context_from_settings()
        previous_auto_image = (ctx.auto_image_enabled, ctx.auto_image_interval)

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
            chat_reasoning_supported = chat_model_supports_reasoning(
                selected_chat_model
            )
            if not chat_reasoning_supported:
                st.session_state.pop("page2_sidebar_chat_reasoning", None)
            chat_reasoning = st.selectbox(
                "聊天模型思考",
                options=["开思考", "关思考"],
                index=(
                    0
                    if ctx.chat_reasoning_enabled and chat_reasoning_supported
                    else 1
                ),
                key="page2_sidebar_chat_reasoning",
                label_visibility="collapsed",
                disabled=not chat_reasoning_supported,
            )
            if not chat_reasoning_supported:
                st.caption("Mistral Large 3 2512 不提供可配置思考模式。")
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
            story_brain_reasoning_supported = chat_model_supports_reasoning(
                story_brain_update_model
            )
            if not story_brain_reasoning_supported:
                st.session_state.pop("page2_sidebar_story_brain_reasoning", None)
            story_brain_reasoning = st.selectbox(
                "Story Brain 思考",
                options=["开思考", "关思考"],
                index=(
                    0
                    if ctx.story_brain_reasoning_enabled
                    and story_brain_reasoning_supported
                    else 1
                ),
                key="page2_sidebar_story_brain_reasoning",
                label_visibility="collapsed",
                disabled=not story_brain_reasoning_supported,
            )
            if not story_brain_reasoning_supported:
                st.caption("Mistral Large 3 2512 不提供可配置思考模式。")
            story_brain_turns = st.number_input(
                "Story Brain 更新间隔",
                min_value=1,
                step=1,
                value=int(ctx.story_brain_turns),
                key="page2_sidebar_story_brain_turns",
            )
            tts_voice_options = list(VOICES)
            if st.session_state.get("page2_sidebar_tts_voice") not in tts_voice_options:
                st.session_state.pop("page2_sidebar_tts_voice", None)
            selected_tts_voice = st.selectbox(
                "音色",
                options=tts_voice_options,
                index=tts_voice_options.index(ctx.selected_tts_voice)
                if ctx.selected_tts_voice in tts_voice_options
                else 0,
                format_func=VOICES.get,
                key="page2_sidebar_tts_voice",
                help="聊天消息语音朗读使用的音色，与「生成语音」页面独立。",
            )
            image_poll_attempts = st.slider(
                "生图轮询次数",
                min_value=1,
                max_value=30,
                value=int(ctx.image_poll_attempts),
                key="page2_sidebar_image_poll_attempts",
                help="生图最多尝试的次数；成功后立即结束。",
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
            )
            manual_update_help = (
                "随时可用：使用上次成功更新后的全部新问答更新 Story Brain；"
                "若暂无新问答，则重复更新最近一次完整问答。成功后下次更新顺延 5 轮。"
            )
            if st.button(
                "更新storybrain",
                key="page2_sidebar_manual_story_brain_update_btn",
                use_container_width=True,
                disabled=manual_update_disabled,
                help=manual_update_help,
            ):
                ctx.story_brain_update_model = story_brain_update_model
                ctx.story_brain_reasoning_enabled = (
                    story_brain_reasoning_supported
                    and story_brain_reasoning == "开思考"
                )
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
                st.caption("暂无新问答，点击将重复更新最近一次完整问答。")
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

        auto_image_enabled = st.toggle(
            "自动生图",
            value=bool(ctx.auto_image_enabled),
            key="page2_sidebar_auto_image_enabled",
        )
        auto_image_interval = int(ctx.auto_image_interval)
        if auto_image_enabled:
            auto_image_interval = st.slider(
                "每隔几轮生成一张图片",
                min_value=1,
                max_value=12,
                value=int(ctx.auto_image_interval),
                step=1,
                key="page2_sidebar_auto_image_interval",
            )
            st.caption("只统计保留的用户完整问答；撤回的轮次和自动掷骰不计入。失败后下一轮重试。你主动生图后，自动生图重新计时。")

        context_changed = _apply_sidebar_context_values(
            ctx,
            context_turn_count=context_turn_count,
            selected_chat_model=selected_chat_model,
            chat_reasoning=chat_reasoning,
            story_brain_update_model=story_brain_update_model,
            story_brain_reasoning=story_brain_reasoning,
            story_brain_turns=story_brain_turns,
            selected_tts_voice=selected_tts_voice,
            image_poll_attempts=image_poll_attempts,
            temperature=temperature,
            unexpected_event_enabled=unexpected_event_enabled,
            unexpected_event_threshold=unexpected_event_threshold,
            auto_image_enabled=auto_image_enabled,
            auto_image_interval=auto_image_interval,
        )
        if previous_auto_image != (ctx.auto_image_enabled, ctx.auto_image_interval):
            st.session_state["page2_auto_image_anchor_turn_count"] = (
                _completed_auto_image_turn_count(list(st.session_state.page2_turns))
                if ctx.auto_image_enabled else None
            )
            st.session_state["page2_auto_image_next_turn"] = (
                st.session_state["page2_auto_image_anchor_turn_count"] + ctx.auto_image_interval
                if ctx.auto_image_enabled else None
            )
        if context_changed:
            try:
                page2_service.save_context_to_settings(ctx)
            except Exception as exc:
                st.error("设置自动保存失败：" + user_facing_error_message(exc))
            else:
                st.success("设置已自动保存")
        else:
            st.caption("修改后自动保存")

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
    if _chat_edit_active():
        st.session_state["page2_operation_error"] = "消息编辑中，请完成后再重发。"
        return False
    turns = list(st.session_state.page2_turns)
    waiting_for_message = _operation_kind_busy("chat")
    if (
        (not turns or not str(turns[-1].user_message or "").strip())
        and not waiting_for_message
    ):
        st.session_state["page2_operation_error"] = "没有可重发的上一轮用户消息。"
        return False
    if turns and turns[-1].is_loading and not waiting_for_message:
        st.session_state["page2_operation_error"] = "上一轮尚未结束，请等待完成后再重发。"
        return False
    _enqueue_operation("resend", "重发上一轮（成功后替换）", {
        "ctx": ctx,
        "story_brain_enabled": story_brain_enabled,
        # If an earlier chat/resend is queued, resolve the latest turn only when
        # this task reaches the front so queue order remains authoritative.
        "target_turn_id": "" if waiting_for_message else turns[-1].id,
    }, allow_same_kind=True)
    return True


def _prepare_resend_operation(task: _Page2OperationTask):
    turns = deepcopy(list(st.session_state.page2_turns))
    target_turn_id = str(task.payload.get("target_turn_id", "") or "")
    if not turns or (target_turn_id and turns[-1].id != target_turn_id):
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
        is_auto_dice_followup=original.is_auto_dice_followup,
    )
    task.meta.update({
        "ctx": ctx, "turn_id": replacement.id, "replacement": replacement,
        "original_turns": [turn.to_dict() for turn in turns], "remaining_turns": remaining,
        "rewound_memory": memory, "rewound_checkpoints": checkpoints, "memory_notice": notice,
        "story_brain_enabled": bool(task.payload.get("story_brain_enabled", True)),
        "is_auto_dice_followup": original.is_auto_dice_followup,
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
        is_auto_dice_followup=bool(task.payload.get("is_auto_dice_followup", False)),
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


def _queue_chat_message(
    *, ctx, user_text: str, hide_user_message: bool, story_brain_enabled: bool
) -> _Page2OperationTask:
    return _enqueue_operation(
        "chat",
        "发送消息",
        {
            "ctx": ctx,
            "user_text": str(user_text or ""),
            "hide_user_message": bool(hide_user_message),
            "story_brain_enabled": bool(story_brain_enabled),
        },
        allow_same_kind=True,
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
        # 允许随时手动更新：同一轮重复点击时，回退到最近一个完整问答重复更新。
        update_turns = _completed_story_brain_turns(turns_snapshot)[-1:]
    if not update_turns:
        raise ValueError("暂无可用于更新 Story Brain 的完整问答。")
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
    turns_snapshot = deepcopy(list(st.session_state.page2_turns))
    source_turn_id = str(task.payload.get("source_turn_id", "") or "")
    if source_turn_id:
        source_index = next(
            (index for index, turn in enumerate(turns_snapshot) if turn.id == source_turn_id),
            None,
        )
        if source_index is None:
            raise ValueError("自动生图对应的故事轮次已不存在。")
        turns_snapshot = turns_snapshot[:source_index + 1]
    task.meta["image_prompt_source"] = latest_image_prompt_source(turns_snapshot)
    task.meta["previous_image_prompt"] = str(st.session_state.get("page2_image_prompt") or "")
    story_brain_short = _current_short_story_brain()
    story_brain_enabled = bool(
        st.session_state.get("page2_story_brain_enabled", True)
    )
    mode = str(task.payload.get("mode", "first_person") or "first_person")
    subject = str(task.payload.get("subject", "") or "")
    return lambda: page2_service.generate_image_prompt(
        ctx=ctx,
        turns=turns_snapshot,
        story_brain_short=story_brain_short,
        story_brain_enabled=story_brain_enabled,
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
    refiner_prompt = str(task.payload.get("refiner_prompt", "") or "")
    quality = task.payload.get("quality", IMAGE_DEFAULT_QUALITY)
    max_attempts = min(30, max(1, int(task.payload.get("max_attempts", 3))))
    def generate():
        if task.image_cancel.is_set():
            raise ImagePollingStopped("已停止轮询")
        record = page2_service.generate_image(
            provider=provider,
            prompt=prompt,
            image_urls=image_urls,
            aspect_ratio=aspect_ratio,
            refiner_prompt=refiner_prompt,
            allow_temporary_upload=bool(task.payload.get("allow_temporary_upload", False)),
            max_attempts=max_attempts,
            progress_callback=task.set_image_attempt,
            cancel_event=task.image_cancel,
            **({"quality": quality} if provider in IMAGE_QUALITY_PROVIDERS else {}),
        )
        if task.image_cancel.is_set():
            raise ImagePollingStopped("已停止轮询")
        return media_store.cache_generated(record)

    return generate


def _video_operation_source(task: _Page2OperationTask):
    image_operation_id = str(task.payload.get("image_operation_id", "") or "")
    if image_operation_id:
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        source_id = str(outputs.get(image_operation_id, "") or "")
        if not source_id:
            raise RuntimeError("前置的生图任务未成功，无法继续生成视频。")
    else:
        source_id = str(task.payload.get("source_id", "") or "")
    source = _find_video_source(source_id)
    if source is None or not media_store.available(source):
        raise ValueError("队列执行到生成视频时，没有找到可用的输入图片。")
    return source


def _prepare_video_prompt_operation(task: _Page2OperationTask):
    source = _video_operation_source(task)
    task.meta["source_id"] = source.id
    task.meta["previous_video_prompt"] = source.video_prompt
    try:
        story_text = _video_story_for_source(source)
    except MissingVideoStorySource:
        st.session_state.page2_video_story_toast = source.id
        raise
    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    image_prompt = source.prompt
    return lambda: page2_service.generate_video_prompt(
        ctx=ctx, image_prompt=image_prompt, story_text=story_text,
    )


def _prepare_video_operation(task: _Page2OperationTask):
    ctx = task.payload.get("ctx") or page2_service.load_context_from_settings()
    prompt = str(task.payload.get("prompt", "") or "")
    seconds = task.payload.get("seconds", 5)
    generate_audio = bool(task.payload.get("generate_audio", True))
    resolution = task.payload.get("resolution")
    if task.payload.get("text_only"):
        return lambda: media_store.cache_generated(page2_service.generate_video_from_text(
            ctx=ctx, prompt=prompt, seconds=seconds,
            aspect_ratio=str(task.payload.get("aspect_ratio") or "16:9"),
            resolution=resolution or WAN3_RESOLUTIONS[0],
            generate_audio=generate_audio,
        ))

    source = deepcopy(_video_operation_source(task))
    prompt_operation_id = str(task.payload.get("video_prompt_operation_id", "") or "")
    if prompt_operation_id:
        outputs = st.session_state.get("page2_operation_outputs") or {}
        prompt = str(outputs.get(prompt_operation_id, "") or "")
        if not prompt.strip():
            raise RuntimeError("前置的视频 prompt 任务未成功，无法继续生成视频。")
        if outputs.get(prompt_operation_id + ":source") != source.id:
            raise RuntimeError("视频 prompt 与输入图片不匹配，已停止生成视频。")
    return lambda: media_store.cache_generated(page2_service.generate_video_from_image(
        ctx=ctx,
        source_record=source,
        prompt=prompt,
        seconds=seconds,
        generate_audio=generate_audio,
        resolution=resolution,
        allow_temporary_upload=bool(task.payload.get("allow_temporary_upload", False)),
    ))


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
    auto_roll_queued = (
        task.error is None
        and not is_context_excluded
        and not bool(task.meta.get("is_auto_dice_followup", False))
        and page2_service.should_auto_roll_dice(reply)
    )
    if auto_roll_queued:
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
    if task.error is None and not is_context_excluded and not auto_roll_queued:
        _schedule_auto_image(
            ctx=page2_service.load_context_from_settings(), source_turn_id=turn_id,
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
        prompt = str(task.result or "")
        source = dict(task.meta.get("image_prompt_source") or {})
        if str(st.session_state.get("page2_image_prompt") or "") == task.meta.get(
            "previous_image_prompt", str(st.session_state.get("page2_image_prompt") or "")
        ):
            st.session_state.page2_image_prompt = prompt
            st.session_state.page2_image_prompt_source = source
        else:
            task.meta["completion_notice"] = "；已保留你修改的图片 prompt，后续生图使用本次生成结果。"
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        outputs[task.id] = prompt
        outputs[task.id + ":source"] = source
        st.session_state["page2_operation_outputs"] = outputs
        return
    if task.kind == "video_prompt":
        source = _find_video_source(task.meta.get("source_id", ""))
        if source is None:
            raise ValueError("输入图片已不存在，无法保存视频 prompt。")
        prompt = str(task.result or "")
        if not prompt.strip():
            raise ValueError("模型没有返回可用的视频 prompt，请重试。")
        if source.video_prompt != task.meta.get("previous_video_prompt", ""):
            raise ValueError("视频 prompt 已被修改，未覆盖你的编辑，请重新生成。")
        _save_image_video_field(source.id, "video_prompt", prompt)
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        outputs[task.id] = prompt
        outputs[task.id + ":source"] = source.id
        st.session_state["page2_operation_outputs"] = outputs
        if task.payload.get("workflow_id"):
            if not _manual_reference_ids():
                st.session_state["page2_pending_video_source"] = source.id
            task.meta["completion_notice"] = "；视频 prompt 已就绪，可编辑后点击“生视频”。"
        return
    if task.kind in {"image", "video"}:
        if task.meta.get("media_applied"):
            return
        record = task.result
        if task.kind == "image":
            source = dict(task.meta.get("image_prompt_source") or {})
            record.source_turn_id = str(source.get("source_turn_id") or "")
            record.previous_source_turn_id = str(source.get("previous_source_turn_id") or "")
            record.video_prompt = str(task.payload.get("pending_video_prompt") or "")
        previous_media = list(st.session_state.page2_generated_media)
        st.session_state.page2_generated_media = previous_media + [record]
        try:
            _upsert_record()
        except Exception:
            st.session_state.page2_generated_media = previous_media
            raise
        outputs = dict(st.session_state.get("page2_operation_outputs") or {})
        outputs[task.id] = record.id
        st.session_state["page2_operation_outputs"] = outputs
        if task.kind == "image":
            try:
                media_store.remember_image(record)
            except OSError as exc:
                media_store.debug_log("[ImageLibrary] metadata update failed", type(exc).__name__)
            st.session_state[_reference_key() + "_latest_generated"] = record.id
            if task.payload.get("workflow_id"):
                _consume_workflow_references(task)
            if not _manual_reference_ids():
                st.session_state["page2_pending_video_source"] = record.id
            # The queue status fragment reruns the page after applying this task.
            # Select the new image now so that rerun shows it without a View click.
            _select_media_preview(record.id)
        else:
            # 视频完成后也直接切换预览，保留当前视频输入图片的选择。
            _select_media_preview(record.id)
        # 图片和视频原来的完成提示写入方式留作恢复参考：
        # st.session_state["page2_media_completions"] = list(
        #     st.session_state.get("page2_media_completions") or []
        # ) + [{"task_id": task.id, "media_id": record.id, "kind": task.kind}]
        task.meta["media_applied"] = True
        return
    raise ValueError("未知队列操作：" + task.kind)


def _dismiss_operation_error() -> None:
    st.session_state["page2_operation_error"] = ""


def _operation_waiting_for_dependency(task: _Page2OperationTask) -> bool:
    pending_ids = {item.id for item in _pending_operations()}
    outputs = st.session_state.get("page2_operation_outputs") or {}
    return any(
        task.payload.get(key) in pending_ids and task.payload.get(key) not in outputs
        for key in ("prompt_operation_id", "image_operation_id", "video_prompt_operation_id")
    )


def _cancel_workflow_followups(task: _Page2OperationTask) -> None:
    """A failed workflow stage cancels only its own unstarted successors."""
    workflow_id = task.payload.get("workflow_id")
    if workflow_id:
        st.session_state["page2_operation_queue"] = [
            item for item in st.session_state.get("page2_operation_queue", [])
            if item.payload.get("workflow_id") != workflow_id
        ]


def _clear_active_operation(task: _Page2OperationTask) -> None:
    if st.session_state.get("page2_active_operation") is task:
        st.session_state["page2_active_operation"] = None
    media = dict(st.session_state.get("page2_active_media_operations") or {})
    st.session_state["page2_active_media_operations"] = {
        lane: item for lane, item in media.items() if item is not task
    }


def _advance_operation_queues() -> bool:
    """Three independent request lanes; all state writes happen on the script thread."""
    changed = False
    for task in _active_operations():
        if not task.is_done():
            continue
        try:
            if task.scope_id != _operation_scope_id():
                raise RuntimeError("当前记录已变更，未写入旧任务结果。")
            if task.kind == "image" and task.image_cancel.is_set():
                _cancel_workflow_followups(task)
                continue
            _apply_operation(task)
            if task.error is not None:
                raise task.error
            if task.payload.get("auto_image") and task.kind == "image":
                st.session_state["page2_auto_image_anchor_turn_count"] = int(
                    task.payload["auto_image_turn_count"]
                )
                st.session_state["page2_auto_image_next_turn"] = (
                    int(task.payload["auto_image_turn_count"])
                    + page2_service.load_context_from_settings().auto_image_interval
                )
                _upsert_record()
            elif task.kind == "image":
                _restart_auto_image_countdown()
            if task.kind in {"image", "video"}:
                st.session_state["page2_operation_notice"] = ""
            else:
                st.session_state["page2_operation_notice"] = f"已完成：{task.label}" + str(
                    task.meta.get("completion_notice") or task.meta.get("memory_notice") or ""
                )
        except Exception as exc:
            _cancel_workflow_followups(task)
            if task.payload.get("auto_image"):
                st.session_state["page2_auto_image_next_turn"] = (
                    int(task.payload["auto_image_turn_count"]) + 1
                )
                try:
                    _upsert_record()
                except Exception:
                    pass
            suffix = (
                "失败，下轮重试：" if task.payload.get("auto_image")
                else "失败，已停止本次一键生成：" if task.payload.get("workflow_id")
                else "失败，其他任务将继续："
            )
            message = f"{task.label}{suffix}" + user_facing_error_message(exc)
            previous = str(st.session_state.get("page2_operation_error") or "")
            st.session_state["page2_operation_error"] = "\n\n".join(filter(None, [previous, message]))
        finally:
            _clear_active_operation(task)
        changed = True

    occupied = {_operation_lane(task) for task in _active_operations()}
    queue = list(st.session_state.get("page2_operation_queue") or [])
    for task in list(queue):
        lane = _operation_lane(task)
        if lane in occupied or _operation_waiting_for_dependency(task):
            continue
        # Skip unresolved dependencies, preserving order among ready tasks in
        # each lane. In particular, a video prompt awaiting an image must not
        # block the chat or image prompt needed to finish that image.
        queue.remove(task)
        st.session_state["page2_operation_queue"] = list(queue)
        if lane == "chat":
            st.session_state["page2_active_operation"] = task
        else:
            media = dict(st.session_state.get("page2_active_media_operations") or {})
            media[lane] = task
            st.session_state["page2_active_media_operations"] = media
        occupied.add(lane)
        changed = True

    for task in _active_operations():
        if task.is_started():
            continue
        try:
            task.start(_prepare_operation(task))
        except Exception as exc:
            task.fail_before_start(exc)
        changed = True
    return changed


def _stop_image_polling(task: _Page2OperationTask) -> None:
    task.image_cancel.set()
    _cancel_workflow_followups(task)
    _clear_active_operation(task)
    st.session_state["page2_operation_notice"] = "已停止轮询"


@st.fragment(run_every=1)
def _render_media_operation_status() -> None:
    """在媒体预览正下方展示任务状态与错误提示，多条向下排列。"""
    active = _active_operations()
    queue = list(st.session_state.get("page2_operation_queue") or [])
    names = {"chat": "聊天 / 提示词", "image": "图片", "video": "视频"}
    for task in active:
        waiting = sum(_operation_lane(item) == _operation_lane(task) for item in queue)
        suffix = f"；还有 {waiting} 项等待" if waiting else ""
        progress = ""
        attempt = task.image_attempt() if task.kind == "image" else 0
        if attempt:
            total = min(30, max(1, int(task.payload.get("max_attempts", 3))))
            progress = f" · 生图轮询 {attempt}/{total}"
        st.info(f"{names[_operation_lane(task)]}：正在执行{task.label}{progress}{suffix}")
        if task.kind == "image":
            st.button(
                "停止轮询", key=f"page2_stop_image_polling_{task.id}",
                on_click=_stop_image_polling, args=(task,),
            )
        if task.kind == "resend" and task.meta.get("memory_notice"):
            st.warning(task.meta["memory_notice"])
    error = str(st.session_state.get("page2_operation_error", "") or "")
    messages = [item.strip() for item in error.split("\n\n") if item.strip()]
    for message in messages:
        st.error(message)
    if messages:
        st.button(
            "关闭错误提示",
            key="page2_dismiss_operation_error",
            on_click=_dismiss_operation_error,
        )


@st.fragment(run_every=1)
def _render_operation_queue_status() -> None:
    if (_advance_operation_queues()
            or st.session_state.get("page2_media_revision", media_store.revision()) != media_store.revision()):
        st.rerun()
    active = _active_operations()
    queue = list(st.session_state.get("page2_operation_queue") or [])
    error = str(st.session_state.get("page2_operation_error", "") or "")
    if not active and not queue:
        notice = str(st.session_state.pop("page2_operation_notice", "") or "")
        if notice and not error:
            st.success(notice)
        return
    for task in queue:
        if _operation_waiting_for_dependency(task):
            st.caption(f"{task.label}：等待前置任务完成")


def _render_chat_column():
    _cleanup_expired_chat_speech()
    _apply_active_tts_result()
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
    tts_voice = ctx.selected_tts_voice if ctx.selected_tts_voice in VOICES else next(iter(VOICES))
    _render_tts_quote_dialog(tts_voice)
    has_dashscope_key = _has_dashscope_key()
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
                        tts_voice=tts_voice,
                        has_dashscope_key=has_dashscope_key,
                    )
                if turn.assistant_message is not None:
                    _render_editable_chat_message(
                        turn=turn,
                        role="assistant",
                        text=turn.assistant_message or "",
                        story_brain_update_busy=story_brain_update_busy,
                        tts_voice=tts_voice,
                        has_dashscope_key=has_dashscope_key,
                    )
            if scroll_to_bottom_request:
                with st.container(key="page2_chat_scroll_trigger"):
                    st.iframe(
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
                        height=1,
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
            _render_empty_space_submit_bridge(send_disabled=chat_edit_active)
            dice_clicked = st.button(
                "",
                key="page2_chat_dice_btn",
                help="掷一次 0–24 点骰子并直接发送",
                icon=":material/casino:",
                type="tertiary",
                disabled=chat_edit_active,
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

    if user_text is None or chat_edit_active:
        return

    submitted_text = str(user_text)
    hide_user_message = submitted_text == _EMPTY_SPACE_SUBMISSION_SENTINEL
    current_text = (
        _EMPTY_SPACE_CONTINUE_MESSAGE if hide_user_message else submitted_text.strip()
    )
    if not current_text:
        return
    _queue_chat_message(
        ctx=ctx,
        user_text=current_text,
        hide_user_message=hide_user_message,
        story_brain_enabled=story_brain_enabled,
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


def _saved_image_prompt_mode() -> str:
    saved = settings.get(AppStorageKeys.PAGE2_IMAGE_PROMPT_MODE, "first_person")
    return saved if isinstance(saved, str) and saved in {"normal", "first_person", "closeup"} else "first_person"


def _render_image_prompt_mode_selector() -> str:
    st.session_state["_page2_image_prompt_mode"] = st.session_state.page2_image_prompt_mode
    mode = st.selectbox(
        "prompt 模式", options=["normal", "first_person", "closeup"],
        key="_page2_image_prompt_mode", on_change=_save_image_prompt_field,
        args=("_page2_image_prompt_mode", "page2_image_prompt_mode"),
    )
    if mode != _saved_image_prompt_mode():
        settings.set(AppStorageKeys.PAGE2_IMAGE_PROMPT_MODE, mode)
    return mode


def _last_image_provider() -> str:
    saved = str(
        settings.get(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, DEFAULT_IMAGE_PROVIDER)
        or DEFAULT_IMAGE_PROVIDER
    )
    provider = IMAGE25_PROVIDER if saved == "image2" else normalize_image_provider(saved)
    if provider not in IMAGE_PROVIDERS:
        return DEFAULT_IMAGE_PROVIDER
    if provider != saved:
        settings.set(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, provider)
    return provider


def _render_image_model_selector() -> str:
    key = "_page2_image_provider"
    scope = _operation_scope_id()
    if (st.session_state.get("_page2_image_provider_scope") != scope
            or st.session_state.get(key) not in IMAGE_PROVIDERS):
        st.session_state[key] = _last_image_provider()
        st.session_state["_page2_image_provider_scope"] = scope
    provider = st.selectbox("图片生成 provider", options=IMAGE_PROVIDERS, key=key)
    if provider != _last_image_provider():
        settings.set(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, provider)
    return provider


def _render_image_quality_selector(provider: str) -> str:
    if provider != IMAGE25_PROVIDER:
        return IMAGE_DEFAULT_QUALITY
    saved_quality = settings.get(AppStorageKeys.PAGE2_IMAGE25_QUALITY, IMAGE_DEFAULT_QUALITY)
    if saved_quality not in IMAGE25_QUALITIES:
        saved_quality = IMAGE_DEFAULT_QUALITY
    quality = st.selectbox(
        "quality", options=IMAGE25_QUALITIES,
        index=IMAGE25_QUALITIES.index(saved_quality), key="page2_image25_quality",
    )
    if quality != saved_quality:
        settings.set(AppStorageKeys.PAGE2_IMAGE25_QUALITY, quality)
    return quality


def _render_image_aspect_ratio_selector(provider: str) -> str:
    saved_ratios = settings.get(AppStorageKeys.PAGE2_IMAGE_ASPECT_RATIOS, {})
    saved_ratios = dict(saved_ratios) if isinstance(saved_ratios, dict) else {}
    # Carry the old Flux preference forward without rewriting historical images.
    migrated = provider == "flux2-dev" and "flux2-dev" not in saved_ratios and "flux" in saved_ratios
    if migrated:
        saved_ratios[provider] = saved_ratios["flux"]
    choices = image_aspect_ratios(provider)
    saved_ratio = saved_ratios.get(provider, page2_service.IMAGE_ASPECT_RATIO)
    if saved_ratio not in choices:
        saved_ratio = page2_service.IMAGE_ASPECT_RATIO
    aspect_ratio = st.selectbox(
        "图片尺寸", options=choices, index=choices.index(saved_ratio),
        format_func=image_aspect_ratio_label, key=f"page2_image_aspect_ratio_{provider}",
    )
    if migrated or aspect_ratio != saved_ratio:
        saved_ratios[provider] = aspect_ratio
        settings.set(AppStorageKeys.PAGE2_IMAGE_ASPECT_RATIOS, saved_ratios)
    return aspect_ratio


def _reference_key():
    return "page2_image_reference_ids_" + _operation_scope_id()


def _reference_images(media=None):
    images = {item.id: item for item in media_store.list_images()}
    for item in st.session_state.page2_generated_media if media is None else media:
        if item.media_kind == GeneratedMediaKind.IMAGE and media_store.available(item):
            images[item.id] = item
    st.session_state["page2_reference_catalog"] = images
    return images


def _manual_reference_ids():
    key = _reference_key()
    return list(st.session_state.get(key + "_selection", st.session_state.get(key, [])))


def _set_reference_selection(ids, *, preview=True):
    key = _reference_key()
    ids = list(dict.fromkeys(ids))
    st.session_state[key + "_selection"] = ids
    st.session_state[key + "_revision"] = st.session_state.get(key + "_revision", 0) + 1
    if ids:
        if preview:
            _select_media_preview(ids[-1])
        st.session_state["page2_pending_video_source"] = ids[-1]
    else:
        st.session_state.pop("page2_pending_video_source", None)
        if preview:
            latest = st.session_state.get(key + "_latest_generated") or next(
                (item.id for item in reversed(st.session_state.page2_generated_media)
                 if item.media_kind == GeneratedMediaKind.IMAGE and item.provider != "local"
                 and media_store.available(item)), None)
            if latest:
                _select_media_preview(latest)


def _reference_selection_changed():
    key = _reference_key()
    chosen = list(st.session_state.get(key, []))
    previous = _manual_reference_ids()
    # Preserve the order in which images were picked, even if the widget sorts options.
    ordered = [item for item in previous if item in chosen]
    ordered.extend(item for item in chosen if item not in previous)
    _set_reference_selection(ordered)


def _import_reference_uploads(upload_key):
    key = _reference_key()
    added, errors = [], []
    for file in st.session_state.get(upload_key, []) or []:
        try:
            record = media_store.import_image(file.getvalue(), file.name,
                                             permanent=bool(st.session_state.get(key + "_permanent")))
            st.session_state.page2_generated_media.append(record)
            added.append(record.id)
        except Exception as exc:
            errors.append(f"{file.name}：{user_facing_error_message(exc)}")
    if added:
        _set_reference_selection(_manual_reference_ids() + added)
        try:
            _upsert_record()
        except Exception as exc:
            errors.append("图片已存入本地，但聊天记录保存失败：" + user_facing_error_message(exc))
    st.session_state[key + "_upload_errors"] = errors
    # A fresh uploader prevents a rerun from importing the same selection twice.
    st.session_state[key + "_upload_nonce"] = st.session_state.get(key + "_upload_nonce", 0) + 1


def _consume_workflow_references(task):
    key = _reference_key()
    cleared = False
    if st.session_state.get(key + "_revision", 0) == task.payload.get("reference_revision", 0):
        cleared = bool(_manual_reference_ids())
        _set_reference_selection([], preview=False)
    if st.session_state.get(key + "_urls", "") == task.payload.get("reference_url_draft", ""):
        # Applied before widgets on the next rerun, including fragment completions.
        st.session_state[key + "_clear_urls"] = True
    return cleared


def _render_image_references(provider: str, media: list) -> list[str]:
    """One shared selection for image references, video input and preview."""
    key = _reference_key()
    supported = provider not in {
        page2_service.ANILLUSTRIOUS_PROVIDER, page2_service.WAI_ILLUSTRIOUS_PROVIDER,
    }
    images = _reference_images(media)
    selected = [item for item in _manual_reference_ids() if item in images]
    st.session_state[key + "_selection"] = selected
    st.session_state[key] = selected
    current_ids = {item.id for item in st.session_state.page2_generated_media if item.provider != "local"}
    busy = _one_click_video_busy()

    def label(record_id):
        item = images[record_id]
        lifetime = "永久保存" if media_store.info(item).get("saved") else "临时图片"
        origin = "当前聊天 · " if item.id in current_ids else ""
        return f"{origin}{lifetime} · {item.prompt[:24]} · {item.created_at} · {item.id[:8]}"

    if st.session_state.pop(key + "_clear_urls", False):
        st.session_state[key + "_urls"] = ""
    raw = ""
    with st.expander("参考图片与本地素材", expanded=False):
        st.multiselect("从已有图片选择参考图", options=list(images), key=key,
                       format_func=label, disabled=busy, on_change=_reference_selection_changed)
        if not supported:
            st.caption("此生图模型不使用参考图；所选图片仍会用于图生视频。")
        st.checkbox("导入后永久保存", key=key + "_permanent", disabled=busy)
        nonce_key = key + "_upload_nonce"
        nonce = st.session_state.get(nonce_key, 0)
        upload_key = f"{key}_upload_{nonce}"
        st.file_uploader("导入本地图片", type=["png", "jpg", "jpeg", "webp", "gif"],
                         accept_multiple_files=True, key=upload_key, disabled=busy,
                         on_change=_import_reference_uploads, args=(upload_key,),
                         help="选好文件后自动导入、选中并预览。每张最多 32 MB。")
        for error in st.session_state.get(key + "_upload_errors", []):
            st.error(error)
        if supported:
            raw = st.text_area("参考图片 URL（每行一个，可选）", value="", height=90,
                               key=key + "_urls", disabled=busy)

    if not supported:
        return []
    references = [image_references.media_reference(images[item]) for item in selected]
    # Pasting an old generated URL can still resolve its existing local record.
    by_url = {item.image_url_string: item for item in images.values() if item.image_url_string}
    for value in (line.strip() for line in raw.splitlines() if line.strip()):
        reference = image_references.media_reference(by_url[value]) if value in by_url else value
        if reference not in references:
            references.append(reference)
    return references


def _current_auto_image_options() -> dict:
    """Read the same image controls used by the right-hand media panel."""
    provider = normalize_image_provider(
        st.session_state.get("_page2_image_provider") or _last_image_provider()
    )
    saved_ratios = settings.get(AppStorageKeys.PAGE2_IMAGE_ASPECT_RATIOS, {})
    saved_ratios = saved_ratios if isinstance(saved_ratios, dict) else {}
    ratio = st.session_state.get(f"page2_image_aspect_ratio_{provider}")
    if ratio not in image_aspect_ratios(provider):
        ratio = saved_ratios.get(provider, page2_service.IMAGE_ASPECT_RATIO)
    if ratio not in image_aspect_ratios(provider):
        ratio = page2_service.IMAGE_ASPECT_RATIO
    quality = IMAGE_DEFAULT_QUALITY
    if provider == IMAGE25_PROVIDER:
        quality = st.session_state.get("page2_image25_quality") or settings.get(
            AppStorageKeys.PAGE2_IMAGE25_QUALITY, IMAGE_DEFAULT_QUALITY
        )
        if quality not in IMAGE25_QUALITIES:
            quality = IMAGE_DEFAULT_QUALITY
    image_urls = []
    if provider not in {
        page2_service.ANILLUSTRIOUS_PROVIDER, page2_service.WAI_ILLUSTRIOUS_PROVIDER,
    }:
        images = _reference_images()
        selected = [item for item in _manual_reference_ids() if item in images]
        image_urls = [image_references.media_reference(images[item]) for item in selected]
        by_url = {
            item.image_url_string: item for item in images.values() if item.image_url_string
        }
        raw = str(st.session_state.get(_reference_key() + "_urls", "") or "")
        for value in (line.strip() for line in raw.splitlines() if line.strip()):
            reference = (
                image_references.media_reference(by_url[value]) if value in by_url else value
            )
            if reference not in image_urls:
                image_urls.append(reference)
    validate_image_options(provider, ratio, image_urls, quality)
    mode = str(st.session_state.get("page2_image_prompt_mode") or "first_person")
    subject = str(st.session_state.get("page2_image_prompt_subject") or "").strip()
    if mode == "closeup" and not subject:
        mode = "first_person"
    if mode not in {"normal", "first_person", "closeup"}:
        mode = "first_person"
    return {
        "mode": mode,
        "subject": subject if mode == "closeup" else "",
        "provider": provider,
        "image_urls": image_urls,
        "aspect_ratio": ratio,
        "refiner_prompt": (
            str(st.session_state.get("page2_refiner_prompt") or "")
            if provider == page2_service.ANILLUSTRIOUS_PROVIDER else ""
        ),
        "quality": quality,
    }


def _schedule_auto_image(*, ctx, source_turn_id: str) -> None:
    if not ctx.auto_image_enabled or not source_turn_id:
        return
    completed = _completed_auto_image_turn_count(list(st.session_state.page2_turns))
    next_turn = st.session_state.get("page2_auto_image_next_turn")
    if next_turn is None:
        st.session_state["page2_auto_image_anchor_turn_count"] = 0
        next_turn = ctx.auto_image_interval
    elif st.session_state.get("page2_auto_image_anchor_turn_count") is None:
        st.session_state["page2_auto_image_anchor_turn_count"] = max(
            0, int(next_turn) - ctx.auto_image_interval
        )
    if completed < int(next_turn) or any(
        task.payload.get("auto_image") for task in _pending_operations()
    ):
        return
    try:
        options = _current_auto_image_options()
    except Exception as exc:
        st.session_state["page2_auto_image_next_turn"] = completed + 1
        _upsert_record()
        st.session_state["page2_operation_error"] = (
            "自动生图设置无效，下轮重试：" + user_facing_error_message(exc)
        )
        return
    workflow_id = uuid.uuid4().hex
    scope_id = _operation_scope_id()
    prompt_task = _Page2OperationTask(
        kind="image_prompt", label="自动生图 · 图片 prompt", scope_id=scope_id,
        payload={
            "ctx": deepcopy(ctx), "mode": options["mode"], "subject": options["subject"],
            "source_turn_id": source_turn_id, "auto_image": True,
            "auto_image_turn_count": completed, "workflow_id": workflow_id,
        },
    )
    image_task = _Page2OperationTask(
        kind="image", label="自动生图 · 图片", scope_id=scope_id,
        payload={
            "provider": options["provider"], "image_urls": options["image_urls"],
            "aspect_ratio": options["aspect_ratio"],
            "refiner_prompt": options["refiner_prompt"], "quality": options["quality"],
            "allow_temporary_upload": True,
            "prompt_operation_id": prompt_task.id, "auto_image": True,
            "auto_image_turn_count": completed, "workflow_id": workflow_id,
            "max_attempts": ctx.image_poll_attempts,
        },
    )
    st.session_state["page2_operation_queue"] = (
        list(st.session_state.get("page2_operation_queue") or []) + [prompt_task, image_task]
    )
    st.session_state["page2_operation_notice"] = "已加入队列：自动生图"


def _restart_auto_image_countdown() -> None:
    """用户主动生图成功后，自动生图的轮次计数从当前轮重新开始计。

    例如自动生图间隔为 5 轮，用户在第 3 轮主动生成了图片，
    则下一次自动生图顺延到第 3+5=8 轮，而不是原先的第 5 轮。
    已排定的更晚时点不会被提前。
    """
    ctx = page2_service.load_context_from_settings()
    if not ctx.auto_image_enabled:
        return
    completed = _completed_auto_image_turn_count(list(st.session_state.page2_turns))
    target = completed + int(ctx.auto_image_interval)
    current = st.session_state.get("page2_auto_image_next_turn")
    if current is not None and int(current) >= target:
        return
    st.session_state["page2_auto_image_anchor_turn_count"] = completed
    st.session_state["page2_auto_image_next_turn"] = target
    try:
        _upsert_record()
    except Exception:
        pass


def _queue_image_generation(
    *,
    provider: str,
    prompt: str,
    image_urls: list[str],
    aspect_ratio: str,
    mode: str,
    subject: str,
    refiner_prompt: str = "",
    quality: str = IMAGE_DEFAULT_QUALITY,
    allow_temporary_upload: bool = False,
) -> _Page2OperationTask:
    """Generate a missing prompt first, then make the image from that exact result."""
    existing = _media_button_task("image")
    if existing is not None:
        return existing
    provider = normalize_image_provider(provider)
    image_urls = [str(url).strip() for url in image_urls if str(url).strip()]
    validate_image_options(provider, aspect_ratio, image_urls, quality)
    prompt_text = str(prompt or "")
    prompt_dependency = None
    if not prompt_text.strip():
        prompt_dependency = _enqueue_operation(
            "image_prompt",
            "生成图片 prompt",
            {"mode": mode, "subject": subject, "media_button": "image"},
            allow_same_kind=True,
        )
    task = _enqueue_operation(
        "image",
        "生成图片",
        {
            "provider": provider,
            "media_button": "image",
            "max_attempts": page2_service.load_context_from_settings().image_poll_attempts,
            "prompt": prompt_text,
            "image_urls": list(image_urls or []),
            "aspect_ratio": aspect_ratio,
            "refiner_prompt": str(refiner_prompt or ""),
            "allow_temporary_upload": bool(allow_temporary_upload),
            **({"quality": quality} if provider in IMAGE_QUALITY_PROVIDERS else {}),
            "prompt_operation_id": (
                prompt_dependency.id if prompt_dependency is not None else ""
            ),
            "image_prompt_source": (
                {}
                if prompt_dependency is not None
                else dict(
                    st.session_state.get("page2_image_prompt_source")
                    or latest_image_prompt_source(list(st.session_state.page2_turns))
                )
            ),
        },
        allow_same_kind=True,
    )
    return task


def _queue_one_click_video(
    *, ctx, provider: str, image_urls: list[str], aspect_ratio: str,
    refiner_prompt: str = "",
    quality: str = IMAGE_DEFAULT_QUALITY,
    allow_temporary_upload: bool = False,
) -> list[_Page2OperationTask]:
    """Prepare a new image and video prompt; video generation remains a manual action."""
    if _one_click_video_busy():
        raise ValueError("一键生成正在执行，请等待完成。")
    if _chat_edit_active():
        raise ValueError("消息编辑中，请完成后再一键生成。")
    selected_id = st.session_state.get("page2_pending_video_source") or st.session_state.get("page2_video_source")
    if _video_prompt_draft(selected_id).strip():
        raise ValueError("视频 prompt 已有内容，请清空后再一键生成。")
    if not latest_image_prompt_source(list(st.session_state.page2_turns)) and not _has_earlier_operation("chat"):
        raise ValueError("先在左侧生成一条助手回复，再一键生成。")
    provider = normalize_image_provider(provider)
    image_urls = [str(url).strip() for url in image_urls if str(url).strip()]
    validate_image_options(provider, aspect_ratio, image_urls, quality)
    ctx = deepcopy(ctx)
    workflow_id = uuid.uuid4().hex
    scope_id = _operation_scope_id()

    def stage(kind, label, payload):
        return _Page2OperationTask(
            kind=kind, label=label, scope_id=scope_id,
            payload={**payload, "workflow_id": workflow_id},
        )

    image_prompt = stage("image_prompt", "一键生成 1/3 · 图片 prompt", {
        "ctx": ctx, "mode": "normal", "subject": "",
    })
    image = stage("image", "一键生成 2/3 · 图片", {
        "provider": provider, "image_urls": image_urls, "aspect_ratio": aspect_ratio,
        "max_attempts": ctx.image_poll_attempts,
        "refiner_prompt": str(refiner_prompt or "") if provider == page2_service.ANILLUSTRIOUS_PROVIDER else "",
        "allow_temporary_upload": bool(allow_temporary_upload),
        **({"quality": quality} if provider in IMAGE_QUALITY_PROVIDERS else {}),
        "prompt_operation_id": image_prompt.id,
        "reference_revision": st.session_state.get(_reference_key() + "_revision", 0),
        "reference_url_draft": st.session_state.get(_reference_key() + "_urls", ""),
    })
    video_prompt = stage("video_prompt", "一键生成 3/3 · 视频 prompt", {
        "ctx": ctx, "image_operation_id": image.id,
    })
    tasks = [image_prompt, image, video_prompt]
    # Publish the full chain only after validation and snapshots succeed.
    st.session_state["page2_operation_queue"] = list(st.session_state.get("page2_operation_queue") or []) + tasks
    st.session_state["page2_operation_notice"] = "已加入队列：一键生成图片和视频 prompt"
    return tasks


def _close_cloudinary_upload_dialog() -> None:
    st.session_state.pop("page2_cloudinary_upload_confirmation", None)


def _request_cloudinary_upload_confirmation(action: str, payload: dict, requirements: list[dict]) -> None:
    st.session_state["page2_cloudinary_upload_confirmation"] = {
        "scope_id": _operation_scope_id(),
        "action": action,
        "payload": payload,
        "requirements": requirements,
    }


def _format_upload_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f} MiB" if size_bytes >= 1024 * 1024 else f"{size_bytes / 1024:.0f} KiB"


@st.dialog("图片超过直传大小", width="small", on_dismiss=_close_cloudinary_upload_dialog)
def _render_cloudinary_upload_dialog() -> None:
    pending = st.session_state.get("page2_cloudinary_upload_confirmation")
    if not pending:
        return
    if pending.get("scope_id") != _operation_scope_id():
        _close_cloudinary_upload_dialog()
        st.warning("当前聊天记录已经变化，请重新发起生成。")
        return
    requirements = list(pending.get("requirements") or [])
    largest = max(requirements, key=lambda item: item["size_bytes"])
    count_text = f"其中 {len(requirements)} 张图片" if len(requirements) > 1 else "这张图片"
    st.write(
        f"{count_text}超过当前接口的直传预算。最大图片处理后为 "
        f"{_format_upload_size(largest['size_bytes'])}，直传预算为 "
        f"{_format_upload_size(largest['budget_bytes'])}。"
    )
    st.caption("继续后会临时上传到你的 Cloudinary，并在最后一次使用约 48 小时后自动清理。取消则不会上传，也不会提交本次生成任务。")
    configured = bool(CloudinaryConfig.api_key() and CloudinaryConfig.api_secret())
    if not configured:
        st.error("尚未配置 Cloudinary API Key 和 API Secret。请取消后到 APIkey 页面配置。")
    confirm, cancel = st.columns(2)
    if confirm.button(
        "上传并继续", type="primary", use_container_width=True,
        disabled=not configured, key="page2_confirm_cloudinary_upload",
    ):
        action = pending["action"]
        payload = dict(pending.get("payload") or {})
        try:
            if action == "image":
                _queue_image_generation(**payload, allow_temporary_upload=True)
            elif action == "workflow":
                _queue_one_click_video(**payload, allow_temporary_upload=True)
            elif action == "video":
                operation = dict(payload["operation"])
                operation["allow_temporary_upload"] = True
                _enqueue_media_operation(
                    payload["kind"], payload["label"], operation, action="video",
                )
            else:
                raise ValueError("未知的 Cloudinary 确认操作。")
        except Exception as exc:
            _show_error(exc)
        else:
            _close_cloudinary_upload_dialog()
            st.rerun()
    if cancel.button("取消", use_container_width=True, key="page2_cancel_cloudinary_upload"):
        _close_cloudinary_upload_dialog()
        st.rerun()


def _find_video_source(source_id: str):
    current = next((item for item in st.session_state.page2_generated_media
                 if item.id == source_id and item.media_kind == GeneratedMediaKind.IMAGE
                 and media_store.available(item)), None)
    if current is not None:
        return current
    candidate = st.session_state.get("page2_reference_catalog", {}).get(source_id)
    return candidate if candidate is not None and media_store.available(candidate) else None


def _video_story_for_source(source):
    if not any(item.id == source.id for item in st.session_state.page2_generated_media):
        # A library image from another chat must not borrow this chat's story.
        source = deepcopy(source)
        source.source_turn_id = ""
    return resolve_video_story(source, list(st.session_state.page2_turns))


def _video_prompt_draft(selected_id: str | None) -> str:
    source = _find_video_source(selected_id)
    if source is not None:
        return str(source.video_prompt or "")
    task = next((item for item in _pending_operations()
                 if item.kind == "image" and "pending:" + item.id == selected_id), None)
    return str(task.payload.get("pending_video_prompt") or "") if task is not None else ""


def _media_selectbox_key() -> str:
    nonce = int(st.session_state.get("page2_media_selection_nonce", 0))
    return "page2_media_record_select" + (f"_{nonce}" if nonce else "")


def _activate_media_selection(selectbox_key: str = "page2_media_record_select") -> None:
    if selectbox_key != _media_selectbox_key():
        return  # Ignore delayed events from the selector replaced by View.
    url_favorites.activate_generated_media_selection(
        "page2", "page2_selected_media_id", selectbox_key,
    )
    source_id = st.session_state.get(selectbox_key, "")
    if not _one_click_video_busy() and _find_video_source(source_id) is not None:
        # 仅设为视频输入源，不自动带入生图参考图；参考图只能由用户在参考图片区主动选择。
        st.session_state["page2_pending_video_source"] = source_id


def _dismiss_media_completion(task_id: str) -> None:
    st.session_state["page2_media_completions"] = [
        item for item in st.session_state.get("page2_media_completions", [])
        if item["task_id"] != task_id
    ]


def _select_media_preview(media_id: str) -> bool:
    if (_find_video_source(media_id) is not None or
            any(item.id == media_id and media_store.available(item) for item in st.session_state.page2_generated_media)):
        st.session_state.page2_selected_media_id = media_id
        st.session_state.page2_pending_media_record_select = media_id
        # A fresh widget identity prevents an in-flight old selection event
        # from undoing a preview change during a fragment rerun.
        st.session_state["page2_media_selection_nonce"] = int(
            st.session_state.get("page2_media_selection_nonce", 0)
        ) + 1
        url_favorites.clear_preview_url("page2")
        return True
    return False


def _view_completed_media(task_id: str, media_id: str) -> None:
    if _select_media_preview(media_id):
        if not _one_click_video_busy() and _find_video_source(media_id) is not None:
            # 仅设为视频输入源，不自动带入生图参考图；参考图只能由用户在参考图片区主动选择。
            st.session_state["page2_pending_video_source"] = media_id
    _dismiss_media_completion(task_id)


def _render_media_completions() -> None:
    media_ids = {item.id for item in st.session_state.page2_generated_media if media_store.available(item)}
    for item in list(st.session_state.get("page2_media_completions") or []):
        if item["kind"] != "video" or item["media_id"] not in media_ids:
            _dismiss_media_completion(item["task_id"])
            continue
        message, view, dismiss = st.columns([4, 1, 1])
        with message:
            st.success("视频生成完成")
        with view:
            st.button("查看", key="page2_view_completed_" + item["task_id"],
                      on_click=_view_completed_media, args=(item["task_id"], item["media_id"]))
        # 旧的图片完成提示及手动“查看”入口留作恢复参考：
        # with message:
        #     st.success("图片生成完成")
        # with view:
        #     st.button("查看", key="page2_view_completed_" + item["task_id"],
        #               on_click=_view_completed_media, args=(item["task_id"], item["media_id"]))
        # with dismiss:
        #     st.button("关闭", key="page2_dismiss_completed_" + item["task_id"],
        #               on_click=_dismiss_media_completion, args=(item["task_id"],))
        with dismiss:
            st.button("关闭", key="page2_dismiss_completed_" + item["task_id"],
                      on_click=_dismiss_media_completion, args=(item["task_id"],))


def _save_image_video_field(source_id: str, field: str, text: str) -> None:
    source = _find_video_source(source_id)
    if source is None:
        raise ValueError("输入图片已不存在，请重新选择。")
    previous = getattr(source, field)
    setattr(source, field, text)
    try:
        if any(item.id == source.id for item in st.session_state.page2_generated_media):
            _upsert_record()
        media_store.remember_image(source)
    except Exception:
        setattr(source, field, previous)
        raise


def _save_video_prompt_field(source_id: str, image_task_id: str) -> None:
    if _one_click_video_busy():
        return  # Ignore delayed editor events while the workflow owns the video input.
    text = str(st.session_state.get("_page2_video_prompt") or "")
    if source_id:
        try:
            _save_image_video_field(source_id, "video_prompt", text)
        except Exception as exc:
            st.session_state.page2_operation_error = "视频 prompt 保存失败：" + user_facing_error_message(exc)
    elif image_task_id:
        task = next((item for item in _pending_operations()
                     if item.kind == "image" and item.id == image_task_id), None)
        if task is not None:
            task.payload["pending_video_prompt"] = text
    else:
        st.session_state.page2_video_prompt = text


def _close_video_story_dialog() -> None:
    st.session_state.pop("page2_video_story_dialog", None)


@st.dialog("补录图片对应的故事", on_dismiss=_close_video_story_dialog, dismissible=False)
def _render_video_story_dialog(source_id: str) -> None:
    source = _find_video_source(source_id)
    if source is None:
        st.error("这张图片已不存在。")
        return
    st.caption("对应图片：" + (source.prompt[:80] or "未保存图片 prompt"))
    story_key = f"page2_video_story_input_{source_id}"
    prompt_draft_guard.protect(story_key, source.video_story_text, "视频来源故事")
    text = st.text_area(
        "粘贴对应那一轮的助手故事正文",
        value=source.video_story_text, height=220,
        key=story_key,
    )
    st.caption("仅保存为这张图片的故事来源，不会添加或修改聊天记录。")
    prompt_draft_guard.save_action("page2_save_video_story", story_key)
    if st.button("保存故事", key="page2_save_video_story", disabled=page2_operation_queue_busy()):
        if not str(text or "").strip():
            st.error("请先粘贴助手的故事正文。")
            render_media_button_guard()
            return
        try:
            _save_image_video_field(source_id, "video_story_text", text.strip())
        except Exception as exc:
            _show_error(exc)
            render_media_button_guard()
            return
        prompt_draft_guard.saved(story_key)
        st.session_state.page2_video_story_toast = None
        _close_video_story_dialog()
        st.session_state.page2_operation_error = ""
        st.session_state.page2_operation_notice = "故事已补录，请点击“生视频prompt”。"
        st.rerun()
    render_media_button_guard()


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


def _render_video_prompt_controls(media: list, video_model: str, *, image_options: dict | None = None) -> None:
    render_toast_style()
    workflow_busy = _one_click_video_busy()
    images = list(st.session_state.get("page2_reference_catalog", {}).values()) + [
        item for item in media if item.media_kind == GeneratedMediaKind.IMAGE and media_store.available(item)]
    image_tasks = {"pending:" + task.id: task for task in _pending_operations() if task.kind == "image"}
    by_id = {item.id: item for item in images}
    options = list(by_id) + list(image_tasks)
    previous_source = st.session_state.get("page2_video_source")
    # If the user explicitly chose a pending image, keep that same input after
    # it finishes. Unrelated completions must never choose another reference.
    if str(previous_source or "").startswith("pending:") and previous_source not in image_tasks:
        resolved_id = (st.session_state.get("page2_operation_outputs") or {}).get(previous_source[8:])
        st.session_state["page2_video_source"] = resolved_id if resolved_id in by_id else None
    preferred = st.session_state.pop("page2_pending_video_source", "")
    manual = [item for item in _manual_reference_ids() if item in by_id]
    latest = st.session_state.get(_reference_key() + "_latest_generated")
    if latest not in by_id:
        latest = next((item.id for item in reversed(st.session_state.page2_generated_media)
                       if item.id in by_id and item.provider != "local"), None)
    if not workflow_busy:
        if manual:
            st.session_state["page2_video_source"] = manual[-1]
        elif preferred in options:
            st.session_state["page2_video_source"] = preferred
        elif st.session_state.get("page2_video_source") in image_tasks:
            pass  # Retain an already queued dependency until its exact result arrives.
        else:
            st.session_state["page2_video_source"] = latest
    elif st.session_state.get("page2_video_source") not in options:
        st.session_state["page2_video_source"] = None
    selected_id = st.session_state.get("page2_video_source")
    text_only = False
    if video_model == WAN3_MODEL:
        text_only = _render_wan3_input_mode(selected_id)
    if text_only:
        st.text("视频输入：纯文字")
    elif selected_id in image_tasks:
        st.text("视频输入：等待所选生图任务完成")
    elif selected_id in by_id:
        origin = "手动选择" if manual else "最新生成"
        st.text(f"视频输入（{origin}）：{by_id[selected_id].prompt[:48]}")
    else:
        st.text("视频输入：请在参考图片中选图，或先生成一张图片。")
    source = None if text_only else by_id.get(selected_id)
    image_dependency = None if text_only else image_tasks.get(selected_id)
    source_id = source.id if source is not None else ""
    image_task_id = image_dependency.id if image_dependency is not None else ""
    draft = (str(st.session_state.page2_video_prompt or "") if text_only
             else _video_prompt_draft(selected_id))
    seconds, generate_audio = _render_video_duration_controls(video_model)
    resolution = _render_wan3_resolution_selector() if video_model == WAN3_MODEL else None
    aspect_ratio = None
    if text_only:
        aspect_ratio = _render_wan3_aspect_ratio_selector()
    if video_model == WAN3_MODEL and not text_only:
        st.caption("画面比例跟随输入图片；需要说话时，请在 prompt 中写明台词。")
    st.session_state["_page2_video_prompt"] = draft
    prompt_draft_guard.protect(
        "_page2_video_prompt", draft, "视频 prompt", requires_commit=False
    )
    prompt = st.text_area(
        "视频 prompt", key="_page2_video_prompt", height=68,
        disabled=(not text_only and not selected_id) or workflow_busy,
        on_change=_save_video_prompt_field, args=(source_id, image_task_id),
    )
    render_editor_sizing()
    ctx = page2_service.load_context_from_settings()
    ctx.selected_video_generation_provider = video_model
    kind = "video" if text_only or str(prompt or "").strip() else "video_prompt"
    label = "生视频" if kind == "video" else "生视频prompt"
    manual_button, workflow_button = st.columns(2)
    if manual_button.button(
        label, key="page2_generate_video", use_container_width=True,
        disabled=(
            (not str(prompt or "").strip() if text_only else not selected_id)
            or _chat_edit_active() or workflow_busy or _media_button_task("video") is not None
        ),
    ):
        try:
            if kind == "video_prompt" and source is not None:
                _video_story_for_source(source)
            operation = {
                "ctx": ctx,
                "source_id": source_id,
                "image_operation_id": image_task_id,
                "prompt": prompt,
                "seconds": seconds,
                "generate_audio": generate_audio,
                "text_only": text_only,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
            }
            requirement = (
                image_references.temporary_upload_requirement(
                    source, provider=video_model, purpose="video",
                )
                if kind == "video" and source is not None else None
            )
        except MissingVideoStorySource:
            st.session_state.page2_video_story_toast = source.id
        except Exception as exc:
            _show_error(exc)
        else:
            if requirement is not None:
                _request_cloudinary_upload_confirmation(
                    "video",
                    {"kind": kind, "label": label, "operation": operation},
                    [requirement],
                )
            else:
                _enqueue_media_operation(
                    kind, label, operation, action="video",
                )
            st.rerun()
    if workflow_button.button(
        "一键生成", key="page2_generate_video_workflow", use_container_width=True,
        disabled=(
            text_only or image_options is None or _chat_edit_active() or workflow_busy or bool(str(prompt or "").strip())
            or (not latest_image_prompt_source(list(st.session_state.page2_turns))
                and not _has_earlier_operation("chat"))
        ),
        help="视频 prompt 为空时可用。使用 normal 模式生成图片 prompt、图片和视频 prompt，完成后等待你点击“生视频”。",
    ):
        workflow_payload = {"ctx": ctx, **image_options}
        try:
            requirements = image_references.temporary_upload_requirements(
                image_options["image_urls"], provider=image_options["provider"],
            )
            if requirements:
                _request_cloudinary_upload_confirmation(
                    "workflow", workflow_payload, requirements,
                )
            else:
                _queue_one_click_video(**workflow_payload)
        except Exception as exc:
            _show_error(exc)
        else:
            st.rerun()  # Render every video-input control locked immediately after submission.
    if workflow_busy:
        st.caption("一键生成中，视频输入图片、prompt 和左侧按钮暂时锁定；完成后可编辑并手动生成视频。")
    if source is not None and source.video_story_text:
        if st.button("编辑补录故事", key="page2_edit_video_story", disabled=page2_operation_queue_busy()):
            st.session_state["page2_video_story_dialog"] = source.id
    _render_video_story_toast()
    dialog_source = st.session_state.get("page2_video_story_dialog")
    if dialog_source:
        _render_video_story_dialog(dialog_source)


def _render_media_actions(record, label):
    kind = getattr(record.media_kind, "value", record.media_kind)
    meta = media_store.info(record)
    path = media_store.local_path(record)
    saved = bool(meta.get("saved"))
    if saved and path:
        st.caption("已永久保存 · 删除聊天后仍保留")
    elif not path and meta.get("status") == "retry":
        st.warning("尚未成功存入本地，暂不能保证保留 48 小时。会自动重试，也可以点击“重试本地保存”。")
        if st.button("重试本地保存", key=f"retry_media_{record.id}"):
            try:
                media_store.cache_record(record)
                st.rerun()
            except Exception as exc:
                _show_error(exc)
    elif not path:
        st.caption("正在存入本地，成功后保留 48 小时。")

    delete_col, save_col, download_col = st.columns(3)
    with delete_col:
        if st.button("删除", key=f"page2_delete_{kind}_{record.id}",
                     use_container_width=True, disabled=page2_operation_queue_busy()):
            request_delete("page2_generated_media", record.id, label)
    with save_col:
        if st.button("已保存" if saved else "保存", key=f"page2_save_{kind}_{record.id}",
                     use_container_width=True, disabled=page2_operation_queue_busy() or saved,
                     help="永久保存在本地，不随聊天删除或到期清理。"):
            try:
                with st.spinner("正在永久保存…"):
                    _save_image_record(record)
                st.rerun()
            except Exception as exc:
                _show_error(exc)
    with download_col:
        if path:
            data = partial(media_store.read_bytes, record)
            suffix = path.suffix
        elif kind == "image":
            data = (_decode_image_base64(record.image_data_base64) if record.image_data_base64
                    else partial(download_image_bytes, record.image_url_string))
            suffix = _image_download_suffix(record.image_url_string)
        else:
            key = OpenRouterConfig.api_key() if is_openrouter_video_url(record.video_url_string) else ""
            data = partial(download_video_bytes, record.video_url_string, api_key=key)
            suffix = ".mp4"
        st.download_button(
            "下载", data=data, file_name=f"{kind}-{record.id}{suffix}",
            mime={".mp4": "video/mp4", ".jpg": "image/jpeg", ".webp": "image/webp",
                  ".gif": "image/gif"}.get(suffix, "image/png"),
            key=f"page2_download_{kind}_{record.id}", use_container_width=True, on_click="ignore",
        )


def _render_media_column():
    with st.container(key="page2_media_controls"):
        _render_media_column_contents()
    render_media_button_guard()


def _render_media_column_contents():
    media_store.request_cache(st.session_state.page2_generated_media)
    media = [item for item in st.session_state.page2_generated_media if media_store.available(item)]
    images = _reference_images(media)
    selected_refs = [item for item in _manual_reference_ids() if item in images]
    st.session_state[_reference_key() + "_selection"] = selected_refs
    shown_ids = {item.id for item in media}
    media += [images[item] for item in selected_refs if item not in shown_ids]
    st.session_state["page2_media_revision"] = media_store.revision()
    # 图片和视频完成后直接切换预览；旧的“完成 / 查看 / 关闭”提示保留在
    # _render_media_completions 中，需要恢复手动查看时可重新启用。
    # _render_media_completions()
    url_preview = url_favorites.get_preview_url("page2")

    if not media:
        st.session_state.page2_selected_media_id = None
        st.session_state[_media_selectbox_key()] = None
        if url_preview:
            with st.container(key="page2_media_preview"):
                _render_url_preview_image(url_preview)
        else:
            st.caption("暂无媒体记录。")
        _render_media_operation_status()
    else:
        media_ids = []
        labels_by_id = {}
        for item in media:
            kind = item.media_kind.value if hasattr(item.media_kind, "value") else str(item.media_kind)
            label = f"{kind.upper()} • {item.provider} • {str(item.created_at or '')}"
            media_ids.append(item.id)
            labels_by_id[item.id] = label

        preview_container = st.container(key="page2_media_preview")
        # 任务状态与错误提示紧跟图片预览下方、prompt 区块上方。
        _render_media_operation_status()
        prompt_container = st.container()

        selected_media_id = str(st.session_state.get("page2_selected_media_id", "") or "")
        desired_media_id = (
            selected_media_id
            if selected_media_id in media_ids
            else None
        )

        media_selectbox_key = _media_selectbox_key()
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
            index=None,
            placeholder="选择素材；新图片和视频生成后会自动显示",
            key=media_selectbox_key,
            format_func=lambda media_id: labels_by_id.get(media_id, media_id),
            on_change=_activate_media_selection,
            args=(media_selectbox_key,),
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
                    if media_store.local_path(selected):
                        st.image(media_store.read_bytes(selected))
                    elif selected.image_data_base64:
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

            _render_media_actions(selected, labels_by_id.get(selected_id, "当前媒体记录"))

            render_delete_confirmation("page2_generated_media", _delete_media_record,
                                       warning="这会删除对应的本地图片或视频，包括已永久保存的文件。删除后无法恢复。")

    st.divider()
    st.subheader("生成图片")

    image_prompt_conversation = page2_service.build_recent_image_prompt_conversation(
        st.session_state.page2_turns,
    )
    if not image_prompt_conversation:
        st.caption("先在左侧生成一条助手回复，然后可以点击“生成图片prompt”。")

    # Separate widget state from the draft so background results can update it
    # after rendering, and hiding a field does not discard the saved draft.
    mode = _render_image_prompt_mode_selector()
    subject = ""
    if mode == "closeup":
        st.session_state["_page2_image_prompt_subject"] = st.session_state.page2_image_prompt_subject
        prompt_draft_guard.protect(
            "_page2_image_prompt_subject",
            st.session_state.page2_image_prompt_subject,
            "图片 prompt 主体",
            requires_commit=False,
        )
        subject = st.text_input(
            "主体",
            key="_page2_image_prompt_subject",
            on_change=_save_image_prompt_field,
            args=("_page2_image_prompt_subject", "page2_image_prompt_subject"),
        )

    if st.button(
        "生成图片prompt",
        key="page2_generate_image_prompt",
        use_container_width=True,
        disabled=(_media_button_task("image_prompt") is not None
                  or (not image_prompt_conversation and not _has_earlier_operation("chat"))),
    ):
        _enqueue_media_operation(
            "image_prompt",
            "生成图片 prompt",
            {"mode": mode, "subject": subject},
            action="image_prompt",
        )
        st.rerun()

    st.session_state["_page2_image_prompt"] = st.session_state.page2_image_prompt
    prompt_draft_guard.protect(
        "_page2_image_prompt",
        st.session_state.page2_image_prompt,
        "图片 prompt",
        requires_commit=False,
    )
    prompt_text = st.text_area(
        "图片prompt（可编辑）",
        key="_page2_image_prompt",
        height=160,
        on_change=_save_image_prompt_field,
        args=("_page2_image_prompt", "page2_image_prompt"),
    )

    provider = _render_image_model_selector()
    quality = _render_image_quality_selector(provider)
    refiner_prompt = ""
    if provider == page2_service.ANILLUSTRIOUS_PROVIDER:
        st.session_state["_page2_refiner_prompt"] = (
            st.session_state.page2_refiner_prompt
        )
        prompt_draft_guard.protect(
            "_page2_refiner_prompt",
            st.session_state.page2_refiner_prompt,
            "refiner prompt",
            requires_commit=False,
        )
        refiner_prompt = st.text_area(
            "refiner",
            key="_page2_refiner_prompt",
            height=100,
            placeholder="留空则关闭 refiner；填写后作为 refiner_prompt 发送",
            on_change=_save_image_prompt_field,
            args=("_page2_refiner_prompt", "page2_refiner_prompt"),
        )
    aspect_ratio = _render_image_aspect_ratio_selector(provider)
    image_urls = _render_image_references(provider, media)

    if st.button(
        "生成图片",
        key="page2_generate_image",
        type="primary",
        use_container_width=True,
        disabled=(
            _media_button_task("image") is not None or (
            not str(prompt_text or "").strip()
            and not image_prompt_conversation
            and not _has_earlier_operation("chat")
            and not _has_earlier_operation("image_prompt"))
        ),
    ):
        image_payload = {
            "provider": provider,
            "prompt": prompt_text,
            "image_urls": image_urls,
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "subject": subject,
            "refiner_prompt": refiner_prompt,
            "quality": quality,
        }
        try:
            requirements = image_references.temporary_upload_requirements(
                image_urls, provider=provider,
            )
            if requirements:
                _request_cloudinary_upload_confirmation(
                    "image", image_payload, requirements,
                )
            else:
                _queue_image_generation(**image_payload)
        except Exception as exc:
            _show_error(exc)
        else:
            st.rerun()

    st.divider()
    st.subheader("图生视频")
    video_model = _render_video_model_selector()
    st.caption("生视频优先使用最后选中的参考图，未选图时使用最新生成的图片。一键流程生图成功后清空参考图，再为新图生成视频 prompt。")

    _render_video_prompt_controls(media, video_model, image_options={
        "provider": provider, "aspect_ratio": aspect_ratio,
        "image_urls": image_urls, "refiner_prompt": refiner_prompt,
        "quality": quality,
    })

    url_favorites.render_url_favorites("page2")
    if st.session_state.get("page2_cloudinary_upload_confirmation"):
        _render_cloudinary_upload_dialog()


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

/* Each desktop column owns its scroll range. Keep native wheel targeting and
   nested text-field scrolling; only stop chaining at the column boundary. */
@media (min-width: 769px) {
  section[data-testid="stMain"]:has(.st-key-page2_workspace) {
    overflow: hidden !important;
  }

  .st-key-page2_dialogue_pane,
  .st-key-page2_media_pane {
    height: calc(100dvh - 40px) !important;
    max-height: calc(100dvh - 40px) !important;
    min-height: 0 !important;
    flex: 0 0 auto !important;
    overflow-y: auto !important;
    overflow-x: hidden !important;
    overscroll-behavior-y: contain;
  }

  .st-key-page2_dialogue_pane > div,
  .st-key-page2_media_pane > div {
    flex-shrink: 0 !important;
  }

  .st-key-page2_dialogue_pane,
  .st-key-page2_media_pane {
    scrollbar-width: none;
  }

  .st-key-page2_dialogue_pane::-webkit-scrollbar,
  .st-key-page2_media_pane::-webkit-scrollbar {
    display: none;
    width: 0;
    height: 0;
  }
}

/* Hide the chat-history scrollbar without affecting nested text fields. */
.st-key-page2_dialogue_pane .st-key-page2_chat_history {
  scrollbar-width: none;
}

.st-key-page2_dialogue_pane .st-key-page2_chat_history::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

/* Let a finished chat-history scroll continue into the left column so Story
   Brain remains reachable. The outer pane contains scrolling on desktop. */
.st-key-page2_dialogue_pane div[data-testid="stVerticalBlock"].st-key-page2_chat_canvas {
  overscroll-behavior: auto !important;
}

/* A narrow viewport uses the document's normal vertical flow. Target only
   the workspace columns, leaving button rows and other nested columns alone. */
@media (max-width: 768px) {
  .st-key-page2_workspace > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
    flex-direction: column !important;
  }

  .st-key-page2_workspace > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    min-width: 0 !important;
  }
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

/* Chat message actions (编辑消息 / 生成语音): bare white icon, no chrome. */
section[data-testid="stMain"] [class*="st-key-page2_chat_edit_open_"] button,
section[data-testid="stMain"] [class*="st-key-page2_tts_generate_"] button {
  background: transparent !important;
  border-color: transparent !important;
  box-shadow: none !important;
  -webkit-backdrop-filter: none !important;
  backdrop-filter: none !important;
  color: #ffffff !important;
}

section[data-testid="stMain"] [class*="st-key-page2_chat_edit_open_"] button svg,
section[data-testid="stMain"] [class*="st-key-page2_tts_generate_"] button svg {
  color: #ffffff !important;
  fill: currentColor !important;
}

section[data-testid="stMain"] [class*="st-key-page2_chat_edit_open_"] button:hover,
section[data-testid="stMain"] [class*="st-key-page2_tts_generate_"] button:hover {
  background: rgba(255, 255, 255, 0.06) !important;
  border-color: transparent !important;
  color: #ffffff !important;
}

section[data-testid="stMain"] [class*="st-key-page2_chat_edit_open_"] button:disabled,
section[data-testid="stMain"] [class*="st-key-page2_tts_generate_"] button:disabled {
  background: transparent !important;
  border-color: transparent !important;
  box-shadow: none !important;
  opacity: 1 !important;
  color: rgba(255, 255, 255, 0.35) !important;
}

section[data-testid="stMain"] [class*="st-key-page2_chat_edit_open_"] button:disabled svg,
section[data-testid="stMain"] [class*="st-key-page2_tts_generate_"] button:disabled svg {
  color: rgba(255, 255, 255, 0.35) !important;
}
</style>
        """,
    )

    # Keep the chat canvas and portrait image preview balanced on a full-screen layout.
    with st.container(key="page2_workspace"):
        left, right = st.columns([1.00, 1.00])
        with left:
            with st.container(key="page2_dialogue_pane"):
                _render_chat_column()
        with right:
            with st.container(key="page2_media_pane"):
                _render_media_column()
                # Keep queue feedback reachable inside the scrollable workspace.
                _render_operation_queue_status()

    # Mutual exclusion: only one chat audio may play at a time.
    st.iframe(
        """
<script>
(() => {
  const parent = window.parent;
  if (parent.__dndAudioMutexInstalled) return;
  parent.__dndAudioMutexInstalled = true;
  const doc = parent.document;
  doc.addEventListener('play', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLAudioElement)) return;
    doc.querySelectorAll('audio').forEach((a) => {
      if (a !== target) a.pause();
    });
  }, true);
})();
</script>
        """,
        height=1,
    )
