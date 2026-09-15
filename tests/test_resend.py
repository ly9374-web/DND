import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app.models import ChatRecord, Page2ConversationTurn
from app.services import page2_service, story_brain_history
from web.pages import page2


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def memory(text, anchor="", next_turn=None):
    return {"short": text, "last_update_turn_id": anchor, "next_update_turn": next_turn, "retry_pending": False}


class ResendTests(unittest.TestCase):
    def setUp(self):
        self.turns = [Page2ConversationTurn(id=f"t{i}", user_message=f"用户{i}", assistant_message=f"回复{i}") for i in range(1, 4)]
        self.old = memory("上一版记忆", "t1", 3)
        self.current = memory("包含旧回复3的记忆", "t3", 5)
        self.history = [{"after_turn_id": "t1", "state": self.old}, {"after_turn_id": "t3", "state": self.current}]
        self.session = SessionState(
            page2_turns=deepcopy(self.turns), page2_operation_scope_id="test", page2_operation_queue=[],
            page2_active_operation=None, page2_story_brain_short_editor_nonce=0,
        )
        patcher = patch.object(page2.st, "session_state", self.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        page2._set_memory_state(self.current, self.history)

    def task(self):
        return page2._Page2OperationTask(kind="resend", label="重发", scope_id="test", payload={
            "ctx": page2_service.default_context(), "target_turn_id": "t3", "story_brain_enabled": True,
        })

    def test_prepare_excludes_last_round_and_uses_prior_memory_without_live_mutation(self):
        task = self.task()
        before = deepcopy(dict(self.session))
        with patch.object(page2_service, "send_message", return_value="新回复") as send:
            result = page2._prepare_operation(task)()
        self.assertEqual(result, "新回复")
        self.assertEqual([t.id for t in send.call_args.kwargs["turns"]], ["t1", "t2"])
        self.assertEqual(send.call_args.kwargs["user_message"], "用户3")
        self.assertEqual(send.call_args.kwargs["story_brain_short"], "上一版记忆")
        for key in ("page2_turns", "page2_story_brain_short", "page2_story_brain_checkpoints"):
            self.assertEqual(self.session[key], before[key])

    def test_success_replaces_only_last_round_and_commits_rewound_memory(self):
        task = self.task()
        page2._prepare_operation(task)
        task.result = "新的回复3"
        with patch.object(page2, "_upsert_record") as save, patch.object(page2, "_schedule_chat_followups") as followup:
            page2._apply_operation(task)
        save.assert_called_once()
        followup.assert_called_once()
        self.assertEqual(len(self.session.page2_turns), 3)
        self.assertEqual(self.session.page2_turns[:2], self.turns[:2])
        self.assertEqual(self.session.page2_turns[-1].user_message, "用户3")
        self.assertEqual(self.session.page2_turns[-1].assistant_message, "新的回复3")
        self.assertNotEqual(self.session.page2_turns[-1].id, "t3")
        self.assertEqual(page2._memory_state(), self.old)
        self.assertEqual(len(self.session.page2_story_brain_checkpoints), 1)

    def test_network_and_empty_reply_failures_leave_original_record_and_memory(self):
        for error in [RuntimeError("网络中断"), None]:
            task = self.task()
            page2._prepare_operation(task)
            task.error = error
            task.result = ""
            with patch.object(page2, "_upsert_record") as save:
                with self.assertRaisesRegex(RuntimeError, "原问答和记忆已保留"):
                    page2._apply_operation(task)
            save.assert_not_called()
            self.assertEqual(self.session.page2_turns, self.turns)
            self.assertEqual(page2._memory_state(), self.current)
            self.assertEqual(self.session.page2_story_brain_checkpoints, self.history)

    def test_save_failure_restores_live_state_and_does_not_schedule_followups(self):
        task = self.task()
        page2._prepare_operation(task)
        task.result = "新回复"
        with patch.object(page2, "_upsert_record", side_effect=OSError("不能写入")), patch.object(page2, "_schedule_chat_followups") as followup:
            with self.assertRaises(OSError):
                page2._apply_operation(task)
        followup.assert_not_called()
        self.assertEqual(self.session.page2_turns, self.turns)
        self.assertEqual(page2._memory_state(), self.current)

    def test_edited_hidden_dice_and_failed_messages_resend_the_exact_stored_user(self):
        for text, hidden in [("编辑后的文字", False), ("掷骰结果：“12”", False), ("继续发展", True), ("开始", False)]:
            self.session.page2_turns[-1].user_message = text
            self.session.page2_turns[-1].is_user_message_hidden = hidden
            self.session.page2_turns[-1].assistant_message = "请求失败"
            task = self.task()
            with patch.object(page2_service, "send_message", return_value="新回复") as send:
                page2._prepare_operation(task)()
            self.assertEqual(send.call_args.kwargs["user_message"], text)
            self.assertEqual(task.meta["replacement"].is_user_message_hidden, hidden)

    def test_stale_record_cannot_be_replaced(self):
        task = self.task()
        page2._prepare_operation(task)
        task.result = "新回复"
        self.session.page2_turns[-1].user_message = "发生了其他修改"
        with patch.object(page2, "_upsert_record") as save:
            with self.assertRaisesRegex(RuntimeError, "记录已变化"):
                page2._apply_operation(task)
        save.assert_not_called()

    def test_resend_queues_behind_running_operations_and_keeps_empty_guard(self):
        ctx = page2_service.default_context()
        for kind in ["chat", "resend", "story_brain", "image", "video"]:
            self.session.page2_active_operation = page2._Page2OperationTask(kind=kind, label=kind, scope_id="test")
            self.assertTrue(page2._queue_resend(ctx=ctx, story_brain_enabled=True))
            self.assertEqual(
                [task.kind for task in self.session.page2_operation_queue],
                ["resend"],
            )
            if kind in {"chat", "resend"}:
                self.assertEqual(
                    self.session.page2_operation_queue[0].payload["target_turn_id"],
                    "",
                )
            else:
                self.assertEqual(
                    self.session.page2_operation_queue[0].payload["target_turn_id"],
                    "t3",
                )
            self.session.page2_operation_queue = []
        self.session.page2_active_operation = None
        self.assertTrue(page2._queue_resend(ctx=ctx, story_brain_enabled=True))
        self.assertTrue(page2._operation_kind_busy("chat"))
        self.session.page2_operation_queue = []
        self.session.page2_turns = []
        self.assertFalse(page2._queue_resend(ctx=ctx, story_brain_enabled=True))
        for text in ["/重发", "／重发", "  /重发\n"]:
            self.assertTrue(page2._is_resend_command(text))
        for text in ["请/重发", "/重发 修改", "/重发/重发"]:
            self.assertFalse(page2._is_resend_command(text))

    def test_deferred_resend_targets_latest_turn_when_it_reaches_front(self):
        task = self.task()
        task.payload["target_turn_id"] = ""
        newer = Page2ConversationTurn(
            id="t4", user_message="排队期间完成的消息", assistant_message="新回复"
        )
        self.session.page2_turns.append(newer)
        with patch.object(page2_service, "send_message", return_value="重发回复") as send:
            page2._prepare_operation(task)()
        self.assertEqual(send.call_args.kwargs["user_message"], "排队期间完成的消息")

    def test_snapshots_evict_two_updates_ago_and_continuous_undo_reports_limit(self):
        first = memory("版本1", "t1", 2)
        second = memory("版本2", "t2", 3)
        third = memory("版本3", "t3", 4)
        history = story_brain_history.record_update([], memory(""), first, self.turns, "t1")
        history = story_brain_history.record_update(history, first, second, self.turns, "t2")
        history = story_brain_history.record_update(history, second, third, self.turns, "t3")
        self.assertEqual([s["state"]["short"] for s in history], ["版本2", "版本3"])
        page2._set_memory_state(third, history)
        with patch.object(page2, "_upsert_record"):
            self.assertTrue(page2._undo_last_turn())
            self.assertEqual(page2._memory_state(), second)
            self.assertTrue(page2._undo_last_turn())
        self.assertEqual(len(self.session.page2_turns), 1)
        self.assertEqual(page2._memory_state(), second)
        self.assertIn("超出", self.session.page2_operation_notice)

    def test_undo_before_memory_boundary_keeps_current_memory(self):
        page2._set_memory_state(self.old, self.history[:1])
        with patch.object(page2, "_upsert_record"):
            page2._undo_last_turn()
        self.assertEqual(page2._memory_state(), self.old)

    def test_legacy_memory_never_claims_a_missing_snapshot_was_restored(self):
        legacy = memory("旧记录记忆", "t3")
        restored, _, notice = story_brain_history.rewind(legacy, [], self.turns, self.turns[:-1])
        self.assertEqual(restored, legacy)
        self.assertIn("旧记录没有快照", notice)

    def test_checkpoint_roundtrip_is_bounded_and_old_records_remain_readable(self):
        record = ChatRecord(title="测试", system_prompt="", turns=self.turns, story_brain_short="当前", story_brain_checkpoints=self.history * 3)
        restored = ChatRecord.from_dict(json.loads(json.dumps(record.to_dict())))
        self.assertEqual(restored.story_brain_checkpoints, self.history)
        self.assertEqual(ChatRecord.from_dict({}).story_brain_checkpoints, [])

    def test_persist_reopen_and_resend_keep_snapshots_and_record_ownership(self):
        from app import storage
        from app.config import SettingsStore
        from app.services import chat_records
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            with patch.object(storage, "settings", store), patch.object(storage, "CHAT_RECORDS_DIR", Path(directory) / "chats"):
                record_id = page2_service.upsert_chat_record(
                    record_id="saved", turns=self.turns, generated_media=[], system_prompt="设定",
                    story_brain={}, story_brain_short=self.current["short"],
                    story_brain_last_update_turn_id="t3", story_brain_next_update_turn=5,
                    story_brain_checkpoints=self.history, final_setting_id="final-test", hidden=False,
                )
                self.session.page2_loaded_record_id = ""
                self.session.hidden_unlocked = True
                with patch.object(page2, "get_arg", return_value=record_id), patch.object(page2_service, "load_context_from_settings", return_value=page2_service.default_context()), patch.object(page2_service, "save_context_to_settings"), patch.object(page2.url_favorites, "clear_preview_url"):
                    page2._load_record_from_nav_if_needed()
                self.assertEqual(self.session.page2_story_brain_checkpoints, self.history)
                self.assertTrue(self.session.page2_chat_scroll_to_bottom_request)
                task = self.task()
                task.scope_id = "record:saved"
                page2._prepare_operation(task)
                # Request failure cannot touch the original on disk.
                task.error = RuntimeError("请求失败")
                with self.assertRaises(RuntimeError):
                    page2._apply_operation(task)
                self.assertEqual(chat_records.load_record_by_id(record_id).turns[-1].assistant_message, "回复3")
                task.error, task.result = None, "重发成功"
                with patch.object(page2_service, "load_context_from_settings", return_value=page2_service.default_context()), patch.object(page2, "_schedule_chat_followups"):
                    page2._apply_operation(task)
                saved = chat_records.load_record_by_id(record_id)
                self.assertEqual(saved.turns[-1].assistant_message, "重发成功")
                self.assertEqual(saved.story_brain_short, self.old["short"])
                self.assertEqual(saved.story_brain_checkpoints, self.history[:1])
                self.assertFalse(saved.hidden)

    def test_successful_memory_updates_persist_only_current_and_previous_versions(self):
        page2._set_memory_state(memory(""), [])
        with patch.object(page2, "_upsert_record"):
            for i in range(1, 4):
                task = page2._Page2OperationTask(kind="story_brain", label="记忆", scope_id="test")
                task.meta = {"turn_id": f"t{i}", "last_processed_turn_id": f"t{i}", "completed_turn_count": i}
                task.result = f"记忆{i}"
                page2._apply_story_brain_update_task(task)
        self.assertEqual([s["state"]["short"] for s in self.session.page2_story_brain_checkpoints], ["记忆2", "记忆3"])

    def test_undo_last_turn_persists_an_empty_existing_chat(self):
        with patch.object(page2_service.ChatRecordStore, "load_index", return_value=[]), patch.object(page2_service.ChatRecordStore, "save_or_update_record") as save:
            page2_service.upsert_chat_record(record_id="existing", turns=[], generated_media=[], system_prompt="", story_brain={})
        self.assertEqual(save.call_args.args[0].turns, [])

    def test_failed_memory_update_does_not_evict_snapshots(self):
        task = page2._Page2OperationTask(kind="story_brain", label="记忆", scope_id="test")
        task.error = RuntimeError("更新失败")
        page2._apply_story_brain_update_task(task)
        self.assertEqual(self.session.page2_story_brain_checkpoints, self.history)


if __name__ == "__main__":
    unittest.main()
