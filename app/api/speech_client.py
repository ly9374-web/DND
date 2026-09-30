"""Flash TTS transport, matching the Eassy-podcast request and WAV format."""
from __future__ import annotations

import io
import struct
import threading
import time
import wave
from collections.abc import Callable
from urllib.parse import urlparse

import requests

MODEL = "qwen-audio-3.0-tts-flash"
SYNTHESIS_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
MAX_AUDIO_BYTES = 100 * 1024 * 1024
VOICES = {
    f"{MODEL}-{suffix}": label for suffix, label in (
        ("loongivyhu", "Ivy Hu"), ("longfengxiuche", "龙凤岫澈"),
        ("longhexueling", "龙荷雪翎"), ("longhongweifeng", "龙鸿薇枫"),
        ("longhuiluling", "龙晦露凌"), ("longqingxiangju", "龙晴湘菊"),
        ("longshanzhuxin", "龙杉竹昕"), ("longxiniyan", "龙溪霓燕"),
        ("longxueyujun", "龙雪瑜珺"), ("longyuyaoluan", "龙瑶鸾"),
        ("longxuansongxing", "松杏"), ("loongadriangao", "英文男"),
        ("loongolivialin", "英文女"), ("longfengjinhe", "中文男"),
    )
}

# Qwen-Audio-TTS control tags documented for qwen-audio-3.0-tts-flash.
EMOTION_TAGS = {
    "": "无前缀（自然语气）",
    "[sad]": "悲伤",
    "[amazed]": "惊叹",
    "[deep and loud shouting]": "深沉大声呐喊",
    "[trembling]": "颤抖",
    "[angry]": "愤怒",
    "[excited]": "兴奋",
    "[sarcastic]": "讽刺",
    "[curious]": "好奇",
    "[like dracula]": "低沉阴森",
    "[bored]": "无聊",
    "[tired]": "疲惫",
    "[scornful]": "轻蔑",
    "[shouting]": "大喊",
    "[asmr]": "轻柔耳语",
    "[panicked]": "恐慌",
    "[mischievously]": "调皮",
    "[empathetic]": "共情",
    "[whispers]": "耳语",
    "[reluctantly]": "不情愿",
    "[crying]": "哭泣",
    "[serious]": "严肃",
    "[very slowly]": "非常缓慢",
    "[very fast]": "非常快速",
}


class SpeechError(RuntimeError):
    pass


class SpeechCancelled(Exception):
    pass


def check_cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise SpeechCancelled()


def validate_wav(audio: bytes) -> None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
                raise SpeechError("语音格式异常，需要 24kHz 单声道 WAV。")
            frames = wav.readframes(wav.getnframes())
            if not wav.getnframes() or len(frames) != wav.getnframes() * 2:
                raise SpeechError("语音片段为空或不完整，请重试。")
    except (wave.Error, EOFError) as exc:
        raise SpeechError("服务返回的语音不是有效的 WAV，请重试。") from exc


def normalize_wav(audio: bytes) -> bytes:
    # The reference service sometimes returns streaming RIFF/data size placeholders.
    if len(audio) >= 20 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        offset = 12
        while offset + 8 <= len(audio):
            size = struct.unpack_from("<I", audio, offset + 4)[0]
            if audio[offset:offset + 4] == b"data":
                fixed = bytearray(audio)
                struct.pack_into("<I", fixed, 4, len(audio) - 8)
                if size > len(audio) - offset - 8 or size == 0:
                    struct.pack_into("<I", fixed, offset + 4, len(audio) - offset - 8)
                audio = bytes(fixed)
                break
            offset += 8 + size + size % 2
    validate_wav(audio)
    return audio


ProgressCallback = Callable[[str, int, int], None]


def _request(session, method: str, url: str, cancel: threading.Event, *,
             phase: str, on_progress: ProgressCallback | None = None,
             read_audio: bool = False, **kwargs):
    label = "音频下载" if read_audio else "语音合成"
    for attempt in range(3):
        check_cancelled(cancel)
        if on_progress:
            on_progress(phase, attempt + 1, 0)
        response = None
        try:
            response = session.request(method, url, timeout=(10, 30 if read_audio else 120), **kwargs)
            check_cancelled(cancel)
            if response.status_code < 400:
                if not read_audio:
                    return response
                parts, size = [], 0
                started = last_report = time.monotonic()
                for part in response.iter_content(64 * 1024):
                    check_cancelled(cancel)
                    now = time.monotonic()
                    if now - started > 180:
                        raise requests.Timeout("audio download deadline exceeded")
                    size += len(part)
                    if size > MAX_AUDIO_BYTES:
                        raise SpeechError("单段音频过大，请缩短正文后重试。")
                    parts.append(part)
                    if on_progress and now - last_report >= 0.5:
                        on_progress(phase, attempt + 1, size)
                        last_report = now
                if on_progress:
                    on_progress(phase, attempt + 1, size)
                return normalize_wav(b"".join(parts))
            status = response.status_code
            if status in (401, 403):
                if read_audio:
                    raise SpeechError("音频下载地址已失效或无权访问，请重试。")
                raise SpeechError("DashScope Key 无效或没有此模型权限，请到 APIkey 页面检查。")
            if status != 429 and status < 500:
                raise SpeechError(f"{label}请求失败（HTTP {status}），请检查正文和账户配置。")
            if attempt == 2:
                raise SpeechError(f"{label}服务暂时不可用（HTTP {status}），请稍后重试。")
        except requests.RequestException as exc:
            check_cancelled(cancel)
            if attempt == 2:
                raise SpeechError(f"{label}连接超时或中断，已尝试 3 次，请检查网络后重试。") from exc
        finally:
            if response is not None and (read_audio or response.status_code >= 400 or cancel.is_set()):
                response.close()
        if cancel.wait(0.25 * 2 ** attempt):
            raise SpeechCancelled()
    raise SpeechError("语音请求失败。")


def synthesize(text: str, voice: str, api_key: str, cancel: threading.Event, *,
               on_progress: ProgressCallback | None = None) -> bytes:
    if voice not in VOICES:
        raise SpeechError("请选择有效的 Flash 音色。")
    # Match Eassy-podcast: ignore environment/macOS proxies and implicit netrc auth.
    with requests.Session() as session:
        session.trust_env = False
        return _synthesize(session, text, voice, api_key, cancel, on_progress)


def _synthesize(session, text, voice, api_key, cancel, on_progress):
    response = _request(
        session, "POST", SYNTHESIS_URL, cancel,
        phase="synthesizing", on_progress=on_progress,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": MODEL, "input": {
            "text": text, "voice": voice, "format": "wav", "sample_rate": 24000, "rate": 1.0,
        }},
        allow_redirects=False,
    )
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError()
        if int(payload.get("status_code", response.status_code)) != 200:
            raise SpeechError("DashScope 未完成合成，请检查账户余额、模型权限或正文内容后重试。")
        url = payload["output"]["audio"]["url"]
        if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise SpeechError("语音服务未返回有效的音频地址，请重试。") from exc
    finally:
        response.close()
    # Download separately; never forward the API key to the audio host.
    return _request(session, "GET", url, cancel, stream=True,
                    phase="downloading", on_progress=on_progress, read_audio=True)
