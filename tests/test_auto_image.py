"""Automatic image scheduling follows user turns and retries after failures."""

import unittest
from unittest.mock import patch

from app.models import ChatRecord, GeneratedImageRecord, Page2ConversationTurn
from app.services import page2_service
from web.pages import page2


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def run_immediately(task, fn):
    task._fn = fn
    task._started = True
    task._run()


class AutoImageTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState()
        self.ctx = page2_service.default_context()
        self.ctx.auto_image_enabled = True
        self.ctx.auto_image_interval = 5
        self.prompt_calls = []
        self.image_calls = []
        self.fail_prompt = False
        self.fail_image = False

        def prompt(**kwargs):
            self.prompt_calls.append(kwargs)
            if self.fail_prompt:
                raise RuntimeError("prompt failed")
            return "generated prompt"

        def image(**kwargs):
            self.image_calls.append(kwargs)
            if self.fail_image:
                raise RuntimeError("image failed")
            return GeneratedImageRecord(provider=kwargs["provider"], prompt=kwargs["prompt"])

        for patcher in (
            patch.object(page2.st, "session_state", self.state),
            patch.object(page2, "_upsert_record"),
            patch.object(page2.settings, "get", return_value={}),
            patch.object(page2.media_store, "cache_generated", side_effect=lambda item: item),
            patch.object(page2.media_store, "remember_image"),
            patch.object(page2._Page2OperationTask, "start", run_immediately),
            patch.object(page2_service, "load_context_from_settings", return_value=self.ctx),
            patch.object(page2_service, "generate_image_prompt", side_effect=prompt),
            patch.object(page2_service, "generate_image", side_effect=image),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        page2._ensure_state()
        self.assertEqual(self.state.page2_image_prompt_mode, "first_person")
        self.state["_page2_image_provider"] = "grok"
        self.state["page2_image_aspect_ratio_grok"] = "3:4"

    def drain(self):
        for _ in range(10):
            if not page2.page2_operation_queue_busy():
                return
            page2._advance_operation_queues()
        self.fail("Automatic image workflow did not finish")

    def append_turn(self, number, *, auto_dice=False):
        turn = Page2ConversationTurn(
            id=f"turn-{number}", user_message=f"action {number}",
            assistant_message=f"story {number}", is_auto_dice_followup=auto_dice,
        )
        self.state.page2_turns.append(turn)
        return turn

    def followup(self, turn):
        task = page2._Page2OperationTask(
            kind="chat", label="chat", scope_id=page2._operation_scope_id(),
        )
        task.meta = {
            "ctx": self.ctx, "turn_id": turn.id,
            "story_brain_enabled": False,
            "is_auto_dice_followup": turn.is_auto_dice_followup,
        }
        page2._schedule_chat_followups(task, turn.assistant_message)

    def load_record(self, record):
        with (
            patch.object(page2, "get_arg", return_value=record.id),
            patch.object(page2.chat_records, "load_record_by_id", return_value=record),
            patch.object(page2.system_prompts, "refresh_selected_prompt", return_value=None),
            patch.object(page2.url_favorites, "clear_preview_url"),
        ):
            page2._load_record_from_nav_if_needed()

    def test_first_reply_generates_for_every_interval_then_waits_full_interval(self):
        for interval in range(1, 13):
            with self.subTest(interval=interval):
                self.ctx.auto_image_interval = interval
                self.state.page2_turns = []
                self.state.page2_generated_media = []
                self.state.page2_auto_image_next_turn = None
                self.image_calls.clear()
                for number in range(1, interval + 2):
                    self.followup(self.append_turn(number))
                    self.drain()
                    self.assertEqual(len(self.image_calls), 1 if number <= interval else 2)
                self.assertFalse(self.state.page2_auto_image_first_turn_pending)
                self.assertEqual(self.state.page2_auto_image_next_turn, 1 + 2 * interval)

    def test_enabling_in_existing_chat_and_reenabling_use_next_reply(self):
        for number in range(1, 5):
            self.append_turn(number)
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        self.assertEqual(self.state.page2_auto_image_next_turn, 5)
        self.assertEqual(self.image_calls, [])
        self.followup(self.append_turn(5))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 10)

        self.ctx.auto_image_enabled = False
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(True, 5))
        self.followup(self.append_turn(6))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)
        self.ctx.auto_image_enabled = True
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        self.assertEqual(self.state.page2_auto_image_next_turn, 7)
        self.followup(self.append_turn(7))
        self.drain()
        self.assertEqual(len(self.image_calls), 2)
        self.assertEqual(self.state.page2_auto_image_next_turn, 12)

    def test_changing_interval_preserves_first_reply_but_not_after_success(self):
        self.append_turn(1)
        self.ctx.auto_image_interval = 1
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        self.ctx.auto_image_interval = 12
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(True, 1))
        self.assertEqual(self.state.page2_auto_image_next_turn, 2)
        self.followup(self.append_turn(2))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)
        self.ctx.auto_image_interval = 1
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(True, 12))
        self.ctx.auto_image_interval = 5
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(True, 1))
        self.assertEqual(self.state.page2_auto_image_next_turn, 7)

    def test_first_reply_failure_retries_without_waiting_interval(self):
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        self.fail_image = True
        self.followup(self.append_turn(1))
        self.drain()
        self.assertTrue(self.state.page2_auto_image_first_turn_pending)
        self.assertEqual(self.state.page2_auto_image_next_turn, 2)
        self.fail_image = False
        self.followup(self.append_turn(2))
        self.drain()
        self.assertFalse(self.state.page2_auto_image_first_turn_pending)
        self.assertEqual(self.state.page2_auto_image_next_turn, 7)

    def test_incomplete_failed_excluded_and_dice_turns_do_not_consume_first_reply(self):
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        turn = self.append_turn("loading")
        turn.is_loading = True
        turn = self.append_turn("failed")
        turn.assistant_message = "请求失败，请稍后重试。"
        turn = self.append_turn("excluded")
        turn.is_context_excluded = True
        turn = self.append_turn("empty")
        turn.assistant_message = ""
        self.followup(self.append_turn("dice", auto_dice=True))
        self.drain()
        self.assertEqual(self.image_calls, [])
        self.followup(self.append_turn(1))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 6)

    def test_reload_keeps_pending_first_reply_and_completed_schedule(self):
        self.append_turn(1)
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        record = ChatRecord(
            id="pending-first", title="story", turns=self.state.page2_turns,
            system_prompt="", auto_image_next_turn=2, auto_image_anchor_turn_count=1,
            auto_image_first_turn_pending=True,
        )
        self.load_record(ChatRecord.from_dict(record.to_dict()))
        self.assertTrue(self.state.page2_auto_image_first_turn_pending)
        self.assertEqual(self.state.page2_auto_image_next_turn, 2)
        self.followup(self.append_turn(2))
        self.drain()
        record.id = "completed-first"
        record.auto_image_next_turn = self.state.page2_auto_image_next_turn
        record.auto_image_anchor_turn_count = self.state.page2_auto_image_anchor_turn_count
        record.auto_image_first_turn_pending = self.state.page2_auto_image_first_turn_pending
        record.turns = self.state.page2_turns
        self.load_record(ChatRecord.from_dict(record.to_dict()))
        self.assertFalse(self.state.page2_auto_image_first_turn_pending)
        self.assertEqual(self.state.page2_auto_image_next_turn, 7)
        self.followup(self.append_turn(3))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)

    def test_record_without_schedule_generates_on_next_reply(self):
        record = ChatRecord(
            id="no-schedule", title="story", turns=[self.append_turn(1)], system_prompt="",
        )
        self.load_record(record)
        self.assertTrue(self.state.page2_auto_image_first_turn_pending)
        self.assertEqual(self.state.page2_auto_image_anchor_turn_count, 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 2)
        self.followup(self.append_turn(2))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)

    def test_save_service_keeps_first_reply_state(self):
        with (
            patch.object(page2_service.ChatRecordStore, "load_index", return_value=[]),
            patch.object(page2_service.ChatRecordStore, "save_or_update_record") as save,
        ):
            page2_service.upsert_chat_record(
                record_id="saved-first", turns=[self.append_turn(1)],
                generated_media=[], system_prompt="", story_brain={},
                auto_image_next_turn=2, auto_image_anchor_turn_count=1,
                auto_image_first_turn_pending=True,
            )
        loaded = ChatRecord.from_dict(save.call_args.args[0].to_dict())
        self.assertTrue(loaded.auto_image_first_turn_pending)
        self.assertEqual(loaded.auto_image_next_turn, 2)

    def test_manual_image_before_first_reply_starts_regular_interval(self):
        page2._update_auto_image_schedule(ctx=self.ctx, previous=(False, 5))
        page2._queue_image_generation(
            provider="grok", prompt="manual", image_urls=[],
            aspect_ratio="3:4", mode="first_person", subject="",
        )
        self.drain()
        self.assertFalse(self.state.page2_auto_image_first_turn_pending)
        self.assertEqual(self.state.page2_auto_image_next_turn, 5)

    def test_fifth_user_turn_uses_current_media_settings_and_first_person_fallback(self):
        self.ctx.image_poll_attempts = 30
        self.state["page2_auto_image_next_turn"] = 5
        self.state.page2_image_prompt_mode = "closeup"
        self.state.page2_image_prompt_subject = ""
        for number in range(1, 5):
            self.append_turn(number)
        self.append_turn("dice", auto_dice=True)
        self.assertEqual(page2._completed_auto_image_turn_count(self.state.page2_turns), 4)
        turn = self.append_turn(5)
        self.followup(turn)
        self.drain()

        self.assertEqual(len(self.prompt_calls), 1)
        self.assertEqual(self.prompt_calls[0]["mode"], "first_person")
        self.assertEqual(self.prompt_calls[0]["turns"][-1].id, turn.id)
        self.assertEqual(self.image_calls[0]["provider"], "grok")
        self.assertEqual(self.image_calls[0]["aspect_ratio"], "3:4")
        self.assertEqual(self.image_calls[0]["max_attempts"], 30)
        self.assertTrue(callable(self.image_calls[0]["progress_callback"]))
        self.assertEqual(self.state.page2_generated_media[0].source_turn_id, turn.id)
        self.assertEqual(self.state.page2_auto_image_next_turn, 10)
        self.assertEqual(self.state.page2_auto_image_anchor_turn_count, 5)

    def test_undo_generated_turn_keeps_exact_interval_after_replacement_turns(self):
        self.ctx.auto_image_interval = 3
        self.state["page2_auto_image_next_turn"] = 3
        for number in range(1, 4):
            self.followup(self.append_turn(number))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 6)

        self.assertTrue(page2._undo_last_turn())
        self.assertEqual(self.state.page2_auto_image_anchor_turn_count, 2)
        self.assertEqual(self.state.page2_auto_image_next_turn, 5)
        for number in ("3-replaced", "4-replaced"):
            self.followup(self.append_turn(number))
            self.drain()
            self.assertEqual(len(self.image_calls), 1)
        self.followup(self.append_turn("5-replaced"))
        self.drain()
        self.assertEqual(len(self.image_calls), 2)
        self.assertEqual(self.state.page2_auto_image_next_turn, 8)

    def test_undo_after_image_preserves_retained_progress_and_repeated_undo(self):
        self.ctx.auto_image_interval = 3
        self.state["page2_auto_image_next_turn"] = 3
        for number in range(1, 4):
            self.followup(self.append_turn(number))
        self.drain()
        self.followup(self.append_turn(4))
        self.followup(self.append_turn(5))
        self.assertTrue(page2._undo_last_turn())
        self.assertEqual(self.state.page2_auto_image_next_turn, 6)
        self.assertTrue(page2._undo_last_turn())
        self.assertEqual(self.state.page2_auto_image_next_turn, 6)
        self.assertTrue(page2._undo_last_turn())
        self.assertEqual(self.state.page2_auto_image_next_turn, 5)
        self.assertTrue(page2._undo_last_turn())
        self.assertEqual(self.state.page2_auto_image_next_turn, 4)
        for number in ("2-new", "3-new"):
            self.followup(self.append_turn(number))
            self.drain()
            self.assertEqual(len(self.image_calls), 1)
        self.followup(self.append_turn("4-new"))
        self.drain()
        self.assertEqual(len(self.image_calls), 2)

    def test_undo_failed_image_turn_retries_at_replacement_turn(self):
        self.ctx.auto_image_interval = 3
        self.state["page2_auto_image_next_turn"] = 3
        for number in range(1, 3):
            self.followup(self.append_turn(number))
        self.fail_image = True
        self.followup(self.append_turn(3))
        self.drain()
        self.assertEqual(self.state.page2_auto_image_next_turn, 4)
        self.assertEqual(self.state.page2_auto_image_anchor_turn_count, 0)

        self.assertTrue(page2._undo_last_turn())
        self.assertEqual(self.state.page2_auto_image_next_turn, 3)
        self.fail_image = False
        self.followup(self.append_turn("3-replaced"))
        self.drain()
        self.assertEqual(len(self.state.page2_generated_media), 1)

    def test_loading_legacy_record_repairs_stale_target_after_undo(self):
        self.ctx.auto_image_interval = 3
        legacy = ChatRecord(
            id="legacy-auto-image", title="old chat", system_prompt="",
            final_setting_id="saved-prompt",
            turns=[
                Page2ConversationTurn(id=f"old-{number}", user_message=f"action {number}",
                                      assistant_message=f"story {number}")
                for number in range(1, 3)
            ],
            auto_image_next_turn=6,
        )
        with (
            patch.object(page2, "get_arg", return_value=legacy.id),
            patch.object(page2.chat_records, "load_record_by_id", return_value=legacy),
            patch.object(page2.system_prompts, "refresh_selected_prompt", return_value=None),
            patch.object(page2.url_favorites, "clear_preview_url"),
        ):
            page2._load_record_from_nav_if_needed()

        self.assertEqual(self.state.page2_auto_image_anchor_turn_count, 2)
        self.assertEqual(self.state.page2_auto_image_next_turn, 5)

    def test_prompt_failure_retries_on_next_user_turn(self):
        self.state["page2_auto_image_next_turn"] = 5
        for number in range(1, 5):
            self.append_turn(number)
        self.fail_prompt = True
        self.followup(self.append_turn(5))
        self.drain()
        self.assertEqual(self.state.page2_auto_image_next_turn, 6)
        self.assertEqual(self.image_calls, [])

        self.fail_prompt = False
        self.followup(self.append_turn(6))
        self.drain()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 11)

    def test_image_failure_retries_and_record_keeps_schedule_and_dice_flag(self):
        self.state["page2_auto_image_next_turn"] = 5
        for number in range(1, 5):
            self.append_turn(number)
        self.fail_image = True
        self.followup(self.append_turn(5))
        self.drain()
        self.assertEqual(self.state.page2_auto_image_next_turn, 6)
        self.assertEqual(self.state.page2_generated_media, [])

        self.fail_image = False
        self.followup(self.append_turn(6))
        self.drain()
        self.assertEqual(len(self.state.page2_generated_media), 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 11)

        saved = ChatRecord(
            title="story", turns=self.state.page2_turns,
            system_prompt="", auto_image_next_turn=self.state.page2_auto_image_next_turn,
            auto_image_anchor_turn_count=self.state.page2_auto_image_anchor_turn_count,
        )
        saved.turns[-1].is_auto_dice_followup = True
        loaded = ChatRecord.from_dict(saved.to_dict())
        self.assertEqual(loaded.auto_image_next_turn, 11)
        self.assertEqual(loaded.auto_image_anchor_turn_count, 6)
        self.assertTrue(loaded.turns[-1].is_auto_dice_followup)

    def test_manual_image_generation_restarts_auto_image_countdown(self):
        self.state["page2_auto_image_next_turn"] = 5
        for number in range(1, 4):
            self.append_turn(number)
        self.assertEqual(page2._completed_auto_image_turn_count(self.state.page2_turns), 3)

        page2._queue_image_generation(
            provider="grok",
            prompt="manual prompt",
            image_urls=[],
            aspect_ratio="3:4",
            mode="first_person",
            subject="",
        )
        self.drain()

        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(self.state.page2_auto_image_next_turn, 8)

    def test_manual_image_generation_never_moves_auto_image_earlier(self):
        self.state["page2_auto_image_next_turn"] = 10
        self.append_turn(1)

        page2._queue_image_generation(
            provider="grok",
            prompt="manual prompt",
            image_urls=[],
            aspect_ratio="3:4",
            mode="first_person",
            subject="",
        )
        self.drain()

        self.assertEqual(self.state.page2_auto_image_next_turn, 10)

    def test_current_reference_urls_and_refiner_follow_right_panel(self):
        reference = GeneratedImageRecord(
            id="reference", provider="local", prompt="old image",
            image_url_string="https://example.com/stored.png",
        )
        self.state[page2._reference_key() + "_urls"] = "https://example.com/new.png"
        with (
            patch.object(page2, "_reference_images", return_value={reference.id: reference}),
            patch.object(page2, "_manual_reference_ids", return_value=[reference.id]),
            patch.object(page2.image_references, "media_reference", return_value="stored-reference"),
        ):
            options = page2._current_auto_image_options()
        self.assertEqual(
            options["image_urls"], ["stored-reference", "https://example.com/new.png"],
        )

        self.state["_page2_image_provider"] = page2.page2_service.ANILLUSTRIOUS_PROVIDER
        self.state["page2_refiner_prompt"] = "fine detail"
        with patch.object(page2, "_manual_reference_ids", return_value=[reference.id]):
            options = page2._current_auto_image_options()
        self.assertEqual(options["image_urls"], [])
        self.assertEqual(options["refiner_prompt"], "fine detail")


if __name__ == "__main__":
    unittest.main()
