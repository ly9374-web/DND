from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import AppStorageKeys, settings
from app.models import FirstInputRecord, SystemPromptRecord, now_iso
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

    _ensure_record_defaults(state)
    _ensure_selected_prompt_visible(state)
    return state


def _ensure_record_defaults(state: PromptState) -> None:
    """Migrate old final settings to named first inputs and the default memory."""
    from app.services import prompt_assets

    prompt_assets.load_long_term_memories(hidden_space=True)
    normal_default_memory = prompt_assets.default_long_term_memory_record()
    hidden_default_memory = prompt_assets.default_long_term_memory_record(
        hidden_space=True
    )
    changed = False
    for record in state.records + state.hidden_records:
        default_memory = (
            (hidden_default_memory or normal_default_memory)
            if record in state.hidden_records
            else normal_default_memory
        )
        if bool(getattr(record, "first_inputs_need_migration", False)):
            changed = True
        before_first_input = str(record.first_input or "")
        before_selected_id = str(record.selected_first_input_id or "")
        record.__post_init__()
        if (
            before_first_input != record.first_input
            or before_selected_id != record.selected_first_input_id
        ):
            changed = True
        if not str(record.long_term_memory or "").strip():
            record.long_term_memory = (
                str(default_memory.content or "")
                if default_memory is not None
                else prompt_assets.DEFAULT_LONG_TERM_MEMORY
            )
            changed = True
        if not str(record.final_long_term_memory_id or "").strip() and default_memory is not None:
            record.final_long_term_memory_id = default_memory.id
            changed = True
    if changed:
        _persist(state)


def _ensure_selected_prompt_visible(state: PromptState) -> None:
    """普通账号下，选中的最终设定若属于隐藏空间，则回退到第一条普通记录。

    防止隐藏账号选中的隐藏 prompt 通过全局 SYSTEM_PROMPT 泄露到普通账号的聊天上下文。
    """
    if state.hidden_space:
        return
    selected = str(state.selected_record_id or "").strip()
    if not selected:
        return
    if any(record.id == selected for record in state.records):
        return
    if not any(record.id == selected for record in state.hidden_records):
        return
    fallback = state.records[0] if state.records else None
    settings.set(
        AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID,
        fallback.id if fallback is not None else "",
    )
    settings.set(AppStorageKeys.SYSTEM_PROMPT, fallback.prompt if fallback is not None else "")
    state.selected_record_id = fallback.id if fallback is not None else ""


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
    long_term_memory_id: str | None = None,
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
    if long_term_memory_id is not None:
        record.final_long_term_memory_id = str(long_term_memory_id or "").strip()
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
    first_input: str | None = None,
    first_inputs: list[FirstInputRecord] | None = None,
    selected_first_input_id: str | None = None,
    long_term_memory: str | None = None,
    preserve_blank_title: bool = False,
    final_template_id: str | None = None,
    final_world_book_id: str | None = None,
    final_long_term_memory_id: str | None = None,
    final_user_character_id: str | None = None,
    final_other_character_ids: list[str] | None = None,
    final_user_character_name: str | None = None,
    final_other_character_names: list[str] | None = None,
) -> PromptState:
    record_id = str(record_id or "").strip()
    title = str(title or "").strip()
    prompt = str(prompt or "").strip()
    normalized_first_input = (
        str(first_input or "").strip() if first_input is not None else None
    )

    existing = get_record(state, record_id) if record_id else None

    if existing is not None:
        existing.title = title
        existing.prompt = prompt
        if first_inputs is not None:
            existing.first_inputs = list(first_inputs)
        if selected_first_input_id is not None:
            existing.selected_first_input_id = str(selected_first_input_id or "")
        if normalized_first_input is not None and first_inputs is None:
            selected = next(
                (
                    item
                    for item in existing.first_inputs
                    if item.id == existing.selected_first_input_id
                ),
                None,
            )
            if selected is not None:
                selected.content = normalized_first_input
                selected.updated_at = now_iso()
        if long_term_memory is not None:
            existing.long_term_memory = str(long_term_memory or "").strip()
        if final_template_id is not None:
            existing.final_template_id = str(final_template_id or "")
        if final_world_book_id is not None:
            existing.final_world_book_id = str(final_world_book_id or "")
        if final_long_term_memory_id is not None:
            existing.final_long_term_memory_id = str(final_long_term_memory_id or "")
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
        existing.__post_init__()
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
        first_input=normalized_first_input or "",
        first_inputs=list(first_inputs or []),
        selected_first_input_id=str(selected_first_input_id or ""),
        long_term_memory=str(long_term_memory or ""),
        final_template_id=str(final_template_id or ""),
        final_world_book_id=str(final_world_book_id or ""),
        final_long_term_memory_id=str(final_long_term_memory_id or ""),
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
    return first_input_for_record(state, state.selected_record_id)


def first_input_for_record(state: PromptState, record_id: str) -> str:
    record = get_record(state, record_id)
    if record is None:
        return ""
    record.__post_init__()
    selected = next(
        item for item in record.first_inputs if item.id == record.selected_first_input_id
    )
    return str(selected.content or "").strip()


def create_first_input(state: PromptState, record_id: str) -> FirstInputRecord:
    record = get_record(state, record_id)
    if record is None:
        raise ValueError("无法新建首轮输入：最终设定不存在。")
    record.__post_init__()
    next_number = len(record.first_inputs) + 1
    existing_titles = {str(item.title or "").strip() for item in record.first_inputs}
    title = f"首轮输入 {next_number}"
    while title in existing_titles:
        next_number += 1
        title = f"首轮输入 {next_number}"
    item = FirstInputRecord(title=title, content="")
    record.first_inputs.append(item)
    record.selected_first_input_id = item.id
    record.first_input = ""
    record.updated_at = now_iso()
    _persist(state)
    return item


def select_first_input(state: PromptState, record_id: str, first_input_id: str) -> None:
    record = get_record(state, record_id)
    if record is None:
        raise ValueError("无法选择首轮输入：最终设定不存在。")
    selected = next(
        (item for item in record.first_inputs if item.id == first_input_id),
        None,
    )
    if selected is None:
        raise ValueError("无法选择首轮输入：记录不存在。")
    record.selected_first_input_id = selected.id
    record.first_input = str(selected.content or "")
    record.updated_at = now_iso()
    _persist(state)


def update_first_input(
    state: PromptState,
    record_id: str,
    first_input_id: str,
    *,
    title: str,
    content: str,
) -> None:
    record = get_record(state, record_id)
    if record is None:
        raise ValueError("无法保存首轮输入：最终设定不存在。")
    selected = next(
        (item for item in record.first_inputs if item.id == first_input_id),
        None,
    )
    if selected is None:
        raise ValueError("无法保存首轮输入：记录不存在。")
    selected.title = str(title or "").strip()
    selected.content = str(content or "").strip()
    selected.updated_at = now_iso()
    if record.selected_first_input_id == selected.id:
        record.first_input = selected.content
    record.updated_at = selected.updated_at
    _persist(state)


def delete_first_input(state: PromptState, record_id: str, first_input_id: str) -> str:
    record = get_record(state, record_id)
    if record is None:
        raise ValueError("无法删除首轮输入：最终设定不存在。")
    record.__post_init__()
    if len(record.first_inputs) <= 1:
        raise ValueError("每个最终设定至少需要保留一条首轮输入。")
    record.first_inputs = [
        item for item in record.first_inputs if item.id != first_input_id
    ]
    if not any(item.id == record.selected_first_input_id for item in record.first_inputs):
        record.selected_first_input_id = record.first_inputs[0].id
    record.__post_init__()
    record.updated_at = now_iso()
    _persist(state)
    return record.selected_first_input_id


def long_term_memory_for_record(state: PromptState, record_id: str) -> str:
    from app.services import prompt_assets

    record = get_record(state, record_id)
    hidden_space = bool(record is not None and record in state.hidden_records)
    default_memory = prompt_assets.current_default_long_term_memory(
        hidden_space=hidden_space
    )
    if record is None:
        return default_memory
    return str(record.long_term_memory or "").strip() or default_memory
