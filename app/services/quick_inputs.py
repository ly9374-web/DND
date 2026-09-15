"""Editable text shortcuts, separated into ordinary and private prompt spaces."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import AppStorageKeys, settings
from app.models import PromptTextRecord, now_iso


_REMOVED_DEFAULT_IMAGE_CONTENTS = {
    "以第三人称视角描述一下当前场景，包括人物站位，人物形象，人物姿势，人物特征，人物是否有伤，人物之间的互动，环境有什么，场景是什么。其中每个角色在描述完这个角色的外貌后在“（）”中写入这个角色的名字。",
}


def _decode(key: str) -> list[PromptTextRecord]:
    raw = settings.get(key, "[]")
    items = json.loads(raw) if isinstance(raw, str) else raw
    return [PromptTextRecord.from_dict(item) for item in (items or [])]


def _encode(records: list[PromptTextRecord]) -> str:
    return json.dumps([record.to_dict() for record in records], ensure_ascii=False)


def _spaces() -> tuple[list[PromptTextRecord], list[PromptTextRecord]]:
    ordinary = _decode(AppStorageKeys.QUICK_INPUT_RECORDS)
    updates = {}
    if not settings.bool(AppStorageKeys.QUICK_INPUT_DEFAULT_SEEDED, False):
        defaults_path = Path(__file__).resolve().parents[1] / "default_prompts" / "quick_inputs.json"
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        names = {record.title for record in ordinary}
        ordinary = [
            PromptTextRecord(title=item["title"], content=item["content"])
            for item in defaults if item["title"] not in names
        ] + ordinary
        updates[AppStorageKeys.QUICK_INPUT_RECORDS] = _encode(ordinary)
        updates[AppStorageKeys.QUICK_INPUT_DEFAULT_SEEDED] = True
    if not settings.bool(AppStorageKeys.QUICK_INPUT_IMAGE_REMOVAL_MIGRATED, False):
        filtered = [
            record for record in ordinary
            if not (
                record.title.strip() == "图片"
                and record.content.strip() in _REMOVED_DEFAULT_IMAGE_CONTENTS
            )
        ]
        if len(filtered) != len(ordinary):
            ordinary = filtered
            updates[AppStorageKeys.QUICK_INPUT_RECORDS] = _encode(ordinary)
        updates[AppStorageKeys.QUICK_INPUT_IMAGE_REMOVAL_MIGRATED] = True
    if updates:
        settings.set_many_atomic(updates)
    return ordinary, _decode(AppStorageKeys.HIDDEN_QUICK_INPUT_RECORDS)


def load_quick_inputs(hidden_space: bool = False) -> list[PromptTextRecord]:
    ordinary, private = _spaces()
    return ordinary + private if hidden_space else ordinary


def quick_input_options(hidden_space: bool = False) -> list[dict[str, str]]:
    ordinary, private = _spaces()
    return [
        {"id": record.id, "title": record.title, "content": record.content, "space": space}
        for space, records in (("普通", ordinary), ("私密", private if hidden_space else []))
        for record in records
    ]


def save_quick_input(
    record_id: str, title: str, content: str, *, hidden_space: bool = False,
) -> PromptTextRecord:
    title = str(title or "").strip()
    content = str(content or "")
    if not title:
        raise ValueError("请填写快捷输入名称。")
    if not content.strip():
        raise ValueError("请填写快捷输入内容。")
    ordinary, private = _spaces()
    existing = next((record for record in ordinary + private if record.id == record_id), None)
    if record_id and existing is None:
        raise ValueError("该快捷输入已不存在，请重新选择。")
    if existing in private and not hidden_space:
        raise ValueError("无法访问该快捷输入。")
    # Editing an ordinary record from the private account retains its ownership.
    target = (private if existing in private else ordinary) if existing else (
        private if hidden_space else ordinary
    )
    if any(record.title == title and record.id != record_id for record in target):
        raise ValueError("同一空间内已有这个名称，请使用其他名称。")
    if existing is None:
        existing = PromptTextRecord(title=title, content=content)
        target.append(existing)
    else:
        existing.title = title
        existing.content = content
        existing.updated_at = now_iso()
    key = AppStorageKeys.HIDDEN_QUICK_INPUT_RECORDS if target is private else AppStorageKeys.QUICK_INPUT_RECORDS
    settings.set(key, _encode(target))
    return existing


def delete_quick_input(record_id: str, *, hidden_space: bool = False) -> None:
    ordinary, private = _spaces()
    if any(record.id == record_id for record in private) and not hidden_space:
        raise ValueError("无法访问该快捷输入。")
    for key, records in ((AppStorageKeys.QUICK_INPUT_RECORDS, ordinary),
                         (AppStorageKeys.HIDDEN_QUICK_INPUT_RECORDS, private)):
        remaining = [record for record in records if record.id != record_id]
        if len(remaining) != len(records):
            settings.set(key, _encode(remaining))
            return
