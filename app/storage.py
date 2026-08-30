import json
from dataclasses import asdict

from app.config import AppStorageKeys, CHAT_RECORDS_DIR, debug_log, settings
from app.models import (
    ChatRecord,
    ChatRecordIndexItem,
    now_iso,
)


class ChatRecordStore:
    """
    对应 Swift 里的 private enum ChatRecordStore。

    功能：
    1. 把每一条聊天记录保存为单独的 json 文件
    2. 用 chatRecordIndex 保存记录索引
    3. 支持读取、保存、重命名、删除
    4. 支持从旧版 chatRecords JSON 迁移到新文件结构
    """

    RECORD_FILE_NAME_PREFIX = "record-"
    RECORD_FILE_NAME_SUFFIX = ".json"
    GUEST_SCOPE = "guest"

    # =========================
    # Public
    # =========================

    @classmethod
    def migrate_legacy_chat_records_if_needed(cls, scope: str | None = None):
        """
        对应 Swift:
        migrateLegacyChatRecordsIfNeeded()

        旧版是所有聊天记录都存在 AppStorageKeys.chatRecords 里。
        新版是每条聊天记录单独一个 json 文件，并维护 chatRecordIndex。
        """
        # 只对默认 scope 做旧版迁移，避免把历史数据误迁到 guest。
        if scope:
            return

        legacy_json = settings.get(AppStorageKeys.CHAT_RECORDS, "")
        if not isinstance(legacy_json, str):
            return

        trimmed_legacy = legacy_json.strip()
        if not trimmed_legacy:
            return

        try:
            raw_records = json.loads(trimmed_legacy)
            if not isinstance(raw_records, list):
                raise ValueError("legacy chatRecords is not a list")
        except Exception as exc:
            debug_log(
                "[ChatRecordStore] Legacy migration decode failed; clearing legacy JSON.",
                exc,
            )
            settings.set(AppStorageKeys.CHAT_RECORDS, "")
            return

        index = cls.load_index(scope=scope)
        index_by_id = {item.id: item for item in index}

        migrated_count = 0

        for raw_record in raw_records:
            try:
                record = ChatRecord.from_dict(raw_record)
                file_name = cls.record_file_name(record.id)

                cls.save_record(record, file_name, scope=scope)

                index_by_id[record.id] = ChatRecordIndexItem(
                    id=record.id,
                    title=record.title,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    file_name=file_name,
                )

                migrated_count += 1

            except Exception as exc:
                debug_log(
                    "[ChatRecordStore] Legacy migration save failed:",
                    exc,
                )

        cls.persist_index(list(index_by_id.values()), scope=scope)
        settings.set(AppStorageKeys.CHAT_RECORDS, "")

        debug_log(
            "[ChatRecordStore] Legacy migration completed. Migrated:",
            migrated_count,
        )

    @classmethod
    def load_index_sorted(cls, scope: str | None = None):
        """
        对应 Swift:
        loadIndexSorted()

        按 updatedAt 倒序排列。
        如果 updatedAt 一样，就按 createdAt 倒序。
        """
        return sorted(
            cls.load_index(scope=scope),
            key=lambda item: (item.updated_at, item.created_at),
            reverse=True,
        )

    @classmethod
    def load_record(cls, item, scope: str | None = None):
        """
        对应 Swift:
        loadRecord(for item: ChatRecordIndexItem)
        """
        return cls.load_record_by_file_name(item.file_name, scope=scope)

    @classmethod
    def save_or_update_record(cls, record, scope: str | None = None):
        """
        对应 Swift:
        saveOrUpdateRecord(_ record: ChatRecord)
        """
        file_name = cls.record_file_name(record.id)

        cls.save_record(record, file_name, scope=scope)

        cls.upsert_index_item(
            ChatRecordIndexItem(
                id=record.id,
                title=record.title,
                created_at=record.created_at,
                updated_at=record.updated_at,
                file_name=file_name,
            ),
            scope=scope,
        )

    @classmethod
    def rename_record(cls, record_id, new_title, scope: str | None = None):
        """
        对应 Swift:
        renameRecord(id: UUID, newTitle: String)
        """
        trimmed = str(new_title or "").strip()
        if not trimmed:
            return

        index = cls.load_index(scope=scope)

        target_index = None
        target_item = None

        for i, item in enumerate(index):
            if item.id == record_id:
                target_index = i
                target_item = item
                break

        if target_item is None:
            return

        try:
            record = cls.load_record_by_file_name(target_item.file_name, scope=scope)

            record.title = trimmed
            record.updated_at = now_iso()

            cls.save_record(record, target_item.file_name, scope=scope)

            index[target_index].title = trimmed
            index[target_index].updated_at = record.updated_at

            cls.persist_index(index, scope=scope)

        except Exception as exc:
            debug_log("[ChatRecordStore] Rename failed:", exc)

    @classmethod
    def delete_record(cls, record_id, scope: str | None = None):
        """
        对应 Swift:
        deleteRecord(id: UUID)
        """
        index = cls.load_index(scope=scope)

        kept = []

        for item in index:
            if item.id == record_id:
                try:
                    path = cls.record_file_path(item.file_name, scope=scope)
                    if path.exists():
                        path.unlink()
                except Exception as exc:
                    debug_log("[ChatRecordStore] Delete file failed:", exc)
            else:
                kept.append(item)

        cls.persist_index(kept, scope=scope)

    @classmethod
    def clear_scope(cls, scope: str):
        scope = str(scope or "").strip()
        if not scope:
            return

        try:
            scope_dir = cls._scope_dir(scope)
            if scope_dir.exists():
                for path in scope_dir.glob(f"{cls.RECORD_FILE_NAME_PREFIX}*{cls.RECORD_FILE_NAME_SUFFIX}"):
                    try:
                        path.unlink()
                    except Exception as exc:
                        debug_log("[ChatRecordStore] Clear scope unlink failed:", exc)
        except Exception as exc:
            debug_log("[ChatRecordStore] Clear scope failed:", exc)

        try:
            cls.persist_index([], scope=scope)
        except Exception as exc:
            debug_log("[ChatRecordStore] Clear scope persist index failed:", exc)

    # =========================
    # Private helpers
    # =========================

    @classmethod
    def load_index(cls, scope: str | None = None):
        """
        对应 Swift private:
        loadIndex()
        """
        raw = settings.get(cls._scope_index_key(scope), "")

        if not raw:
            return []

        if isinstance(raw, list):
            items = raw
        else:
            try:
                items = json.loads(raw)
            except Exception:
                return []

        result = []

        for item in items:
            if not isinstance(item, dict):
                continue

            try:
                result.append(ChatRecordIndexItem.from_dict(item))
            except Exception as exc:
                debug_log("[ChatRecordStore] Bad index item:", exc)

        return result

    @classmethod
    def persist_index(cls, items, scope: str | None = None):
        """
        对应 Swift private:
        persistIndex(_ items: [ChatRecordIndexItem])
        """
        payload = []

        for item in items:
            if hasattr(item, "to_dict"):
                payload.append(item.to_dict())
            else:
                payload.append(asdict(item))

        settings.set(
            cls._scope_index_key(scope),
            json.dumps(payload, ensure_ascii=False),
        )

    @classmethod
    def upsert_index_item(cls, item, scope: str | None = None):
        """
        对应 Swift private:
        upsertIndexItem(_ item: ChatRecordIndexItem)
        """
        index = cls.load_index(scope=scope)

        for i, existing in enumerate(index):
            if existing.id == item.id:
                index[i] = item
                cls.persist_index(index, scope=scope)
                return

        index.append(item)
        cls.persist_index(index, scope=scope)

    @classmethod
    def record_file_name(cls, record_id):
        """
        对应 Swift private:
        recordFileName(for id: UUID)
        """
        return (
            cls.RECORD_FILE_NAME_PREFIX
            + str(record_id)
            + cls.RECORD_FILE_NAME_SUFFIX
        )

    @classmethod
    def record_file_path(cls, file_name, scope: str | None = None):
        """
        对应 Swift private:
        recordFileURL(fileName:)
        """
        base_dir = cls._scope_dir(scope)
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / file_name

    @classmethod
    def load_record_by_file_name(cls, file_name, scope: str | None = None):
        """
        对应 Swift private:
        loadRecord(fileName:)
        """
        path = cls.record_file_path(file_name, scope=scope)

        data = json.loads(path.read_text(encoding="utf-8"))

        return ChatRecord.from_dict(data)

    @classmethod
    def save_record(cls, record, file_name, scope: str | None = None):
        """
        对应 Swift private:
        saveRecord(_ record:fileName:)
        """
        path = cls.record_file_path(file_name, scope=scope)

        if hasattr(record, "to_dict"):
            payload = record.to_dict()
        else:
            payload = asdict(record)

        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _scope_dir(cls, scope: str | None):
        if str(scope or "").strip() == cls.GUEST_SCOPE:
            return CHAT_RECORDS_DIR / cls.GUEST_SCOPE
        return CHAT_RECORDS_DIR

    @classmethod
    def _scope_index_key(cls, scope: str | None) -> str:
        if str(scope or "").strip() == cls.GUEST_SCOPE:
            return AppStorageKeys.GUEST_CHAT_RECORD_INDEX
        return AppStorageKeys.CHAT_RECORD_INDEX
