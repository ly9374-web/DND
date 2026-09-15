"""Durable recovery storage for prompt editor drafts.

Browser localStorage is the first line of defence because it can capture every
keystroke without waiting for a Streamlit rerun.  This store is the second line:
the browser bridge mirrors drafts here on blur and before guarded actions so a
server rerun/restart cannot silently discard an acknowledged draft.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from app.config import DATA_DIR, debug_log


DRAFTS_PATH = DATA_DIR / "PromptDrafts" / "drafts.json"
_LOCK = threading.RLock()


def _load() -> dict[str, dict]:
    if not DRAFTS_PATH.exists():
        return {}
    try:
        value = json.loads(DRAFTS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        debug_log("[PromptDrafts] load failed:", exc)
        corrupt = DRAFTS_PATH.with_name(
            f"{DRAFTS_PATH.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        try:
            os.replace(DRAFTS_PATH, corrupt)
        except Exception as move_exc:
            debug_log("[PromptDrafts] preserving corrupt file failed:", move_exc)
            return {}
        backup = DRAFTS_PATH.with_name(f"{DRAFTS_PATH.stem}.last-good.json")
        try:
            value = json.loads(backup.read_text(encoding="utf-8"))
        except Exception as backup_exc:
            debug_log("[PromptDrafts] backup recovery failed:", backup_exc)
            return {}
    return value if isinstance(value, dict) else {}


def _persist(entries: dict[str, dict]) -> None:
    DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DRAFTS_PATH.with_name(f".{DRAFTS_PATH.name}.tmp")
    backup = DRAFTS_PATH.with_name(f"{DRAFTS_PATH.stem}.last-good.json")
    payload = json.dumps(entries, ensure_ascii=False, indent=2)
    try:
        temporary.write_text(payload, encoding="utf-8")
        if DRAFTS_PATH.exists():
            try:
                backup.write_bytes(DRAFTS_PATH.read_bytes())
            except Exception as exc:
                debug_log("[PromptDrafts] backup failed:", exc)
        os.replace(temporary, DRAFTS_PATH)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def scoped_entries(scope: str) -> dict[str, dict]:
    prefix = str(scope or "") + ":"
    with _LOCK:
        return {
            key[len(prefix) :]: dict(value)
            for key, value in _load().items()
            if key.startswith(prefix) and isinstance(value, dict)
        }


def apply_browser_snapshot(
    scope: str,
    drafts: list[dict] | None,
    cleared: list[str] | None = None,
) -> None:
    scope = str(scope or "")
    if not scope:
        return
    with _LOCK:
        entries = _load()
        changed = False
        cleared_keys = {str(key) for key in cleared or []}
        for widget_key in cleared_keys:
            storage_key = f"{scope}:{str(widget_key or '')}"
            if storage_key in entries:
                entries.pop(storage_key, None)
                changed = True
        for item in drafts or []:
            if not isinstance(item, dict):
                continue
            widget_key = str(item.get("widget_key") or "")
            if not widget_key or widget_key in cleared_keys:
                continue
            entries[f"{scope}:{widget_key}"] = {
                "widget_key": widget_key,
                "value": str(item.get("value") or ""),
                "baseline": str(item.get("baseline") or ""),
                "label": str(item.get("label") or widget_key),
                "accepted": bool(item.get("accepted", False)),
                "updated_at": float(item.get("updated_at") or time.time()),
            }
            changed = True
        if changed:
            _persist(entries)


def clear_scope(scope: str) -> None:
    prefix = str(scope or "") + ":"
    with _LOCK:
        entries = _load()
        kept = {key: value for key, value in entries.items() if not key.startswith(prefix)}
        if len(kept) != len(entries):
            _persist(kept)
