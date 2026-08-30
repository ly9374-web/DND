from __future__ import annotations

import json
from pathlib import Path

from app.config import AppStorageKeys, settings
from app.models import FixedTemplateRecord, PromptTextRecord, SystemPromptRecord


PUBLIC_ASSETS_PATH = (
    Path(__file__).resolve().parents[2]
    / "public_prompt_assets"
    / "naruto_public.json"
)
PUBLIC_ASSET_SEED_VERSION = 1
REMOVED_BUILTIN_PROMPT_TITLES = {"小说生成", "无prompt"}


def _decode_records(key: str) -> list[dict]:
    raw = settings.get(key, "")
    try:
        decoded = raw if isinstance(raw, list) else json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = []
    return [item for item in decoded if isinstance(item, dict)]


def _encode_records(records) -> str:
    return json.dumps([record.to_dict() for record in records], ensure_ascii=False)


def _unique_public_record(records: list[dict], title: str, label: str) -> dict:
    matches = [item for item in records if str(item.get("title", "")).strip() == title]
    if len(matches) != 1:
        raise RuntimeError(
            f'公开种子数据中的{label}“{title}”应当恰好存在一条，实际为 {len(matches)} 条。'
        )
    return matches[0]


def _upsert_text_record(
    records: list[PromptTextRecord],
    public_records: list[dict],
    title: str,
    label: str,
) -> PromptTextRecord:
    existing = next(
        (item for item in records if str(item.title or "").strip() == title), None
    )
    if existing is not None:
        return existing
    source = _unique_public_record(public_records, title, label)
    record = PromptTextRecord(title=title, content=str(source.get("content", "")))
    records.append(record)
    return record


def _upsert_template(
    records: list[FixedTemplateRecord], public_records: list[dict], title: str
) -> FixedTemplateRecord:
    existing = next(
        (item for item in records if str(item.title or "").strip() == title), None
    )
    if existing is not None:
        return existing
    source = _unique_public_record(public_records, title, "固定模板")
    record = FixedTemplateRecord(
        title=title,
        background_intro=str(source.get("background_intro", "")),
        dm_core_tasks=str(source.get("dm_core_tasks", "")),
        response_format=str(source.get("response_format", "")),
    )
    records.append(record)
    return record


def _upsert_final_setting(
    records: list[SystemPromptRecord], public_records: list[dict], title: str
) -> SystemPromptRecord:
    existing = next(
        (item for item in records if str(item.title or "").strip() == title), None
    )
    if existing is not None:
        return existing
    source = _unique_public_record(public_records, title, "最终设定")
    record = SystemPromptRecord(
        title=title,
        prompt=str(source.get("prompt", "")),
        first_input=str(source.get("first_input", "")),
    )
    records.append(record)
    return record


def seed_public_prompt_assets_if_needed() -> bool:
    """Import the public allowlist once without replacing existing user assets."""
    if settings.int(AppStorageKeys.PUBLIC_PROMPT_ASSET_SEED_VERSION, 0) >= PUBLIC_ASSET_SEED_VERSION:
        return False

    payload = json.loads(PUBLIC_ASSETS_PATH.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 1:
        raise RuntimeError("不支持的公开 Prompt 资产版本。")

    world_books = [
        PromptTextRecord.from_dict(item)
        for item in _decode_records(AppStorageKeys.WORLD_BOOK_RECORDS)
    ]
    character_cards = [
        PromptTextRecord.from_dict(item)
        for item in _decode_records(AppStorageKeys.CHARACTER_CARD_RECORDS)
    ]
    templates = [
        FixedTemplateRecord.from_dict(item)
        for item in _decode_records(AppStorageKeys.FIXED_TEMPLATE_RECORDS)
    ]
    final_settings = [
        SystemPromptRecord.from_dict(item)
        for item in _decode_records(AppStorageKeys.SYSTEM_PROMPT_RECORDS)
        if str(item.get("title", "")).strip() not in REMOVED_BUILTIN_PROMPT_TITLES
    ]
    hidden_final_settings = [
        SystemPromptRecord.from_dict(item)
        for item in _decode_records(AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS)
        if str(item.get("title", "")).strip() not in REMOVED_BUILTIN_PROMPT_TITLES
    ]

    public_world_books = list(payload.get("world_books") or [])
    public_character_cards = list(payload.get("character_cards") or [])
    public_templates = list(payload.get("fixed_templates") or [])
    public_final_settings = list(payload.get("final_settings") or [])

    world_book = _upsert_text_record(
        world_books, public_world_books, "火影忍者", "世界书"
    )
    seeded_characters = {
        title: _upsert_text_record(
            character_cards, public_character_cards, title, "角色卡"
        )
        for title in ("日向雏田", "油女志乃", "犬冢牙")
    }
    user_character = seeded_characters["油女志乃"]
    other_characters = [
        seeded_characters["日向雏田"],
        seeded_characters["犬冢牙"],
    ]
    default_template = _upsert_template(templates, public_templates, "默认模板")
    final_setting = _upsert_final_setting(
        final_settings, public_final_settings, "火影忍者·油女志乃"
    )

    # The published final prompt is self-contained. Link the world and characters,
    # but leave the template unlinked because its original private template was not
    # part of the upload allowlist.
    if not final_setting.final_world_book_id:
        final_setting.final_world_book_id = world_book.id
    if not final_setting.final_user_character_id:
        final_setting.final_user_character_id = user_character.id
    if not final_setting.final_other_character_ids:
        final_setting.final_other_character_ids = [item.id for item in other_characters]
    if not final_setting.final_user_character_name:
        final_setting.final_user_character_name = user_character.title
    if not final_setting.final_other_character_names:
        final_setting.final_other_character_names = [item.title for item in other_characters]

    updates = {
        AppStorageKeys.WORLD_BOOK_RECORDS: _encode_records(world_books),
        AppStorageKeys.CHARACTER_CARD_RECORDS: _encode_records(character_cards),
        AppStorageKeys.FIXED_TEMPLATE_RECORDS: _encode_records(templates),
        AppStorageKeys.FIXED_TEMPLATE_DEFAULT_SEEDED: True,
        AppStorageKeys.SELECTED_FIXED_TEMPLATE_RECORD_ID: default_template.id,
        AppStorageKeys.SYSTEM_PROMPT_RECORDS: _encode_records(final_settings),
        AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS: _encode_records(hidden_final_settings),
        AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID: final_setting.id,
        AppStorageKeys.SYSTEM_PROMPT: final_setting.prompt,
        AppStorageKeys.SYSTEM_PROMPT_RECORD_NEXT_INDEX: max(1, len(final_settings) + 1),
        AppStorageKeys.PUBLIC_PROMPT_ASSET_SEED_VERSION: PUBLIC_ASSET_SEED_VERSION,
    }
    settings.set_many_atomic(updates)
    return True
