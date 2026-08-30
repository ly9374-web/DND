from __future__ import annotations

from typing import Optional, Tuple

from app.models import ChatRecord, ChatRecordIndexItem
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
