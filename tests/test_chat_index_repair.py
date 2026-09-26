"""Index repair preserves chat bodies, visibility, scope and branch metadata."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import storage
from app.config import AppStorageKeys, SettingsStore
from app.models import ChatRecord


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CHAT_RECORDS_DIR", tmp_path / "Chats")
    monkeypatch.setattr(storage, "settings", SettingsStore(tmp_path / "settings.json"))
    return storage.ChatRecordStore


def chat(**kwargs):
    return ChatRecord(title="test", turns=[], system_prompt="", **kwargs)


def test_orphan_recovered_with_visibility_and_branch_metadata(store):
    record = chat(hidden=True, branch_group_id="branch-group", branch_number=3)
    name = store.record_file_name(record.id)
    store.save_record(record, name)
    path = store.record_file_path(name)
    before = path.read_bytes()
    items = store.load_index()
    assert len(items) == 1
    assert items[0].id == record.id and items[0].hidden
    assert items[0].branch_group_id == "branch-group" and items[0].branch_number == 3
    assert path.read_bytes() == before
    assert len(store.load_index()) == 1
    assert json.loads(storage.settings.get(AppStorageKeys.CHAT_RECORD_INDEX))[0]["id"] == record.id


def test_failed_index_write_recovers_from_complete_chat_file(store, monkeypatch):
    record = chat()
    with monkeypatch.context() as patch:
        patch.setattr(storage.settings, "set", Mock(side_effect=OSError("index write failed")))
        with pytest.raises(OSError):
            store.save_or_update_record(record)
    path = store.record_file_path(store.record_file_name(record.id))
    assert json.loads(path.read_text())["id"] == record.id
    # Simulate restarting with only the files that reached disk.
    storage.settings.load()
    assert store.load_index()[0].id == record.id


def test_guest_and_default_repaired_separately(store):
    ordinary, guest = chat(), chat()
    store.save_record(ordinary, store.record_file_name(ordinary.id))
    store.save_record(guest, store.record_file_name(guest.id), scope="guest")
    assert [item.id for item in store.load_index()] == [ordinary.id]
    assert [item.id for item in store.load_index(scope="guest")] == [guest.id]


def test_atomic_chat_write_preserves_previous_file_on_failure(store, monkeypatch):
    record = chat()
    name = store.record_file_name(record.id)
    store.save_record(record, name)
    path = store.record_file_path(name)
    before = path.read_bytes()
    record.title = "changed"
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(OSError):
        store.save_record(record, name)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


def test_simultaneous_saves_keep_all_entries(store):
    records = [chat() for _ in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(store.save_or_update_record, records))
    assert {item.id for item in store.load_index()} == {record.id for record in records}


def test_deleted_file_and_duplicate_entries_are_removed(store):
    keep, remove = chat(), chat()
    store.save_or_update_record(keep)
    store.save_or_update_record(remove)
    items = store.load_index()
    store.persist_index(items + [items[0]])
    store.record_file_path(store.record_file_name(remove.id)).unlink()
    assert [item.id for item in store.load_index()] == [keep.id]


def test_bad_orphan_is_not_deleted_or_given_new_identity(store):
    path = store.record_file_path("record-broken.json")
    path.write_text('{"id":')
    wrong = store.record_file_path("record-wrong-name.json")
    wrong.write_text(json.dumps(chat().to_dict()))
    assert store.load_index() == []
    assert path.read_text() == '{"id":'
    assert wrong.exists()
