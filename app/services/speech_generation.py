"""Durable, resumable speech jobs. Worker threads never access Streamlit state."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
import wave
from datetime import datetime
from pathlib import Path

from app.api.speech_client import (
    VOICES, SpeechCancelled, SpeechError, check_cancelled, synthesize, validate_wav,
)

ACTIVE = {"running", "cancelling"}


def split_text(script: str) -> list[str]:
    """Eassy-podcast Flash policy: first newline, then last punctuation, cap 1300."""
    chunks = []
    start = 0
    while start < len(script):
        hard_end = min(start + 1300, len(script))
        newline = next((i for i in range(start + 1, hard_end)
                        if script[i] in "\r\n" and script[start:i + 1].strip()), None)
        if newline is not None:
            end = newline + 1
            if script[newline] == "\r" and end < len(script) and script[end] == "\n":
                end += 1
        elif hard_end == len(script):
            end = hard_end
        else:
            punctuation = next((i for i in range(hard_end - 1, start - 1, -1)
                                if script[i] in "。！？.!?"), None)
            end = punctuation + 1 if punctuation is not None else hard_end
        chunks.append(script[start:end])
        start = end
    return chunks


def owner_key(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:24]


class SpeechJobs:
    def __init__(self, root: Path, output: Path, synthesizer=synthesize):
        self.root, self.output = Path(root), Path(output)
        self.synthesizer = synthesizer
        self.lock = threading.RLock()
        self.running: dict[str, threading.Event] = {}

    def _directory(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise SpeechError("任务编号无效。")
        return self.root / job_id

    def _read(self, job_id: str, owner: str) -> dict:
        job = json.loads((self._directory(job_id) / "job.json").read_text(encoding="utf-8"))
        if job["owner"] != owner:
            raise SpeechError("无法访问此语音任务。")
        if job["status"] in ACTIVE and job_id not in self.running:
            job.update(status="interrupted", error="应用上次运行中断，可继续生成。")
            self._save(job)
        return job

    def _save(self, job: dict) -> None:
        directory = self._directory(job["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "job.json.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, directory / "job.json")

    def get(self, job_id: str, owner: str) -> dict:
        with self.lock:
            return self._read(job_id, owner)

    def latest(self, owner: str) -> dict | None:
        with self.lock:
            jobs = []
            for path in self.root.glob("*/job.json"):
                try:
                    job = self._read(path.parent.name, owner)
                    jobs.append(job)
                except (OSError, ValueError, KeyError, SpeechError):
                    continue
            return max(jobs, key=lambda job: job["created_at"], default=None)

    def _ensure_available(self, owner: str) -> None:
        for job_id in self.running:
            job = json.loads((self._directory(job_id) / "job.json").read_text(encoding="utf-8"))
            if job["owner"] == owner:
                raise SpeechError("已有语音任务正在运行，请等待完成或取消。")

    @staticmethod
    def _validate_key(api_key: str) -> str:
        api_key = api_key.strip()
        if not api_key or not api_key.isascii() or any(c.isspace() for c in api_key):
            raise SpeechError("请先在 APIkey 页面填写有效的 DashScope Key。")
        return api_key

    def start(self, owner: str, text: str, voice: str, api_key: str) -> str:
        api_key = self._validate_key(api_key)
        text = text.strip()
        if not text:
            raise SpeechError("请输入需要朗读的正文。")
        if voice not in VOICES:
            raise SpeechError("请选择有效的 Flash 音色。")
        with self.lock:
            self._ensure_available(owner)
            job_id = uuid.uuid4().hex
            now = datetime.now()
            job = {"id": job_id, "owner": owner, "text": text, "voice": voice,
                   "created_at": now.isoformat(), "status": "pending", "completed": 0,
                   "total": len(split_text(text)), "error": "",
                   "filename": f"{now:%Y%m%d-%H%M%S}_{VOICES[voice]}_{job_id[:8]}.wav"}
            self._save(job)
            self._launch(job, api_key)
            return job_id

    def retry(self, job_id: str, owner: str, api_key: str) -> None:
        api_key = self._validate_key(api_key)
        with self.lock:
            self._ensure_available(owner)
            job = self._read(job_id, owner)
            if job["status"] not in {"failed", "cancelled", "interrupted"}:
                raise SpeechError("此任务不需要重试。")
            self._launch(job, api_key)

    def _launch(self, job: dict, api_key: str) -> None:
        cancel = threading.Event()
        self.running[job["id"]] = cancel
        now = datetime.now().isoformat()
        job.update(status="running", error="", started_at=now, phase="preparing",
                   phase_started_at=now, attempt=1, downloaded_bytes=0)
        thread = threading.Thread(target=self._work, args=(job, api_key, cancel), daemon=True)
        try:
            self._save(job)
            thread.start()
        except (OSError, RuntimeError):
            self.running.pop(job["id"], None)
            job.update(status="failed", error="无法启动后台任务，请重试。")
            try:
                self._save(job)
            except OSError:
                pass
            raise SpeechError(job["error"])

    def cancel(self, job_id: str, owner: str) -> None:
        with self.lock:
            job = self._read(job_id, owner)
            event = self.running.get(job_id)
            if event is not None:
                event.set()
                job["status"] = "cancelling"
                self._save(job)

    def _progress(self, job: dict, cancel: threading.Event) -> None:
        with self.lock:
            job["status"] = "cancelling" if cancel.is_set() else "running"
            self._save(job)

    def _stage(self, job: dict, cancel: threading.Event, phase: str,
               attempt: int = 1, downloaded_bytes: int = 0) -> None:
        with self.lock:
            if (job.get("phase"), job.get("attempt")) != (phase, attempt):
                job["phase_started_at"] = datetime.now().isoformat()
            job.update(phase=phase, attempt=attempt, downloaded_bytes=downloaded_bytes)
            self._progress(job, cancel)

    def _work(self, job: dict, api_key: str, cancel: threading.Event) -> None:
        directory = self._directory(job["id"])
        chunks_dir = directory / "chunks"
        temporary = self.output / (job["filename"] + ".tmp")
        try:
            chunks_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            for index, text in enumerate(split_text(job["text"])):
                check_cancelled(cancel)
                job["current_chunk"] = index + 1
                self._stage(job, cancel, "preparing")
                path = chunks_dir / f"{index:06d}.wav"
                cached = False
                if path.exists():
                    try:
                        validate_wav(path.read_bytes())
                        cached = True
                    except (OSError, SpeechError):
                        pass
                if not cached:
                    self._stage(job, cancel, "synthesizing")
                    audio = self.synthesizer(
                        text, job["voice"], api_key, cancel,
                        on_progress=lambda phase, attempt, size: self._stage(
                            job, cancel, phase, attempt, size),
                    )
                    self._stage(job, cancel, "saving")
                    validate_wav(audio)
                    partial = path.with_suffix(".tmp")
                    partial.write_bytes(audio)
                    os.replace(partial, path)
                paths.append(path)
                job["completed"] = index + 1
                self._progress(job, cancel)
            check_cancelled(cancel)
            self._stage(job, cancel, "merging")
            self.output.mkdir(parents=True, exist_ok=True)
            # Stream frames into a single WAV; do not repeatedly rebuild all prior chunks.
            with wave.open(str(temporary), "wb") as merged:
                merged.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                for index, path in enumerate(paths):
                    check_cancelled(cancel)
                    if index:
                        merged.writeframesraw(b"\0" * (14400 * 2))  # Flash: 0.6 seconds.
                    with wave.open(str(path), "rb") as source:
                        while frames := source.readframes(24000):
                            check_cancelled(cancel)
                            merged.writeframesraw(frames)
            # Cancellation and final publication share a lock to avoid a completion race.
            with self.lock:
                check_cancelled(cancel)
                os.replace(temporary, self.output / job["filename"])
                job.update(status="completed", error="")
                self._save(job)
                self.running.pop(job["id"], None)
            shutil.rmtree(chunks_dir, ignore_errors=True)
        except SpeechCancelled:
            job.update(status="cancelled", error="已取消，已完成片段已保留，可继续生成。")
        except SpeechError as exc:
            job.update(status="failed", error=str(exc))
        except OSError:
            job.update(status="failed", error="文件读写失败，请检查磁盘空间和目录权限后重试。")
        except Exception:
            # Do not persist raw transport errors: they can contain URLs or credentials.
            job.update(status="failed", error="语音生成遇到异常，已完成片段已保留，请重试。")
        finally:
            with self.lock:
                self.running.pop(job["id"], None)
                self._save(job)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
