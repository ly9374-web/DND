import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import GeneratedImageRecord, Page2ConversationTurn, CHAT_MODEL_OPTIONS
from app.services import page2_service
from app.services.video_prompts import (
    MissingVideoStorySource, VIDEO_PROMPT_SYSTEM_PROMPT,
    latest_image_prompt_source, resolve_video_story,
)
from web.pages import page2


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class VideoPromptTests(unittest.TestCase):
    def setUp(self):
        self.turns = [Page2ConversationTurn(id=f"t{i}", user_message=f"输入{i}",
                      assistant_message=f"故事{i}") for i in range(1, 9)]
        self.image = GeneratedImageRecord(provider="grok", prompt="蓝衣女子站在画面左侧",
                                         source_turn_id="t8", previous_source_turn_id="t7")
        self.ctx = page2_service.default_context()
        self.session = SessionState(
            page2_turns=self.turns, page2_generated_media=[self.image],
            page2_operation_scope_id="test", page2_operation_queue=[],
            page2_active_operation=None, page2_operation_outputs={},
        )
        patcher = patch.object(page2.st, "session_state", self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def task(self, kind, **payload):
        return page2._Page2OperationTask(kind=kind, label=kind, scope_id="test",
            payload={"ctx": self.ctx, "source_id": self.image.id, **payload})

    def test_original_turn_and_current_edits_take_precedence_over_latest_turn(self):
        self.turns.append(Page2ConversationTurn(id="t9", assistant_message="不应该取的新故事"))
        self.turns[7].assistant_message = "第八轮编辑后"
        self.assertEqual(resolve_video_story(self.image, self.turns), "第八轮编辑后")

    def test_undo_only_falls_back_to_original_predecessor_not_reused_round_number(self):
        replacement = Page2ConversationTurn(id="new8", assistant_message="新的第八轮")
        self.assertEqual(resolve_video_story(self.image, self.turns[:-1] + [replacement]), "故事7")
        with self.assertRaisesRegex(ValueError, "上一轮也不存在"):
            resolve_video_story(self.image, self.turns[:-2] + [replacement])
        self.image.source_turn_id = "t1"
        self.image.previous_source_turn_id = ""
        with self.assertRaises(ValueError):
            resolve_video_story(self.image, [])

    def test_blank_existing_assistant_does_not_silently_use_another_turn(self):
        self.turns[-1].assistant_message = " "
        with self.assertRaisesRegex(ValueError, "没有可用的助手回复"):
            resolve_video_story(self.image, self.turns)

    def test_legacy_source_requires_manual_story_and_roundtrip_preserves_all_fields(self):
        legacy = GeneratedImageRecord.from_dict({"provider": "grok", "prompt": "旧图"})
        with self.assertRaises(MissingVideoStorySource):
            resolve_video_story(legacy, self.turns)
        legacy.video_story_text = "粘贴的那一轮助手正文"
        legacy.video_prompt = "蓝衣女子转头微笑。"
        restored = GeneratedImageRecord.from_dict(json.loads(json.dumps(legacy.to_dict())))
        self.assertEqual(resolve_video_story(restored, []), legacy.video_story_text)
        self.assertEqual(restored.video_prompt, legacy.video_prompt)
        self.assertEqual(GeneratedImageRecord.from_dict(self.image.to_dict()).source_turn_id, "t8")

    def test_request_uses_only_image_prompt_story_and_builtin_system_for_each_chat_model(self):
        for model in CHAT_MODEL_OPTIONS:
            self.ctx.selected_chat_model = model
            long_reply = "她转身微笑。" * 60
            with patch.object(page2_service.DeepSeekAPIClient, "send_message", return_value=long_reply) as deepseek, \
                 patch.object(page2_service.GrokAPIClient, "send_message", return_value=long_reply) as grok:
                result = page2_service.generate_video_prompt(ctx=self.ctx, image_prompt=self.image.prompt, story_text="编辑后故事")
            client = deepseek if deepseek.called else grok
            args = client.call_args.kwargs
            self.assertEqual(args["system_prompt"], VIDEO_PROMPT_SYSTEM_PROMPT)
            self.assertEqual(args["context_messages"], [])
            self.assertEqual(args["user_message"], "图片prompt：\n蓝衣女子站在画面左侧\n\n本轮故事内容：\n编辑后故事")
            expected_model = page2_service.deepseek_api_model_for_chat_model(model) or page2_service.grok_api_model_for_chat_model(model)
            self.assertEqual(args["model"], expected_model)
            self.assertEqual(result, long_reply)  # 200 字仅为提示词要求，不截断、不再次调用。
            self.assertEqual(deepseek.call_count + grok.call_count, 1)

    def test_missing_inputs_and_empty_response_do_not_fill_editor(self):
        with patch.object(page2_service, "_send_with_current_chat_model", return_value="") as send:
            for image, story in [("", "正文"), ("图片", "")]:
                with self.assertRaises(ValueError):
                    page2_service.generate_video_prompt(ctx=self.ctx, image_prompt=image, story_text=story)
            send.assert_not_called()
            with self.assertRaisesRegex(ValueError, "没有返回可用"):
                page2_service.generate_video_prompt(ctx=self.ctx, image_prompt="图片", story_text="故事")
        with patch.object(page2_service, "_video_input_image_bytes") as prepare:
            with self.assertRaisesRegex(ValueError, "不能为空"):
                page2_service.generate_video_from_image(ctx=self.ctx, source_record=self.image, prompt=" \n", seconds=5)
            prepare.assert_not_called()

    def test_image_source_survives_later_turns_and_queued_prompt_dependency(self):
        prompt_task = self.task("image_prompt")
        with patch.object(page2_service, "generate_image_prompt", return_value="第八轮图片prompt"):
            prompt_task.result = page2._prepare_operation(prompt_task)()
        page2._apply_operation(prompt_task)
        self.turns.append(Page2ConversationTurn(id="t9", assistant_message="新故事"))
        for payload in [
            {"prompt_operation_id": prompt_task.id},
            {"prompt": "手动编辑过的第八轮图片prompt", "image_prompt_source": self.session.page2_image_prompt_source},
        ]:
            task = self.task("image", provider="grok", **payload)
            with patch.object(page2_service, "generate_image", return_value=GeneratedImageRecord(provider="grok", prompt="实际生成prompt")):
                task.result = page2._prepare_operation(task)()
            with patch.object(page2, "_upsert_record"), patch.object(page2.url_favorites, "clear_preview_url"):
                page2._apply_operation(task)
            self.assertEqual(task.result.source_turn_id, "t8")
            self.assertEqual(task.result.previous_source_turn_id, "t7")
        self.assertEqual(latest_image_prompt_source(self.turns)["source_turn_id"], "t9")

    def test_result_updates_only_source_image_and_does_not_add_chat_or_video(self):
        other = GeneratedImageRecord(provider="grok", prompt="另一张", video_prompt="另一张的草稿")
        self.session.page2_generated_media.append(other)
        self.session.page2_selected_media_id = other.id
        task = self.task("video_prompt")
        with patch.object(page2_service, "generate_video_prompt", return_value="她转头微笑。") as generate:
            task.result = page2._prepare_operation(task)()
        self.assertEqual(generate.call_args.kwargs["story_text"], "故事8")
        with patch.object(page2, "_upsert_record") as save:
            page2._apply_operation(task)
        save.assert_called_once()
        self.assertEqual(self.image.video_prompt, "她转头微笑。")
        self.assertEqual(other.video_prompt, "另一张的草稿")
        self.assertEqual(len(self.session.page2_generated_media), 2)
        self.assertEqual(len(self.turns), 8)

    def test_preview_selection_also_selects_that_images_video_draft(self):
        self.session.page2_media_record_select = self.image.id
        with patch.object(page2.url_favorites, "activate_generated_media_selection") as activate:
            page2._activate_media_selection()
        activate.assert_called_once_with("page2", "page2_selected_media_id", "page2_media_record_select")
        self.assertEqual(self.session.page2_pending_video_source, self.image.id)

    def test_failed_or_stale_completion_does_not_overwrite_draft(self):
        task = self.task("video_prompt")
        page2._prepare_operation(task)
        self.image.video_prompt = "手工修改"
        task.result = "迟到的结果"
        with patch.object(page2, "_upsert_record") as save:
            with self.assertRaisesRegex(ValueError, "未覆盖"):
                page2._apply_operation(task)
            save.assert_not_called()
        self.assertEqual(self.image.video_prompt, "手工修改")
        with patch.object(page2, "_upsert_record", side_effect=OSError("磁盘写入失败")):
            with self.assertRaises(OSError):
                page2._save_image_video_field(self.image.id, "video_prompt", "新草稿")
        self.assertEqual(self.image.video_prompt, "手工修改")

    def test_queued_video_prompt_waits_for_image_and_missing_source_opens_toast(self):
        task = self.task("video_prompt", image_operation_id="pending-image")
        with self.assertRaisesRegex(RuntimeError, "前置的生图任务未成功"):
            page2._prepare_operation(task)
        self.session.page2_operation_outputs["pending-image"] = self.image.id
        page2._prepare_operation(task)
        self.image.source_turn_id = ""
        with self.assertRaises(MissingVideoStorySource):
            page2._prepare_operation(task)
        self.assertEqual(self.session.page2_video_story_toast, self.image.id)

    def test_persist_and_reopen_keep_source_manual_story_and_per_image_drafts(self):
        from app import storage
        from app.config import SettingsStore
        from app.services import chat_records
        self.image.video_prompt = "已保存视频prompt"
        self.image.video_story_text = "补录故事"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(storage, "settings", SettingsStore(Path(directory) / "settings.json")), \
                 patch.object(storage, "CHAT_RECORDS_DIR", Path(directory) / "chats"):
                record_id = page2_service.upsert_chat_record(record_id="", turns=self.turns,
                    generated_media=[self.image], system_prompt="设定", story_brain={})
                record = chat_records.load_record_by_id(record_id)
        restored = record.generated_images[0]
        self.assertEqual((restored.source_turn_id, restored.previous_source_turn_id), ("t8", "t7"))
        self.assertEqual(restored.video_prompt, self.image.video_prompt)
        self.assertEqual(restored.video_story_text, self.image.video_story_text)


if __name__ == "__main__":
    unittest.main()
