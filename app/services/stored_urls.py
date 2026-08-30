from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass

from app.config import AppStorageKeys, settings
from app.models import StoredImageURLRecord, now_iso


@dataclass
class StoredURLState:
    hidden_space: bool
    records: list[StoredImageURLRecord]
    hidden_records: list[StoredImageURLRecord]


def _decode_records(raw) -> list[StoredImageURLRecord]:
    if not raw:
        return []

    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except Exception:
            return []

    records: list[StoredImageURLRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        records.append(StoredImageURLRecord.from_dict(item))
    return records


def _encode_records(records: list[StoredImageURLRecord]) -> str:
    return json.dumps([r.to_dict() for r in records], ensure_ascii=False)


def load_state(hidden_space: bool = False) -> StoredURLState:
    return StoredURLState(
        hidden_space=bool(hidden_space),
        records=_decode_records(settings.get(AppStorageKeys.STORED_IMAGE_URL_RECORDS, "")),
        hidden_records=_decode_records(settings.get(AppStorageKeys.HIDDEN_URL_RECORDS, "")),
    )


def persist_state(state: StoredURLState):
    settings.set(AppStorageKeys.STORED_IMAGE_URL_RECORDS, _encode_records(state.records))
    settings.set(AppStorageKeys.HIDDEN_URL_RECORDS, _encode_records(state.hidden_records))


def visible_records(state: StoredURLState) -> list[StoredImageURLRecord]:
    if state.hidden_space:
        return state.records + state.hidden_records
    return state.records


def validate_url(url: str):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ["http", "https"] or not parsed.netloc:
        raise ValueError("请输入有效的 http/https URL。")


def add_url(state: StoredURLState, url: str, title: str = "") -> StoredURLState:
    text = str(url or "").strip()
    if not text:
        return state

    validate_url(text)

    custom_title = str(title or "").strip()
    if custom_title:
        final_title = custom_title
    else:
        if state.hidden_space:
            next_key = AppStorageKeys.HIDDEN_URL_RECORD_NEXT_INDEX
            prefix = "隐藏url"
        else:
            next_key = AppStorageKeys.STORED_IMAGE_URL_RECORD_NEXT_INDEX
            prefix = "url"
        index = max(1, settings.int(next_key, 1))
        final_title = f"{prefix}{index}"
        settings.set(next_key, index + 1)

    record = StoredImageURLRecord(
        title=final_title,
        url=text,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    if state.hidden_space:
        state.hidden_records.append(record)
    else:
        state.records.append(record)

    persist_state(state)
    return state


def rename_url(state: StoredURLState, record_id: str, new_title: str) -> StoredURLState:
    record_id = str(record_id or "").strip()
    new_title = str(new_title or "").strip()
    if not record_id or not new_title:
        return state

    for records in (state.records, state.hidden_records):
        for record in records:
            if record.id == record_id:
                record.title = new_title
                record.updated_at = now_iso()
    persist_state(state)
    return state


def delete_url(state: StoredURLState, record_id: str) -> StoredURLState:
    record_id = str(record_id or "").strip()
    if not record_id:
        return state

    state.records = [r for r in state.records if r.id != record_id]
    state.hidden_records = [r for r in state.hidden_records if r.id != record_id]
    persist_state(state)
    return state
