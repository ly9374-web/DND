"""Bounded, redacted application debug logs with a rolling 48-hour lifetime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

RETENTION_SECONDS = 48 * 60 * 60
_lock = threading.RLock()
_started: set[Path] = set()
_run_id = uuid.uuid4().hex[:12]
_filename = re.compile(r"debug-(\d+)-[a-f0-9]{12}\.jsonl\Z")
_data_url = re.compile(r"data:[\w.+/-]+;base64,[A-Za-z0-9+/=\s]+")
_long_base64 = re.compile(r"[A-Za-z0-9+/=]{256,}")
_secret_keys = {"authorization", "api_key", "apikey", "api_secret", "secret", "token", "access_token"}


def redact(value):
    if isinstance(value, dict):
        return {str(k): "[已隐藏]" if str(k).lower() in _secret_keys else
                "[图片数据已省略]" if str(k).lower() in {"b64_json", "image_data_base64", "source_image_data_base64"}
                else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, bytes):
        return f"[二进制 {len(value)} bytes]"
    if not isinstance(value, str):
        return value
    # Raw HTTP responses are often JSON containing bare base64 or credentials.
    if value.lstrip().startswith(("{", "[")):
        try:
            return redact(json.loads(value))
        except (ValueError, RecursionError):
            pass
    value = _data_url.sub("[Data URL 图片数据已省略]", value)
    value = _long_base64.sub("[长编码数据已省略]", value)
    value = re.sub(r"(?i)Bearer\s+[^\s\"',}]+", "Bearer [已隐藏]", value)
    value = re.sub(r"https?://[^\s\"'<>]+", lambda m: m[0].split("?", 1)[0] +
                   ("?[签名已省略]" if "?" in m[0] else ""), value)
    return value[:16000] + ("…[已截断]" if len(value) > 16000 else "")


def cleanup(directory: Path, *, now=None):
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    with _lock:
        for path in directory.glob("debug-*.jsonl"):
            match = _filename.fullmatch(path.name)
            if not match or path.is_symlink():
                continue
            bucket = int(match[1])
            if bucket + 60 <= cutoff:
                path.unlink(missing_ok=True)
            elif bucket <= cutoff:
                # Trim the boundary minute too: a newer line must not keep old logs alive.
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    continue
                kept = []
                for line in lines:
                    try:
                        if float(json.loads(line)["time"]) > cutoff:
                            kept.append(line)
                    except (ValueError, KeyError, TypeError):
                        continue
                if kept:
                    temporary = path.with_suffix(f".{os.getpid()}.tmp")
                    temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
                    temporary.replace(path)
                else:
                    path.unlink(missing_ok=True)


def format_message(*items):
    return " ".join(
        item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str)
        for item in (redact(item) for item in items)
    )[:20000]


def write(directory: Path, *items, message=None):
    # Logging must never turn a successful paid generation into a failed task.
    try:
        now = time.time()
        message = format_message(*items) if message is None else str(message)[:20000]
        with _lock:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"debug-{int(now // 60) * 60}-{_run_id}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"time": now, "message": message}, ensure_ascii=False) + "\n")
    except (OSError, ValueError, RecursionError):
        pass


def start_maintenance(directory: Path):
    directory = directory.resolve()
    with _lock:
        if directory in _started:
            return
        try:
            cleanup(directory)
        except OSError:
            pass
        _started.add(directory)

        def maintain():
            while True:
                time.sleep(60)
                try:
                    cleanup(directory)
                except OSError:
                    pass

        threading.Thread(target=maintain, daemon=True, name="log-maintenance").start()
