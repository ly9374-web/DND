from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / ".python_app_data" / "user_defaults.json"
OUTPUT_PATH = ROOT / "public_prompt_assets" / "naruto_public.json"

WORLD_BOOK_TITLES = ["火影忍者"]
CHARACTER_CARD_TITLES = ["日向雏田", "油女志乃", "犬冢牙"]
FIXED_TEMPLATE_TITLES = ["默认模板"]
FINAL_SETTING_TITLES = ["火影忍者·油女志乃"]


def decode_records(settings: dict, key: str) -> list[dict]:
    raw = settings.get(key, "")
    try:
        records = raw if isinstance(raw, list) else json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法解析 {key}") from exc
    return [item for item in records if isinstance(item, dict)]


def select_exact(records: list[dict], titles: list[str], label: str) -> list[dict]:
    selected: list[dict] = []
    for title in titles:
        matches = [item for item in records if str(item.get("title", "")).strip() == title]
        if len(matches) != 1:
            raise RuntimeError(f'{label}“{title}”应当恰好存在一条，实际为 {len(matches)} 条。')
        selected.append(matches[0])
    return selected


def public_text_record(record: dict) -> dict:
    return {
        "title": str(record.get("title", "")),
        "content": str(record.get("content", "")),
    }


def public_template_record(record: dict) -> dict:
    return {
        "title": str(record.get("title", "")),
        "background_intro": str(record.get("background_intro", "")),
        "dm_core_tasks": str(record.get("dm_core_tasks", "")),
        "response_format": str(record.get("response_format", "")),
    }


def public_final_setting(record: dict) -> dict:
    return {
        "title": str(record.get("title", "")),
        "prompt": str(record.get("prompt", "")),
        "first_input": str(record.get("first_input", "")),
        "world_book": WORLD_BOOK_TITLES[0],
        "user_character": "油女志乃",
        "other_characters": ["日向雏田", "犬冢牙"],
    }


def main() -> None:
    settings = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    world_books = select_exact(
        decode_records(settings, "worldBookRecords"), WORLD_BOOK_TITLES, "世界书"
    )
    character_cards = select_exact(
        decode_records(settings, "characterCardRecords"),
        CHARACTER_CARD_TITLES,
        "角色卡",
    )
    fixed_templates = select_exact(
        decode_records(settings, "fixedTemplateRecords"),
        FIXED_TEMPLATE_TITLES,
        "固定模板",
    )
    final_settings = select_exact(
        decode_records(settings, "systemPromptRecords"),
        FINAL_SETTING_TITLES,
        "最终设定",
    )

    payload = {
        "schema_version": 1,
        "description": "可公开上传的火影忍者 Prompt 资产；不包含 API Key、聊天记录、UUID 或时间戳。",
        "world_books": [public_text_record(item) for item in world_books],
        "character_cards": [public_text_record(item) for item in character_cards],
        "fixed_templates": [public_template_record(item) for item in fixed_templates],
        "final_settings": [public_final_setting(item) for item in final_settings],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
