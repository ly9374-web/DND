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
        self.state["page2_image2_quality"] = "low"

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

    def test_fifth_user_turn_uses_current_media_settings_and_first_person_fallback(self):
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
        self.assertEqual(self.state.page2_generated_media[0].source_turn_id, turn.id)
        self.assertEqual(self.state.page2_auto_image_next_turn, 10)

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
        )
        saved.turns[-1].is_auto_dice_followup = True
        loaded = ChatRecord.from_dict(saved.to_dict())
        self.assertEqual(loaded.auto_image_next_turn, 11)
        self.assertTrue(loaded.turns[-1].is_auto_dice_followup)

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
