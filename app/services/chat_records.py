from __future__ import annotations

from copy import deepcopy
from typing import Optional

from app.models import ChatRecord, ChatRecordIndexItem, new_id, now_iso
from app.storage import ChatRecordStore


def load_index_sorted(*, scope: str | None = None) -> list[ChatRecordIndexItem]:
    ChatRecordStore.migrate_legacy_chat_records_if_needed(scope=scope)
    return ChatRecordStore.load_index_sorted(scope=scope)


def find_index_item(record_id: str, *, scope: str | None = None) -> Optional[ChatRecordIndexItem]:
    record_id = str(record_id or "").strip()
    if not record_id:
        return None

    for item in ChatRecordStore.load_index(scope=scope):
        if item.id == record_id:
            return item
    return None


def load_record_by_id(record_id: str, *, scope: str | None = None) -> Optional[ChatRecord]:
    item = find_index_item(record_id, scope=scope)
    if item is None:
        return None
    try:
        return ChatRecordStore.load_record(item, scope=scope)
    except Exception:
        return None


def rename_record(record_id: str, new_title: str, *, scope: str | None = None):
    ChatRecordStore.rename_record(record_id, new_title, scope=scope)


def delete_record(record_id: str, *, scope: str | None = None):
    ChatRecordStore.delete_record(record_id, scope=scope)


def branch_title_defaults(
    record_id: str, *, scope: str | None = None
) -> tuple[str, str, str]:
    """Return the current title, its numeric default, and the next branch title."""
    source = load_record_by_id(record_id, scope=scope)
    if source is None:
        raise ValueError("当前聊天记录不存在，无法创建分支。")

    branch_group_id = str(source.branch_group_id or source.id)
    source_branch_number = source.branch_number or 1
    next_branch_number = source_branch_number
    for item in ChatRecordStore.load_index(scope=scope):
        if str(getattr(item, "branch_group_id", "") or "") != branch_group_id:
            continue
        number = getattr(item, "branch_number", None)
        if number is not None:
            next_branch_number = max(next_branch_number, int(number))
    next_branch_number += 1
    source_default = str(source_branch_number)
    current_title = source.title if source.branch_number is not None else source_default
    return current_title, source_default, str(next_branch_number)


def create_branch(
    record_id: str,
    *,
    scope: str | None = None,
    source_title: str | None = None,
    branch_title: str | None = None,
) -> ChatRecord:
    """Persist and return an independent, incrementally numbered branch."""
    source = load_record_by_id(record_id, scope=scope)
    if source is None:
        raise ValueError("当前聊天记录不存在，无法创建分支。")

    branch_group_id = str(source.branch_group_id or source.id)
    source_branch_number = source.branch_number or 1
    _, source_default, next_default = branch_title_defaults(record_id, scope=scope)
    chosen_source_title = source_default if source.branch_number is None else source.title
    if source_title is not None:
        chosen_source_title = str(source_title).strip()
    chosen_branch_title = next_default if branch_title is None else str(branch_title).strip()
    if not chosen_source_title or not chosen_branch_title:
        raise ValueError("对话名称不能为空。")

    now = now_iso()
    source.branch_group_id = branch_group_id
    source.branch_number = source_branch_number
    source.title = chosen_source_title
    source.updated_at = now
    ChatRecordStore.save_or_update_record(source, scope=scope)

    branch = deepcopy(source)
    branch.id = new_id()
    branch.title = chosen_branch_title
    branch.branch_number = int(next_default)
    branch.created_at = now
    branch.updated_at = now
    ChatRecordStore.save_or_update_record(branch, scope=scope)
    return branch
