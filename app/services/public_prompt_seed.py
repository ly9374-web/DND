"""Import a public bundle only into a fresh prompt store; never migrate user data."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import AppStorageKeys, settings
from app.models import FixedTemplateRecord, FirstInputRecord, PromptTextRecord, SystemPromptRecord
from app.services.public_prompt_bundle import CATEGORIES, DEFAULT_FIELDS, SOURCE_FIELDS, TEXT_FIELDS, TEMPLATE_FIELDS

PUBLIC_ASSETS_PATH = Path(__file__).resolve().parents[2] / "public_prompt_assets" / "ordinary.json"
PUBLIC_ASSET_SEED_VERSION = 2


def seed_public_prompt_assets_if_needed() -> bool:
    # Even empty existing lists belong to the user: do not resurrect deleted assets.
    keys = list(CATEGORIES.values())
    keys += ["hidden" + key[0].upper() + key[1:] for key in CATEGORIES.values()]
    if settings.get(AppStorageKeys.PUBLIC_PROMPT_ASSET_SEED_VERSION) is not None:
        return False
    if any(settings.get(key) is not None for key in keys):
        return False
    if settings.get(AppStorageKeys.SYSTEM_PROMPT) or settings.get(AppStorageKeys.CHAT_RECORD_INDEX) or settings.get(AppStorageKeys.CHAT_RECORDS):
        return False

    payload = json.loads(PUBLIC_ASSETS_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2:
        raise ValueError("不支持的公开 Prompt 资产版本")
    records, mapping = {}, {}
    for cat in CATEGORIES:
        items = payload.get(cat)
        if not isinstance(items, list):
            raise ValueError(f"公开资产缺少 {cat}")
        records[cat], mapping[cat] = [], {}
        for source in items:
            asset_id = source.get("asset_id")
            if not asset_id or asset_id in mapping[cat]:
                raise ValueError("公开资产标识缺失或重复")
            if cat == "final_settings":
                inputs = [FirstInputRecord(**{key: item[key] for key in ("title", "content")}) for item in source["first_inputs"]]
                selected = source["selected_first_input_index"]
                if not isinstance(selected, int) or selected < 0 or selected >= len(inputs):
                    raise ValueError("公开资产首次输入选择无效")
                record = SystemPromptRecord(
                    title=source["title"], prompt=source["prompt"], long_term_memory=source["long_term_memory"],
                    first_inputs=inputs, selected_first_input_id=inputs[selected].id,
                )
            elif cat == "fixed_templates":
                record = FixedTemplateRecord(**{key: source[key] for key in TEMPLATE_FIELDS})
            else:
                record = PromptTextRecord(**{key: source[key] for key in TEXT_FIELDS})
            records[cat].append(record)
            mapping[cat][asset_id] = record

    for source, record in zip(payload["final_settings"], records["final_settings"]):
        sources = source["sources"]
        for name, (field, cat) in SOURCE_FIELDS.items():
            ref = sources.get(name, "")
            setattr(record, field, mapping[cat][ref].id if ref else "")
        record.final_other_character_ids = [mapping["character_cards"][ref].id for ref in sources.get("other_characters", [])]
        ref = sources.get("user_character")
        record.final_user_character_name = mapping["character_cards"][ref].title if ref else ""
        record.final_other_character_names = [mapping["character_cards"][ref].title for ref in sources.get("other_characters", [])]

    updates = {key: json.dumps([r.to_dict() for r in records[cat]], ensure_ascii=False) for cat, key in CATEGORIES.items()}
    for name, (key, cat) in DEFAULT_FIELDS.items():
        ref = payload.get("defaults", {}).get(name, "")
        chosen = mapping[cat][ref] if ref else next(iter(records[cat]), None)
        updates[key] = chosen.id if chosen else ""
        if name == "final_setting":
            updates[AppStorageKeys.SYSTEM_PROMPT] = chosen.prompt if chosen else ""
    updates.update({
        AppStorageKeys.FIXED_TEMPLATE_DEFAULT_SEEDED: True,
        AppStorageKeys.LONG_TERM_MEMORY_DEFAULT_SEEDED: True,
        AppStorageKeys.QUICK_INPUT_DEFAULT_SEEDED: True,
        AppStorageKeys.SYSTEM_PROMPT_RECORD_NEXT_INDEX: len(records["final_settings"]) + 1,
        AppStorageKeys.PUBLIC_PROMPT_ASSET_SEED_VERSION: PUBLIC_ASSET_SEED_VERSION,
    })
    settings.set_many_atomic(updates)
    return True
