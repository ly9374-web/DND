"""Pure, strict serialization of ordinary prompt assets; no runtime store imports."""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5
import json

CATEGORIES = {
    "world_books": "worldBookRecords",
    "character_cards": "characterCardRecords",
    "fixed_templates": "fixedTemplateRecords",
    "final_settings": "systemPromptRecords",
    "long_term_memories": "longTermMemoryRecords",
    "quick_inputs": "quickInputRecords",
}
TEXT_FIELDS = ("title", "content")
TEMPLATE_FIELDS = ("title", "background_intro", "dm_core_tasks", "response_format")
SOURCE_FIELDS = {
    "template": ("final_template_id", "fixed_templates"),
    "world_book": ("final_world_book_id", "world_books"),
    "long_term_memory": ("final_long_term_memory_id", "long_term_memories"),
    "user_character": ("final_user_character_id", "character_cards"),
}
DEFAULT_FIELDS = {
    "final_setting": ("selectedSystemPromptRecordID", "final_settings"),
    "template": ("selectedFixedTemplateRecordID", "fixed_templates"),
    "long_term_memory": ("defaultLongTermMemoryRecordID", "long_term_memories"),
}


def decode_records(values: dict, key: str) -> list[dict]:
    raw = values.get(key, [])
    try:
        rows = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无法解析 {key}，已停止，未跳过任何记录") from exc
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError(f"{key} 必须是记录列表，已停止")
    return rows


def _text(record: dict, fields) -> dict:
    result = {}
    for key in fields:
        value = record.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"Prompt 字段 {key} 必须是文本")
        result[key] = value
    return result


def build_public_bundle(values: dict) -> tuple[dict, list[str]]:
    """Only the six ordinary lists and explicitly supported fields can be published."""
    rows = {cat: decode_records(values, key) for cat, key in CATEGORIES.items()}
    maps = {}
    for cat, items in rows.items():
        ids = [item.get("id") for item in items]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"{cat} 有缺失或重复 ID，已停止导出")
        maps[cat] = {i: str(uuid5(NAMESPACE_URL, f"dnd-public/{cat}/{i}")) for i in ids}
    warnings = []
    payload = {"schema_version": 2}
    for cat, items in rows.items():
        exported = []
        for item in items:
            result = {"asset_id": maps[cat][item["id"]]}
            if cat != "final_settings":
                result.update(_text(item, TEMPLATE_FIELDS if cat == "fixed_templates" else TEXT_FIELDS))
            else:
                result.update(_text(item, ("title", "prompt", "long_term_memory")))
                inputs = item.get("first_inputs")
                if inputs is None:
                    inputs = [{"id": "legacy", "title": "首次输入1", "content": item.get("first_input", "")}]
                if not isinstance(inputs, list) or not inputs or any(not isinstance(i, dict) for i in inputs):
                    raise ValueError("最终设定首次输入格式不正确，已停止导出")
                input_ids = [i.get("id") for i in inputs]
                if any(not isinstance(i, str) or not i for i in input_ids) or len(set(input_ids)) != len(input_ids):
                    raise ValueError("首次输入 ID 缺失或重复，已停止导出")
                result["first_inputs"] = [_text(i, ("title", "content")) for i in inputs]
                selected = item.get("selected_first_input_id")
                if selected and selected not in input_ids:
                    raise ValueError("首次输入选择指向不存在的记录")
                result["selected_first_input_index"] = input_ids.index(selected) if selected else 0
                sources = {}
                for name, (field, target) in SOURCE_FIELDS.items():
                    ref = item.get(field, "")
                    sources[name] = maps[target].get(ref, "")
                    if ref and not sources[name]:
                        warnings.append(f"普通区最终设定的 {name} 关联不在普通区；仅导出正文，未改本地关联。")
                refs = item.get("final_other_character_ids", [])
                if not isinstance(refs, list):
                    raise ValueError("其他角色关联格式不正确")
                sources["other_characters"] = [maps["character_cards"][ref] for ref in refs if ref in maps["character_cards"]]
                if any(ref not in maps["character_cards"] for ref in refs):
                    warnings.append("普通区最终设定有其他角色关联不在普通区；未改本地关联。")
                result["sources"] = sources
            exported.append(result)
        payload[cat] = exported
    payload["defaults"] = {name: maps[cat].get(values.get(key), "") for name, (key, cat) in DEFAULT_FIELDS.items()}
    return payload, warnings
