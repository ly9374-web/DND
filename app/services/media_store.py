"""Local media lifetime, independent of chat records and Streamlit sessions.

The small per-media manifests are also tombstones: expired/deleted IDs must not
be downloaded again when an old chat or another session is loaded.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
import hashlib
import filecmp
import io
import json
import os
from pathlib import Path
import queue
import stat
import threading
import time
import uuid

from PIL import Image

from app.api.media_clients import (
    MediaExpiredError, download_image_bytes, download_video_bytes,
    is_openrouter_video_url,
)
from app.config import CHAT_RECORDS_DIR, DATA_DIR, OpenRouterConfig, debug_log
from app.models import GeneratedImageRecord

LEGACY_MEDIA_DIR = DATA_DIR / "Media"
# Keep the default project media beside .python_app_data. Custom data roots
# retain their own media so independent installs/tests cannot share a cache.
MEDIA_DIR = Path(os.environ.get("VS_IMAGE_NOVEL_MEDIA_DIR") or (
    DATA_DIR.parent / ".media" if DATA_DIR.name == ".python_app_data" else DATA_DIR / ".media"
)).expanduser().resolve()
RETENTION_SECONDS = 48 * 60 * 60
# Temporary media and saved media use separate folders. Saved images also carry
# Finder's hidden flag, so their visibility does not depend on their folder.
_TEMPORARY_CATEGORY = ".临时"
_SAVED_CATEGORY = ".保存"
_IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".heic", ".avif", ".bmp", ".tif", ".tiff",
}
_CATEGORY_TARGETS = {"临时": _TEMPORARY_CATEGORY, "已保存": _SAVED_CATEGORY}
_lock = threading.RLock()
_pending: set[str] = set()
_jobs: queue.Queue = queue.Queue()
_workers_started = False
_maintenance_started = False
_revision = 0


def _is_dataless(path: Path) -> bool:
    """Do not block a Streamlit run while iCloud downloads an evicted file."""
    return bool(getattr(path.stat(), "st_flags", 0) & 0x40000000)


def migrate_media_directory():
    """Move bytes and lifetime manifests together; never reset their deadlines."""
    with _lock:
        old = LEGACY_MEDIA_DIR
        if old.is_symlink():
            if old.resolve() != MEDIA_DIR.resolve():
                raise ValueError("旧媒体目录指向其他位置，请先检查路径配置。")
            return
        if not old.exists() or old.resolve() == MEDIA_DIR.resolve():
            return
        MEDIA_DIR.parent.mkdir(parents=True, exist_ok=True)
        if not MEDIA_DIR.exists():
            old.rename(MEDIA_DIR)
        else:
            files = [path for path in old.rglob("*") if path.is_file()]
            # Check all conflicts before moving anything. Do not overwrite an
            # independently saved file or a newer permanent/expiry manifest.
            for path in files:
                destination = MEDIA_DIR / path.relative_to(old)
                if destination.exists() and not filecmp.cmp(path, destination, shallow=False):
                    raise ValueError("新旧媒体目录存在同名但内容不同的文件，已停止迁移。")
            for path in files:
                destination = MEDIA_DIR / path.relative_to(old)
                destination.parent.mkdir(parents=True, exist_ok=True)
                path.replace(destination)
            for directory in sorted((p for p in old.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                directory.rmdir()
            old.rmdir()
        # A relative compatibility link lets already-running old download
        # workers finish into the new folder instead of creating a second copy.
        if os.name == "posix":
            old.symlink_to(os.path.relpath(MEDIA_DIR, old.parent), target_is_directory=True)


def _files_root():
    """Images and videos live beside .media (e.g. the project root)."""
    return MEDIA_DIR.parent


def _saved_image(meta):
    return meta.get("saved") and meta.get("kind") == "image"


def _hide_image(path):
    """Hide an image in Finder without changing its name or location."""
    if hasattr(os, "chflags") and hasattr(stat, "UF_HIDDEN"):
        current = path.stat().st_flags
        if not current & stat.UF_HIDDEN:
            os.chflags(path, current | stat.UF_HIDDEN)


def hide_project_images():
    """Hide images dropped into DND or its saved folder while the app runs."""
    for folder in (_files_root(), _files_root() / _SAVED_CATEGORY):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_symlink() and path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
                try:
                    _hide_image(path)
                except OSError as exc:
                    debug_log("[MediaStore] Could not hide image:", path.name, type(exc).__name__)


def _layout_target(relative, meta):
    """Map historical paths to the current media folders."""
    parts = Path(relative).parts
    if _saved_image(meta):
        return str(Path(_SAVED_CATEGORY) / parts[-1])
    first = parts[0] if parts else ""
    if first in (_TEMPORARY_CATEGORY, _SAVED_CATEGORY):
        category = first
    else:
        category = _CATEGORY_TARGETS.get(first, _SAVED_CATEGORY)
    return str(Path(category) / parts[-1])


def migrate_media_layout():
    """Keep saved media in its folder and update manifests after moving it."""
    with _lock:
        # Earlier revisions kept visible 临时/保存 folders beside .media.
        for old, new in (("临时", _TEMPORARY_CATEGORY), ("保存", _SAVED_CATEGORY)):
            source = _files_root() / old
            if not source.is_dir():
                continue
            destination = _files_root() / new
            destination.mkdir(parents=True, exist_ok=True)
            for path in [p for p in source.iterdir() if p.is_file()]:
                target = destination / path.name
                if target.exists():
                    if not filecmp.cmp(path, target, shallow=False):
                        raise ValueError("新旧媒体目录存在同名但内容不同的文件，已停止迁移。")
                    path.unlink()
                else:
                    path.replace(target)
            try:
                source.rmdir()
            except OSError:
                pass
        # Manifest-driven moves keep every record pointing at its bytes.
        for manifest in list((MEDIA_DIR / ".index").glob("*.json")):
            try:
                if _is_dataless(manifest):
                    continue
                meta = json.loads(manifest.read_text(encoding="utf-8"))
                relative = meta.get("path")
                if not relative:
                    continue
                target_relative = _layout_target(relative, meta)
                destination = _files_root() / target_relative
                candidates = (_files_root() / relative, MEDIA_DIR / relative)
                for source in candidates:
                    if source == destination or not source.is_file():
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        if not filecmp.cmp(source, destination, shallow=False):
                            raise ValueError("新旧媒体目录存在同名但内容不同的文件，已停止迁移。")
                        source.unlink()
                    else:
                        source.replace(destination)
                if not destination.exists():
                    continue
                if _saved_image(meta):
                    _hide_image(destination)
                if target_relative != relative:
                    _write({**meta, "path": target_relative})
            except ValueError:
                raise
            except Exception as exc:
                debug_log("[MediaStore] Layout manifest failed:", type(exc).__name__)
        # Crash debris inside the old category folders has no manifest.
        for category, target in _CATEGORY_TARGETS.items():
            base = MEDIA_DIR / category
            if not base.is_dir():
                continue
            for path in [p for p in base.rglob("*") if p.is_file()]:
                if path.name.startswith(".") or path.name.endswith(".tmp"):
                    path.unlink()  # Finder metadata or an interrupted write.
                    continue
                destination = _files_root() / target / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if not filecmp.cmp(path, destination, shallow=False):
                        raise ValueError("新旧媒体目录存在同名但内容不同的文件，已停止迁移。")
                    path.unlink()
                else:
                    path.replace(destination)
                if category == "已保存" and destination.suffix.lower() in _IMAGE_SUFFIXES:
                    _hide_image(destination)
        # Flat files historically stored directly inside .media never auto-expire.
        for path in [p for p in MEDIA_DIR.iterdir() if p.is_file() and not p.name.startswith(".")]:
            destination = _files_root() / _SAVED_CATEGORY / path.name
            if destination.exists():
                if not filecmp.cmp(path, destination, shallow=False):
                    raise ValueError("新旧媒体目录存在同名但内容不同的文件，已停止迁移。")
                path.unlink()
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                path.replace(destination)
            if destination.suffix.lower() in _IMAGE_SUFFIXES:
                _hide_image(destination)
        for category in _CATEGORY_TARGETS:
            base = MEDIA_DIR / category
            if base.is_dir():
                for directory in sorted((p for p in base.rglob("*") if p.is_dir()),
                                        key=lambda p: len(p.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                try:
                    base.rmdir()
                except OSError:
                    pass


def _key(record_id):
    return hashlib.sha256(str(record_id).encode()).hexdigest()


def _manifest_path(record_id):
    return MEDIA_DIR / ".index" / (_key(record_id) + ".json")


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(record_id):
    path = _manifest_path(record_id)
    if not path.exists():
        return {}
    if _is_dataless(path):
        raise OSError("媒体索引仍在 iCloud 中，等待下载完成后重试。")
    # Do not overwrite damaged manifests: that could reset a lifetime or lose
    # the permanent flag. Surface the failure for repair instead.
    return json.loads(path.read_text(encoding="utf-8"))


def _write(meta):
    global _revision
    _atomic_write(_manifest_path(meta["id"]), json.dumps(meta, ensure_ascii=False).encode())
    _revision += 1


def revision():
    return _revision


def _path(meta):
    relative = meta.get("path")
    if not relative:
        return None
    path = (_files_root() / relative).resolve()
    if not path.is_relative_to(_files_root().resolve()):
        raise ValueError("媒体文件路径不在存储目录内。")
    return path


def info(record):
    """A cheap local lookup; never contacts the provider on a UI render.

    Raises OSError when the on-disk manifest is still in iCloud or damaged, so
    render paths should use available()/local_path(), which degrade instead.
    """
    with _lock:
        meta = _read(record.id)
        if meta.get("status") == "ready":
            if not meta.get("saved") and time.time() >= meta["expires_at"]:
                return {**meta, "status": "expired"}
            path = _path(meta)
            if not path or not path.is_file():
                return {**meta, "status": "missing"}
        return meta


def available(record):
    try:
        meta = info(record)
    except OSError:
        # Manifest evicted to iCloud: unavailable for now, but never a crash.
        return False
    return meta.get("status") not in {"expired", "deleted", "missing"}


def local_path(record):
    try:
        meta = info(record)
    except OSError:
        return None
    return _path(meta) if meta.get("status") == "ready" else None


def read_bytes(record):
    with _lock:
        path = local_path(record)
        if path is None:
            raise MediaExpiredError("本地媒体已过期或不存在。")
        return path.read_bytes()


def _image_metadata(record):
    # The library survives chat deletion, without embedding image bytes or refs.
    return {name: getattr(record, name) for name in (
        "id", "provider", "prompt", "created_at", "source_turn_id",
        "previous_source_turn_id", "video_prompt", "video_story_text",
    )}


def remember_image(record):
    """Persist editable library metadata without changing the file's lifetime."""
    if getattr(record.media_kind, "value", record.media_kind) != "image":
        return
    with _lock:
        meta = info(record)
        if meta.get("status") == "ready" and meta.get("record") != _image_metadata(record):
            _write({**meta, "record": _image_metadata(record)})


def list_images():
    """All live project images, including files placed directly in media folders."""
    with _lock:
        manifests, known_paths = [], set()
        deferred_manifests = False
        for path in (MEDIA_DIR / ".index").glob("*.json"):
            try:
                if _is_dataless(path):
                    deferred_manifests = True
                    continue
                meta = json.loads(path.read_text(encoding="utf-8"))
                if meta.get("path"):
                    known_paths.add(_path(meta))  # Include tombstones; never resurrect them.
                manifests.append(meta)
            except (OSError, ValueError, TypeError):
                continue
        # A deferred manifest may be a deletion tombstone. Do not rediscover
        # loose files until its contents are available again.
        for category in (() if deferred_manifests else (_TEMPORARY_CATEGORY, _SAVED_CATEGORY, "")):
            for path in (_files_root() / category).glob("*"):
                if (path.is_symlink() or not path.is_file() or path.resolve() in known_paths
                        or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}):
                    continue
                try:
                    with Image.open(path) as image:
                        image.verify()
                    relative = str(path.relative_to(_files_root()))
                    modified = path.stat().st_mtime
                    saved = category in (_SAVED_CATEGORY, "")
                    if saved:
                        _hide_image(path)
                    meta = {"id": "local-" + _key(relative), "kind": "image", "status": "ready",
                            "path": relative, "saved": saved, "cached_at": modified,
                            "expires_at": None if saved else modified + RETENTION_SECONDS}
                    _write(meta)
                    manifests.append(meta)
                except (OSError, ValueError):
                    continue
        images = []
        for meta in manifests:
            if meta.get("kind") != "image" or meta.get("status") != "ready":
                continue
            record = GeneratedImageRecord.from_dict({
                "provider": "local", "prompt": Path(meta.get("path", "图片")).stem,
                "created_at": meta.get("source_created_at") or datetime.fromtimestamp(meta.get("cached_at", 0)).isoformat(),
                **meta.get("record", {}), "id": meta["id"], "media_managed": True,
                "image_url_string": meta.get("source_url"),
            })
            if local_path(record):
                images.append(record)
        return sorted(images, key=lambda record: record.created_at, reverse=True)


def _promote(meta):
    source = _path(meta)
    destination = _files_root() / _SAVED_CATEGORY / source.name
    # Write the durable copy first, then commit its manifest before removing
    # the temporary copy. Saving and cleanup use the same lock.
    _atomic_write(destination, source.read_bytes())
    if meta.get("kind") == "image":
        _hide_image(destination)
    meta = {**meta, "saved": True, "expires_at": None,
            "path": str(destination.relative_to(_files_root()))}
    _write(meta)
    if source != destination:
        source.unlink(missing_ok=True)
    return meta


def cache_record(record, *, permanent=False):
    """Download outside the lock; commit only if no deletion/expiry won the race."""
    with _lock:
        meta = info(record)
        if meta.get("status") in {"expired", "deleted", "missing"}:
            raise MediaExpiredError("素材已过期或已删除，无法保存。")
        if meta.get("status") == "ready":
            return _promote(meta) if permanent and not meta.get("saved") else meta

    kind = getattr(record.media_kind, "value", record.media_kind)
    if kind == "image":
        if record.image_data_base64:
            data = base64.b64decode(record.image_data_base64.split(",")[-1])
        elif record.image_url_string:
            data = download_image_bytes(record.image_url_string)
        else:
            raise MediaExpiredError("图片没有可用内容。")
        with Image.open(io.BytesIO(data)) as image:
            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(image.format, ".png")
            image.verify()
    else:
        if not record.video_url_string:
            raise MediaExpiredError("视频没有可用地址。")
        key = OpenRouterConfig.api_key() if is_openrouter_video_url(record.video_url_string) else ""
        data = download_video_bytes(record.video_url_string, api_key=key)
        suffix = ".mp4"
    # Older embedded images were the old Save mechanism. Fresh base64 API
    # results explicitly set media_managed and receive the same 48-hour TTL.
    saved = permanent or bool(record.image_data_base64 and not record.media_managed)
    with _lock:
        current = info(record)
        if current.get("status") in {"expired", "deleted", "missing"}:
            raise MediaExpiredError("素材已过期或已删除。")
        if current.get("status") == "ready":
            return _promote(current) if saved and not current.get("saved") else current
        now = time.time()
        filename = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S") + "-" + _key(record.id)[:16] + suffix
        path = _files_root() / (_SAVED_CATEGORY if saved else _TEMPORARY_CATEGORY) / filename
        _atomic_write(path, data)
        if saved and kind == "image":
            _hide_image(path)
        meta = {"id": record.id, "kind": kind, "status": "ready", "saved": saved,
                "cached_at": now, "expires_at": None if saved else now + RETENTION_SECONDS,
                "path": str(path.relative_to(_files_root()))}
        if kind == "image":
            meta.update(source_url=record.image_url_string, source_created_at=record.created_at,
                        record=_image_metadata(record))
        try:
            _write(meta)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return meta


def _attempt(record):
    try:
        return cache_record(record)
    except Exception as exc:
        with _lock:
            current = info(record)
            if current.get("status") not in {"ready", "deleted", "expired", "missing"}:
                _write({"id": record.id,
                        "status": "expired" if isinstance(exc, MediaExpiredError) else "retry",
                        "retry_after": time.time() + 300})
        # Do not persist signed URLs, provider response bodies, or API keys.
        debug_log("[MediaStore] Cache failed:", type(exc).__name__)
        return None


def cache_generated(record):
    record.media_managed = True
    try:
        meta = _attempt(record)
        if meta and record.image_data_base64:
            record.image_data_base64 = None
    except Exception as exc:
        # A disk problem must not cause a paid generation to be submitted again.
        debug_log("[MediaStore] Local storage failed:", type(exc).__name__)
    return record


def save(record):
    return cache_record(record, permanent=True)


def import_image(data: bytes, name: str, *, permanent=False):
    """Import a local image without contacting a cloud service or persisting base64."""
    from app.image_inputs import image_mime
    if not data or len(data) > 32 * 1024 * 1024:
        raise ValueError("导入图片大小应在 0–32 MB 之间。")
    image_mime(data)
    record = GeneratedImageRecord(provider="local", prompt=Path(name).stem or "本地图片",
                                  image_data_base64=base64.b64encode(data).decode("ascii"),
                                  media_managed=True)
    cache_record(record, permanent=permanent)
    record.image_data_base64 = None
    return record


def delete(record):
    with _lock:
        meta = _read(record.id)
        path = _path(meta)
        _write({**meta, "id": record.id, "status": "deleted"})
        if path:
            path.unlink(missing_ok=True)


def cleanup():
    """Clean all media, including files whose chat has already been deleted."""
    with _lock:
        for manifest in (MEDIA_DIR / ".index").glob("*.json"):
            try:
                if _is_dataless(manifest):
                    continue
                meta = json.loads(manifest.read_text(encoding="utf-8"))
                expired = (meta.get("status") == "ready" and not meta.get("saved")
                           and time.time() >= meta["expires_at"])
                if expired:
                    meta["status"] = "expired"
                    _write(meta)
                if meta.get("status") in {"expired", "deleted"}:
                    path = _path(meta)
                    if path:
                        path.unlink(missing_ok=True)
            except Exception as exc:
                debug_log("[MediaStore] Cleanup failed:", type(exc).__name__)


def _download_worker():
    while True:
        record = _jobs.get()
        try:
            _attempt(record)
        except Exception as exc:
            debug_log("[MediaStore] Migration failed:", type(exc).__name__)
        finally:
            with _lock:
                _pending.discard(record.id)
            _jobs.task_done()


def request_cache(records):
    global _workers_started
    with _lock:
        if not _workers_started:
            for _ in range(2):
                threading.Thread(target=_download_worker, daemon=True, name="media-download").start()
            _workers_started = True
        for record in records:
            try:
                meta = info(record)
            except OSError:
                # Manifest still in iCloud; a later render retries once it lands.
                continue
            if meta.get("status") in {"ready", "expired", "deleted", "missing"}:
                continue
            if record.id not in _pending and time.time() >= meta.get("retry_after", 0):
                _pending.add(record.id)
                _jobs.put(deepcopy(record))


def migrate_existing():
    for path in CHAT_RECORDS_DIR.rglob("record-*.json"):
        try:
            # macOS may evict iCloud-backed chats. Opening a dataless file can
            # block indefinitely offline; retry once it is downloaded locally.
            if _is_dataless(path):
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            request_cache([GeneratedImageRecord.from_dict(item) for item in
                           (data.get("generated_images") or data.get("generatedImages") or [])])
        except Exception as exc:
            debug_log("[MediaStore] Read old media failed:", type(exc).__name__)


def preserve_legacy_saved(record):
    """Must succeed before deleting a chat containing old embedded saved images."""
    for item in record.generated_images:
        if item.image_data_base64 and not item.media_managed and available(item):
            save(item)


def _maintain():
    while True:
        try:
            cleanup()
            hide_project_images()
            from app.services import temporary_uploads
            temporary_uploads.cleanup()
        except Exception as exc:
            debug_log("[MediaStore] Maintenance failed:", type(exc).__name__)
        time.sleep(60)


def _migrate_loop():
    while True:
        try:
            migrate_existing()
        except Exception as exc:
            debug_log("[MediaStore] Migration scan failed:", type(exc).__name__)
        time.sleep(60)


def start_maintenance():
    global _maintenance_started
    with _lock:
        if not _maintenance_started:
            migrate_media_directory()
            migrate_media_layout()
            cleanup()
            hide_project_images()
            for category in (_TEMPORARY_CATEGORY, _SAVED_CATEGORY):
                (_files_root() / category).mkdir(parents=True, exist_ok=True)
            threading.Thread(target=_maintain, daemon=True, name="media-maintenance").start()
            threading.Thread(target=_migrate_loop, daemon=True, name="media-migration").start()
            _maintenance_started = True
