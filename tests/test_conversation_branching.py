import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import storage
from app.config import SettingsStore
from app.models import ChatRecord, GeneratedImageRecord, Page2ConversationTurn
from app.services import chat_records, page2_service
from web.pages import page2


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class ConversationBranchPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_patch = patch.object(
            storage,
            "settings",
            SettingsStore(Path(self.temp.name) / "settings.json"),
        )
        self.records_patch = patch.object(
            storage,
            "CHAT_RECORDS_DIR",
            Path(self.temp.name) / "chats",
        )
        self.settings_patch.start()
        self.records_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.records_patch.stop)

    def _source_record(self):
        record = ChatRecord(
            id="source",
            title="原来的标题",
            turns=[Page2ConversationTurn(id="turn-1", user_message="出发", assistant_message="你走进森林。")],
            system_prompt="世界设定",
            generated_images=[GeneratedImageRecord(provider="test", prompt="森林")],
            story_brain={"legacy": "保留"},
            story_brain_short="当前记忆",
            story_brain_next_update_turn=6,
            story_brain_last_update_turn_id="turn-1",
            story_brain_checkpoints=[{"after_turn_id": "turn-1", "state": {"short": "当前记忆"}}],
            final_setting_id="setting-1",
            hidden=True,
        )
        storage.ChatRecordStore.save_or_update_record(record)
        return record

    def test_first_branch_renames_source_and_copies_the_full_snapshot(self):
        source = self._source_record()

        branch = chat_records.create_branch(source.id)
        persisted_source = chat_records.load_record_by_id(source.id)

        self.assertEqual((persisted_source.title, persisted_source.branch_number), ("1", 1))
        self.assertEqual((branch.title, branch.branch_number), ("2", 2))
        self.assertEqual(branch.branch_group_id, persisted_source.branch_group_id)
        self.assertNotEqual(branch.id, persisted_source.id)
        self.assertEqual(
            branch.to_dict()["turns"],
            persisted_source.to_dict()["turns"],
        )
        self.assertEqual(
            branch.to_dict()["generated_images"],
            persisted_source.to_dict()["generated_images"],
        )
        for field in (
            "system_prompt",
            "story_brain",
            "story_brain_short",
            "story_brain_next_update_turn",
            "story_brain_last_update_turn_id",
            "story_brain_checkpoints",
            "final_setting_id",
            "hidden",
        ):
            self.assertEqual(getattr(branch, field), getattr(persisted_source, field))

        branch.turns[0].assistant_message = "分支中的新内容"
        storage.ChatRecordStore.save_or_update_record(branch)
        self.assertEqual(
            chat_records.load_record_by_id(source.id).turns[0].assistant_message,
            "你走进森林。",
        )

    def test_next_branch_uses_numbering(self):
        source = self._source_record()
        second = chat_records.create_branch(source.id)
        chat_records.rename_record(second.id, "手动名称")

        third = chat_records.create_branch(source.id)
        fourth = chat_records.create_branch(second.id)

        self.assertEqual((third.title, third.branch_number), ("3", 3))
        self.assertEqual((fourth.title, fourth.branch_number), ("4", 4))
        self.assertEqual(
            len({second.branch_group_id, third.branch_group_id, fourth.branch_group_id}),
            1,
        )

    def test_dialog_defaults_and_confirmed_titles_persist(self):
        source = self._source_record()
        self.assertEqual(chat_records.branch_title_defaults(source.id), ("1", "1", "2"))

        second = chat_records.create_branch(
            source.id, source_title="序章", branch_title="第一幕"
        )
        self.assertEqual(chat_records.load_record_by_id(source.id).title, "序章")
        self.assertEqual(chat_records.load_record_by_id(second.id).title, "第一幕")
        self.assertEqual(
            chat_records.branch_title_defaults(second.id), ("第一幕", "2", "3")
        )

        third = chat_records.create_branch(
            second.id, source_title="第二幕", branch_title="第三幕"
        )
        self.assertEqual(chat_records.load_record_by_id(second.id).title, "第二幕")
        self.assertEqual(chat_records.load_record_by_id(third.id).title, "第三幕")
        self.assertEqual(third.branch_number, 3)

    def test_cancel_can_restore_numeric_titles(self):
        source = self._source_record()
        second = chat_records.create_branch(
            source.id, source_title="序章", branch_title="第一幕"
        )
        _, source_default, branch_default = chat_records.branch_title_defaults(second.id)
        third = chat_records.create_branch(
            second.id, source_title=source_default, branch_title=branch_default
        )

        self.assertEqual(chat_records.load_record_by_id(second.id).title, "2")
        self.assertEqual(chat_records.load_record_by_id(third.id).title, "3")

    def test_normal_chat_autosave_preserves_branch_identity(self):
        source = self._source_record()
        branch = chat_records.create_branch(source.id)
        branch.turns.append(Page2ConversationTurn(user_message="分支继续"))

        page2_service.upsert_chat_record(
            record_id=branch.id,
            turns=branch.turns,
            generated_media=branch.generated_images,
            system_prompt=branch.system_prompt,
            story_brain=branch.story_brain,
            story_brain_short=branch.story_brain_short,
        )

        restored = chat_records.load_record_by_id(branch.id)
        self.assertEqual(restored.branch_group_id, branch.branch_group_id)
        self.assertEqual(restored.branch_number, 2)
        self.assertEqual(restored.title, "2")

    def test_old_records_without_branch_fields_still_load(self):
        record = ChatRecord.from_dict({"id": "legacy", "title": "旧记录", "turns": []})
        self.assertEqual(record.branch_group_id, "")
        self.assertIsNone(record.branch_number)
        restored = ChatRecord.from_dict(record.to_dict())
        self.assertEqual(restored.branch_group_id, "")
        self.assertIsNone(restored.branch_number)


class ConversationBranchInteractionTests(unittest.TestCase):
    def test_non_empty_sidebar_action_switches_to_the_new_branch_without_clearing(self):
        turn = Page2ConversationTurn(user_message="继续", assistant_message="回复")
        state = SessionState(
            page2_turns=[turn],
            page2_generated_media=[],
            page2_record_id="source",
            page2_loaded_record_id="source",
            page2_operation_queue=[],
            page2_active_operation=None,
            page2_story_brain_short="记忆",
            page2_story_brain_archive={},
            page2_chat_edit_target=None,
        )
        branch = SimpleNamespace(id="branch-2", branch_number=2)
        with (
            patch.object(page2.st, "session_state", state),
            patch.object(page2, "_upsert_record") as save,
            patch.object(page2.chat_records, "create_branch", return_value=branch) as create,
        ):
            self.assertTrue(page2.branch_or_start_new_conversation())

        save.assert_called_once_with()
        create.assert_called_once_with("source", scope=None)
        self.assertIs(state.page2_turns[0], turn)
        self.assertEqual(state.page2_record_id, "branch-2")
        self.assertEqual(state.page2_loaded_record_id, "branch-2")
        self.assertEqual(state.page2_operation_scope_id, "record:branch-2")

    def test_empty_sidebar_action_keeps_the_original_new_conversation_behavior(self):
        state = SessionState(
            page2_turns=[],
            page2_generated_media=[],
            page2_operation_queue=[],
            page2_active_operation=None,
            page2_story_brain_short="",
            page2_story_brain_archive={},
        )
        with (
            patch.object(page2.st, "session_state", state),
            patch.object(page2, "start_new_conversation", return_value=True) as start,
            patch.object(page2.chat_records, "create_branch") as create,
        ):
            self.assertTrue(page2.branch_or_start_new_conversation())

        start.assert_called_once_with()
        create.assert_not_called()

    def test_sidebar_action_passes_both_dialog_titles_to_branch_creation(self):
        state = SessionState(
            page2_turns=[Page2ConversationTurn(user_message="继续")],
            page2_generated_media=[],
            page2_record_id="source",
            page2_loaded_record_id="source",
            page2_operation_queue=[],
            page2_active_operation=None,
            page2_story_brain_short="",
            page2_story_brain_archive={},
            page2_chat_edit_target=None,
        )
        branch = SimpleNamespace(id="branch-2", branch_number=2)
        with (
            patch.object(page2.st, "session_state", state),
            patch.object(page2, "_upsert_record"),
            patch.object(page2.chat_records, "create_branch", return_value=branch) as create,
        ):
            self.assertTrue(
                page2.branch_or_start_new_conversation(
                    source_title="序章", branch_title="第一幕"
                )
            )

        create.assert_called_once_with(
            "source", scope=None, source_title="序章", branch_title="第一幕"
        )


if __name__ == "__main__":
    unittest.main()
