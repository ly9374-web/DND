from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import AppStorageKeys, settings
from app.models import SystemPromptRecord, now_iso
from app.services.public_prompt_seed import seed_public_prompt_assets_if_needed


DEFAULT_SYSTEM_PROMPT_FILE = Path(__file__).resolve().parents[1] / "default_prompts" / "system_prompt.json"


@dataclass
class PromptState:
    hidden_space: bool
    records: list[SystemPromptRecord]
    hidden_records: list[SystemPromptRecord]
    next_index: int
    hidden_next_index: int
    selected_record_id: str


def _decode_records(raw) -> list[SystemPromptRecord]:
    if not raw:
        return []

    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except Exception:
            return []

    records: list[SystemPromptRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        records.append(SystemPromptRecord.from_dict(item))
    return records


def _encode_records(records: list[SystemPromptRecord]) -> str:
    return json.dumps([r.to_dict() for r in records], ensure_ascii=False)


def _persist(state: PromptState):
    settings.set(AppStorageKeys.SYSTEM_PROMPT_RECORDS, _encode_records(state.records))
    settings.set(AppStorageKeys.SYSTEM_PROMPT_RECORD_NEXT_INDEX, int(state.next_index))
    settings.set(AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS, _encode_records(state.hidden_records))
    settings.set(AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORD_NEXT_INDEX, int(state.hidden_next_index))


def _load_default_records() -> tuple[list[SystemPromptRecord], str]:
    try:
        data = json.loads(DEFAULT_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return [], ""

    if isinstance(data, list):
        items = data
        selected_title = ""
    elif isinstance(data, dict):
        items = data.get("records") or []
        selected_title = str(data.get("selected_title", "") or "").strip()
    else:
        return [], ""

    records = [
        SystemPromptRecord.from_dict(item)
        for item in items
        if isinstance(item, dict)
    ]
    return records, selected_title


def _seed_default_records(state: PromptState) -> PromptState:
    records, selected_title = _load_default_records()
    if not records:
        return state

    state.records = records
    state.next_index = max(state.next_index, len(records) + 1)

    selected_record = None
    if selected_title:
        selected_record = next((record for record in records if record.title == selected_title), None)
    selected_record = selected_record or records[0]

    state.selected_record_id = selected_record.id
    settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, selected_record.id)
    settings.set(AppStorageKeys.SYSTEM_PROMPT, selected_record.prompt)
    _persist(state)
    return state


def load_state(hidden_space: bool = False) -> PromptState:
    seed_public_prompt_assets_if_needed()
    state = PromptState(
        hidden_space=bool(hidden_space),
        records=_decode_records(settings.get(AppStorageKeys.SYSTEM_PROMPT_RECORDS, "")),
        hidden_records=_decode_records(settings.get(AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS, "")),
        next_index=max(1, int(settings.get(AppStorageKeys.SYSTEM_PROMPT_RECORD_NEXT_INDEX, 1) or 1)),
        hidden_next_index=max(1, int(settings.get(AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORD_NEXT_INDEX, 1) or 1)),
        selected_record_id=str(settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or ""),
    )

    if not state.records:
        legacy_prompt = str(settings.get(AppStorageKeys.SYSTEM_PROMPT, "") or "").strip()
        if legacy_prompt:
            record = SystemPromptRecord(
                title="记录1",
                prompt=legacy_prompt,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            state.records = [record]
            state.selected_record_id = record.id
            state.next_index = max(state.next_index, 2)
            _persist(state)

            settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, record.id)
            settings.set(AppStorageKeys.SYSTEM_PROMPT, record.prompt)
        else:
            state = _seed_default_records(state)

    return state


def visible_records(state: PromptState) -> list[SystemPromptRecord]:
    if state.hidden_space:
        return state.records + state.hidden_records
    return state.records


def record_label(
    state: PromptState,
    record: SystemPromptRecord,
    index: int,
    *,
    unnamed: str = "未命名 prompt",
) -> str:
    prefix = "隐藏：" if record_space(state, record.id) == "hidden" else ""
    return f"{index + 1}. {prefix}{record.title or unnamed}"


def record_space(state: PromptState, record_id: str) -> Optional[str]:
    for record in state.hidden_records:
        if record.id == record_id:
            return "hidden"
    for record in state.records:
        if record.id == record_id:
            return "normal"
    return None


def get_record(state: PromptState, record_id: str) -> Optional[SystemPromptRecord]:
    for record in state.records + state.hidden_records:
        if record.id == record_id:
            return record
    return None


def update_selected_prompt(state: PromptState, prompt: str) -> Optional[SystemPromptRecord]:
    """Keep the active Page2 prompt and its selected prompt record in sync."""
    prompt = str(prompt or "").strip()
    settings.set(AppStorageKeys.SYSTEM_PROMPT, prompt)

    record = get_record(state, state.selected_record_id)
    if record is None:
        return None

    record.prompt = prompt
    record.updated_at = now_iso()
    _persist(state)
    return record


def bind_final_setting_sources(
    state: PromptState,
    *,
    record_id: str,
    template_id: str,
    world_book_id: str,
    user_character_id: str,
    other_character_ids: list[str],
    user_character_name: str,
    other_character_names: list[str],
) -> SystemPromptRecord:
    """Attach source records to a legacy final setting without changing its prompt."""
    record = get_record(state, str(record_id or "").strip())
    if record is None:
        raise ValueError("无法补录来源关联：最终设定记录不存在。")

    record.final_template_id = str(template_id or "").strip()
    record.final_world_book_id = str(world_book_id or "").strip()
    record.final_user_character_id = str(user_character_id or "").strip()
    record.final_other_character_ids = [
        str(item or "").strip() for item in other_character_ids
    ]
    record.final_user_character_name = str(user_character_name or "").strip()
    record.final_other_character_names = [
        str(item or "").strip() for item in other_character_names
    ]
    record.updated_at = now_iso()
    _persist(state)
    return record


def save_prompt(
    state: PromptState,
    *,
    record_id: str,
    title: str,
    prompt: str,
    first_input: str = "",
    preserve_blank_title: bool = False,
    final_template_id: str | None = None,
    final_world_book_id: str | None = None,
    final_user_character_id: str | None = None,
    final_other_character_ids: list[str] | None = None,
    final_user_character_name: str | None = None,
    final_other_character_names: list[str] | None = None,
) -> PromptState:
    record_id = str(record_id or "").strip()
    title = str(title or "").strip()
    prompt = str(prompt or "").strip()
    first_input = str(first_input or "").strip()

    existing = get_record(state, record_id) if record_id else None

    if existing is not None:
        existing.title = title
        existing.prompt = prompt
        existing.first_input = first_input
        if final_template_id is not None:
            existing.final_template_id = str(final_template_id or "")
        if final_world_book_id is not None:
            existing.final_world_book_id = str(final_world_book_id or "")
        if final_user_character_id is not None:
            existing.final_user_character_id = str(final_user_character_id or "")
        if final_other_character_ids is not None:
            existing.final_other_character_ids = [str(item or "") for item in final_other_character_ids]
        if final_user_character_name is not None:
            existing.final_user_character_name = str(final_user_character_name or "")
        if final_other_character_names is not None:
            existing.final_other_character_names = [
                str(item or "") for item in final_other_character_names
            ]
        existing.updated_at = now_iso()
        settings.set(AppStorageKeys.SYSTEM_PROMPT, existing.prompt)
        settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, existing.id)
        state.selected_record_id = existing.id
        _persist(state)
        return state

    if state.hidden_space:
        new_title = title if preserve_blank_title else (title or f"隐藏记录{state.hidden_next_index}")
        state.hidden_next_index += 1
    else:
        new_title = title if preserve_blank_title else (title or f"记录{state.next_index}")
        state.next_index += 1

    record = SystemPromptRecord(
        title=new_title,
        prompt=prompt,
        first_input=first_input,
        final_template_id=str(final_template_id or ""),
        final_world_book_id=str(final_world_book_id or ""),
        final_user_character_id=str(final_user_character_id or ""),
        final_other_character_ids=[str(item or "") for item in (final_other_character_ids or [])],
        final_user_character_name=str(final_user_character_name or ""),
        final_other_character_names=[
            str(item or "") for item in (final_other_character_names or [])
        ],
        created_at=now_iso(),
        updated_at=now_iso(),
    )

    if state.hidden_space:
        state.hidden_records.append(record)
    else:
        state.records.append(record)

    state.selected_record_id = record.id
    settings.set(AppStorageKeys.SYSTEM_PROMPT, record.prompt)
    settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, record.id)
    _persist(state)
    return state


def delete_record(state: PromptState, record_id: str) -> PromptState:
    record_id = str(record_id or "").strip()
    if not record_id:
        return state

    state.records = [r for r in state.records if r.id != record_id]
    state.hidden_records = [r for r in state.hidden_records if r.id != record_id]

    if str(settings.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "") or "") == record_id:
        settings.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, "")
        settings.set(AppStorageKeys.SYSTEM_PROMPT, "")
        state.selected_record_id = ""

    _persist(state)
    return state


def selected_first_input(state: PromptState) -> str:
    """当前选中最终设定的首轮输入（未设置则为空串）。"""
    record = get_record(state, state.selected_record_id)
    if record is None:
        return ""
    return str(record.first_input or "").strip()
