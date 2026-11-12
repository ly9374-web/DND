"""Snapshot ownership, event replacement and cancellation without paid requests."""
import json
import threading
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn as Turn, SystemPromptRecord
from app.services import foreshadowing, page2_service, system_prompts
from app.services.task_control import GenerationStopped
from app.api.media_clients import OpenRouterVideoAPIClient, ReplicateVideoAPIClient
from web.pages import page2

FINAL = "1. 背景介绍\n固定背景\n2. 世界书\n世界事实\n3. 用户角色\n用户人物\n4. 其他角色\n同伴\n5. DM 的核心任务\n固定DM\n6. 回复格式要求\n固定格式"


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def immediate(task, fn):
    task._fn, task._started = fn, True
    task._run()


class GenerationActionsTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState(page2_operation_scope_id="test", page2_turns=[
            Turn(id="t1", user_message="继续", assistant_message=">当前时间：第8天 14:00，当前地点：学校")],
            page2_story_brain_short="点击时的记忆", page2_story_brain_enabled=False, page2_final_setting_id="final", page2_foreshadowing={"id": "old"},
            page2_generated_media=[])
        self.record = SystemPromptRecord(id="final", title="测试", prompt=FINAL,
                                        foreshadowing={"plans": [{"id": "old", "title": "旧方案"}], "selected_plan_id": "old"})
        self.prompt_state = system_prompts.PromptState(False, [self.record], [], 2, 1, "final")
        self.ctx = page2_service.default_context()
        self.ctx.system_prompt = FINAL
        for patcher in (
            patch.object(page2.st, "session_state", self.state),
            patch.object(page2_service, "load_context_from_settings", return_value=self.ctx),
            patch.object(system_prompts, "load_state", return_value=self.prompt_state),
            patch.object(system_prompts, "_persist"),
            patch.object(page2.settings, "int", return_value=6),
            patch.object(page2, "_upsert_record"),
            patch.object(page2._Page2OperationTask, "start", immediate),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def result(self):
        return {"story": "新故事", "scene_events": [], "time_events": []}

    def test_event_snapshot_survives_continued_chat_and_updates_both_stores(self):
        task = page2._queue_event_generation()
        self.state.page2_turns.append(Turn(id="later", user_message="后来的输入", assistant_message=">当前时间：第9天 18:00，当前地点：家"))
        self.state.page2_story_brain_short = "后来的记忆"
        with patch.object(foreshadowing, "generate", return_value=self.result()) as generate:
            page2._advance_operation_queues()
            page2._advance_operation_queues()
        captured = generate.call_args.kwargs["captured_context"]
        self.assertEqual(captured["clock"]["day"], 8)
        self.assertEqual(captured["clock"]["minute"], 14 * 60)
        self.assertEqual(captured["story_brain"], "点击时的记忆")
        self.assertNotIn("后来的输入", captured["history"])
        self.assertEqual(len(self.record.foreshadowing["plans"]), 2)
        self.assertEqual(self.record.foreshadowing["plans"][0]["id"], "old")
        self.assertEqual(self.state.page2_foreshadowing["story"], "新故事")
        self.assertTrue(self.state.page2_foreshadowing["enabled"])
        self.assertEqual(self.state.page2_turns[-1].id, "later")
        self.assertNotEqual(self.state.page2_foreshadowing["id"], "old")
        self.assertEqual(page2._operation_lane(task), "events")

    def test_settings_entry_uses_edited_body_without_game_chat_or_memory(self):
        task = page2._queue_event_generation(final_setting=FINAL.replace("世界事实", "尚未保存的正文"), record_id="final", update_game=False)
        self.assertIn("尚未保存的正文", task.payload["context"]["final_setting"])
        self.assertEqual(task.payload["context"]["history"], "暂无")
        self.assertEqual(task.payload["context"]["story_brain"], "暂无")
        task.result = self.result()
        page2._apply_operation(task)
        self.assertEqual(self.state.page2_foreshadowing, {"id": "old"})

    def test_stop_event_releases_lane_and_late_result_cannot_replace_plan(self):
        task = page2._queue_event_generation()
        self.state.page2_operation_queue = []
        self.state.page2_active_media_operations = {"events": task}
        page2._stop_generation(task.id)
        task.result = self.result()
        page2._apply_operation(task)
        self.assertEqual(len(self.record.foreshadowing["plans"]), 1)
        self.assertEqual(self.state.page2_foreshadowing, {"id": "old"})
        self.assertFalse(page2.page2_operation_queue_busy())

    def test_failed_event_keeps_existing_plan_and_game(self):
        task = page2._queue_event_generation()
        task.error = ValueError("生成失败")
        with self.assertRaises(ValueError):
            page2._apply_operation(task)
        self.assertEqual(len(self.record.foreshadowing["plans"]), 1)
        self.assertEqual(self.state.page2_foreshadowing, {"id": "old"})

    def test_failed_game_save_rolls_back_prompt_and_current_plan(self):
        task = page2._queue_event_generation()
        task.result = self.result()
        before = deepcopy(self.record.foreshadowing)
        with patch.object(page2, "_upsert_record", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                page2._apply_operation(task)
        self.assertEqual(self.record.foreshadowing, before)
        self.assertEqual(self.state.page2_foreshadowing, {"id": "old"})

    def test_event_and_chat_execute_concurrently(self):
        event = page2._queue_event_generation()
        chat = page2._queue_chat_message(ctx=self.ctx, user_text="继续聊天", hide_user_message=False, story_brain_enabled=False)
        with patch.object(page2._Page2OperationTask, "start", lambda task, fn: setattr(task, "_started", True)), patch.object(page2, "_prepare_operation", return_value=lambda: None):
            page2._advance_operation_queues()
        self.assertIs(self.state.page2_active_operation, chat)
        self.assertIs(self.state.page2_active_media_operations["events"], event)

    def event_work(self):
        base = page2._current_event_plan()
        return {"scope_id": "test", "base": deepcopy(base), "plan": deepcopy(base)}

    def test_current_event_edits_save_only_game_and_keep_consumed_ids(self):
        original = {"id": "active", "title": "本局方案", "enabled": True, "story": "旧背景",
                    "scene_events": [], "time_events": [{"id": "used", "title": "旧名称", "day": 8,
                    "time": "13:00", "event_content": "旧事件", "date": "9999-12-31"}]}
        self.state.page2_foreshadowing = original
        self.state.page2_turns[0].special_events = [{"id": "used", "event_content": "已发生"}]
        work = self.event_work()
        edited = deepcopy(work["plan"])
        edited["story"] = "修改后的背景"
        edited["time_events"][0].update(title="新名称", event_content="修改内容")
        template_before = deepcopy(self.record.foreshadowing)
        with patch.object(page2, "_upsert_record") as persist:
            page2._save_current_event_plan(edited, work)
        persist.assert_called_once()
        self.assertEqual(self.state.page2_foreshadowing["story"], "修改后的背景")
        self.assertNotIn("date", self.state.page2_foreshadowing["time_events"][0])
        self.assertEqual(self.record.foreshadowing, template_before)
        self.assertEqual(foreshadowing.due_events(self.state.page2_foreshadowing, self.state.page2_turns), [])

    def test_current_event_save_rejects_stale_game_or_plan(self):
        work = self.event_work()
        self.state.page2_foreshadowing = {"id": "newly-generated-plan"}
        with patch.object(page2, "_upsert_record") as persist:
            with self.assertRaisesRegex(ValueError, "本局事件已更新"):
                page2._save_current_event_plan(work["plan"], work)
            work["scope_id"] = "another-game"
            with self.assertRaisesRegex(ValueError, "游戏已切换"):
                page2._save_current_event_plan(work["plan"], work)
        persist.assert_not_called()
        self.assertEqual(self.state.page2_foreshadowing, {"id": "newly-generated-plan"})

    def test_invalid_current_event_or_failed_disk_save_keeps_previous_plan(self):
        self.state.page2_foreshadowing = {"id": "active", "scene_events": [], "time_events": []}
        work = self.event_work()
        invalid = deepcopy(work["plan"])
        invalid["time_events"] = [{"id": "x", "title": "事件", "event_content": "内容", "day": 0, "time": "14:00"}]
        with self.assertRaises(ValueError):
            page2._save_current_event_plan(invalid, work)
        changed = deepcopy(work["plan"])
        changed["story"] = "改动"
        with patch.object(page2, "_upsert_record", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                page2._save_current_event_plan(changed, work)
        self.assertEqual(self.state.page2_foreshadowing, work["base"])

    def test_guest_current_event_edit_is_rejected(self):
        self.state.auth_mode = "guest"
        with self.assertRaisesRegex(ValueError, "游客仅可查看"):
            page2._save_current_event_plan({}, self.event_work())

    def test_video_uses_click_time_image_and_fresh_prompt_then_generates_video(self):
        source = GeneratedImageRecord(id="image", provider="grok", prompt="原图", source_turn_id="t1", video_prompt="旧视频提示词")
        self.state.page2_generated_media = [source]
        self.state.page2_video_source = source.id
        tasks = page2._queue_current_image_video(ctx=self.ctx, source_id=source.id, seconds=5)
        self.state.page2_turns[0].assistant_message = "后来修改的故事"
        self.state.page2_generated_media.append(GeneratedImageRecord(id="other", provider="grok", prompt="别的图片"))
        self.state.page2_video_source = "other"
        with patch.object(page2_service, "generate_video_prompt", return_value="新的视频提示词") as prompt, \
             patch.object(page2_service, "generate_video_from_image", return_value=GeneratedImageRecord(provider="grok", prompt="新的视频提示词", media_kind=GeneratedMediaKind.VIDEO)) as video, \
             patch.object(page2.media_store, "cache_generated", side_effect=lambda record: record):
            for _ in range(4):
                page2._advance_operation_queues()
        self.assertIn("第8天", prompt.call_args.kwargs["story_text"])
        self.assertEqual(video.call_args.kwargs["source_record"].id, "image")
        self.assertEqual(video.call_args.kwargs["prompt"], "新的视频提示词")
        self.assertEqual(source.video_prompt, "新的视频提示词")
        self.assertTrue(all(task.is_done() for task in tasks))

    def test_stop_video_prompt_cancels_video_successor_but_leaves_chat(self):
        source = GeneratedImageRecord(id="image", provider="grok", prompt="原图", source_turn_id="t1")
        self.state.page2_generated_media = [source]
        tasks = page2._queue_current_image_video(ctx=self.ctx, source_id=source.id, seconds=5)
        chat = page2._queue_chat_message(ctx=self.ctx, user_text="聊天", hide_user_message=False, story_brain_enabled=False)
        page2._stop_generation(tasks[0].id)
        self.assertEqual(self.state.page2_operation_queue, [chat])
        self.assertTrue(all(task.cancel_event.is_set() for task in tasks))
        page2._stop_generation(chat.id)
        self.assertFalse(chat.cancel_event.is_set())

    def test_background_task_discards_a_return_after_stop(self):
        task = page2._Page2OperationTask(kind="events", label="事件", scope_id="test")
        def request():
            task.cancel_event.set()
            return "迟到结果"
        task.start(request)
        self.assertTrue(task._done.wait(1))
        self.assertIsNone(task.result)

    def test_video_clients_do_not_submit_when_already_cancelled(self):
        stopped = threading.Event()
        stopped.set()
        with patch("app.api.media_clients.requests.post") as post:
            for call in (
                lambda: ReplicateVideoAPIClient.generate_wan_2_2_i2v_fast("https://example.com/image.png", "motion", cancel_event=stopped),
                lambda: OpenRouterVideoAPIClient.generate_video(model="anything", prompt="motion", seconds=5, aspect_ratio="16:9", cancel_event=stopped),
            ):
                with self.assertRaises(GenerationStopped):
                    call()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
