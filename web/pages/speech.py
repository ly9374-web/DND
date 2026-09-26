from pathlib import Path
from datetime import datetime

import streamlit as st

from app.api.speech_client import VOICES, SpeechError
from app.config import AppStorageKeys, DATA_DIR, get_effective_api_key, settings
from app.services.speech_generation import ACTIVE, SpeechJobs, owner_key
from web.nav import goto


@st.cache_resource
def speech_jobs(version: int = 2) -> SpeechJobs:
    # Invalidate the old cached worker after the direct-connection upgrade.
    return SpeechJobs(DATA_DIR / "SpeechJobs", Path(__file__).resolve().parents[2] / "故事")


def _api_key() -> str:
    try:
        return get_effective_api_key(settings.get(AppStorageKeys.DASHSCOPE_API_KEY, ""), "DASHSCOPE_API_KEY")
    except ValueError as exc:
        raise SpeechError("DashScope Key 包含无效字符，请到 APIkey 页面重新填写。") from exc


def _remember(widget: str, draft: str) -> None:
    st.session_state[draft] = st.session_state[widget]


def _restore(widget: str, draft: str, default):
    if widget not in st.session_state:
        st.session_state[widget] = st.session_state.get(draft, default)


def _elapsed(timestamp: str) -> str:
    try:
        started = datetime.fromisoformat(timestamp)
        seconds = max(0, int((datetime.now(tz=started.tzinfo) - started).total_seconds()))
    except (TypeError, ValueError):
        return "0 秒"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"


def _stage_caption(job: dict) -> str:
    phase = job.get("phase", "synthesizing")
    description = {"preparing": "正在准备", "synthesizing": "正在合成",
                   "downloading": "正在下载音频", "saving": "正在保存片段",
                   "merging": "正在合并保存"}.get(phase, "正在处理")
    if phase != "merging":
        description = f"第 {job.get('current_chunk', job['completed'] + 1)} / {job['total']} 段 · {description}"
    attempt = job.get("attempt", 1)
    if attempt > 1:
        description += f"（正在重试，第 {attempt} / 3 次尝试）"
    if phase == "downloading" and job.get("downloaded_bytes", 0):
        description += f" · 已下载 {job['downloaded_bytes'] / 1024:.0f} KB"
    description += f" · 当前步骤已等待 {_elapsed(job.get('phase_started_at', job['created_at']))}"
    description += f" · 本次累计 {_elapsed(job.get('started_at', job['created_at']))}"
    return description


def _render_generator(owner: str):
    jobs = speech_jobs()
    job = jobs.latest(owner)
    busy = job is not None and job["status"] in ACTIVE
    prefix = f"speech_{owner}_"
    language_key, voice_key, text_key = (prefix + name for name in ("language", "voice", "text"))
    for key, default in ((language_key, "中文"), (voice_key, next(iter(VOICES))), (text_key, "")):
        _restore(key, key + "_draft", default)

    language_column, voice_column = st.columns(2)
    with language_column:
        st.selectbox(
            "语言", ["中文", "英文"], key=language_key,
            help="此选项暂不影响语音生成，正文将直接交给 TTS。",
            on_change=_remember, args=(language_key, language_key + "_draft"),
        )
    with voice_column:
        st.selectbox(
            "音色", list(VOICES), format_func=VOICES.get, key=voice_key,
            on_change=_remember, args=(voice_key, voice_key + "_draft"),
        )
    st.text_area(
        "正文", height=440, placeholder="输入或粘贴需要朗读的故事……", key=text_key,
        on_change=_remember, args=(text_key, text_key + "_draft"),
    )
    st.caption("固定使用 Qwen Flash · 原文朗读 · 中英文选项暂不影响生成")
    if st.button("开始生成", type="primary", disabled=busy, key=prefix + "start"):
        try:
            # Freeze the text/voice for the task; subsequent edits do not change a running job.
            jobs.start(owner, st.session_state[text_key], st.session_state[voice_key], _api_key())
            st.rerun()
        except (SpeechError, OSError) as exc:
            st.error(str(exc) if isinstance(exc, SpeechError) else "无法保存任务，请检查目录权限。")

    st.fragment(run_every=1 if busy else None)(_render_status)(owner, busy)


def _render_status(owner: str, watching: bool):
    jobs = speech_jobs()
    job = jobs.latest(owner)
    prefix = f"speech_{owner}_"
    if job is None:
        return
    if watching and job["status"] not in ACTIVE:
        st.rerun()
    st.divider()
    st.caption(f"最近任务：{VOICES.get(job['voice'], job['voice'])} · {job['created_at'][:19].replace('T', ' ')}")
    status = job["status"]
    if status in ACTIVE:
        completed, total = job["completed"], job["total"]
        label = f"已完成 {completed} / {total} 段"
        if completed == total:
            label += "，正在合并保存"
        st.progress(completed / total, text=label)
        st.caption(_stage_caption(job))
        if status == "cancelling":
            st.info("正在取消；当前网络请求结束后停止，已完成片段会保留。")
        elif st.button("取消生成", key=prefix + "cancel"):
            jobs.cancel(job["id"], owner)
            st.rerun()
        st.caption("可切换到其他页面，生成会继续；回到这里查看结果。")
    elif status == "completed":
        path = jobs.output / job["filename"]
        if path.is_file():
            st.success(f"已保存到 故事/{path.name}")
            st.audio(str(path), format="audio/wav")
            with path.open("rb") as audio:
                st.download_button("下载 WAV", audio, file_name=path.name,
                                   mime="audio/wav", key=prefix + "download", on_click="ignore")
        else:
            st.warning("音频已生成，但文件已被移动或删除。可重新生成。")
    else:
        (st.error if status == "failed" else st.info)(job["error"])
        st.caption(f"已完成 {job['completed']} / {job['total']} 段；继续时沿用该任务的原文和音色。")
        if st.button("继续生成（保留已完成片段）", key=prefix + "retry"):
            try:
                jobs.retry(job["id"], owner, _api_key())
                st.rerun()
            except (SpeechError, OSError) as exc:
                st.error(str(exc) if isinstance(exc, SpeechError) else "无法保存任务，请检查目录权限。")


def render():
    if not st.session_state.get("auth_ok") or st.session_state.get("auth_mode") == "guest":
        st.warning("请登录后使用语音生成，游客模式暂不开放。")
        return
    st.title("生成语音")
    try:
        configured = bool(_api_key())
    except SpeechError as exc:
        st.error(str(exc))
        configured = False
    if not configured:
        st.info("请先到 APIkey 页面填写 DashScope Key。")
        if st.button("前往 APIkey"):
            goto("modelSettings")
    owner = owner_key(str(st.session_state.get("user_id") or "1"))
    _render_generator(owner)
