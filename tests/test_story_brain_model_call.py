import unittest
from unittest.mock import patch

from app.models import CHAT_MODEL_LABELS, CHAT_MODEL_OPTIONS, Page2ConversationTurn
from app.services import page2_service


class StoryBrainModelCallTests(unittest.TestCase):
    def test_story_brain_model_choices_match_chat_model_choices(self):
        self.assertEqual(page2_service.STORY_BRAIN_UPDATE_MODELS, CHAT_MODEL_OPTIONS)
        self.assertEqual(page2_service.STORY_BRAIN_UPDATE_MODEL_LABELS, CHAT_MODEL_LABELS)

    def test_legacy_grok_choice_migrates_to_grok_43(self):
        self.assertEqual(page2_service.normalize_story_brain_update_model("grok"), "grok1")

    def test_story_brain_reuses_chat_model_dispatch(self):
        ctx = page2_service.default_context()
        ctx.story_brain_update_model = "grok2"
        ctx.story_brain_reasoning_enabled = False
        turns = [
            Page2ConversationTurn(user_message="查看门", assistant_message="门上有血迹")
        ]

        with patch.object(
            page2_service,
            "_send_with_current_chat_model",
            return_value="更新后的 Story Brain",
        ) as send:
            result = page2_service.generate_short_story_brain(
                ctx=ctx,
                turns=turns,
                story_brain_short="旧记忆",
                long_term_memory_prompt="自定义长期记忆规则",
            )

        self.assertEqual(result, "更新后的 Story Brain")
        call = send.call_args.kwargs
        self.assertEqual(call["ctx"].selected_chat_model, "grok2")
        self.assertFalse(call["ctx"].chat_reasoning_enabled)
        self.assertEqual(call["system_prompt"], "自定义长期记忆规则")
        self.assertEqual(call["context_messages"], [])
        self.assertIn("门上有血迹", call["user_message"])

    def test_grok_46_story_brain_gets_same_forced_reasoning_as_chat(self):
        ctx = page2_service.default_context()
        ctx.story_brain_update_model = "grok2"
        ctx.story_brain_reasoning_enabled = False

        with patch.object(
            page2_service.GrokAPIClient,
            "send_message",
            return_value="更新后的 Story Brain",
        ) as send:
            page2_service.generate_short_story_brain(
                ctx=ctx,
                turns=[],
                story_brain_short="旧记忆",
            )

        self.assertEqual(send.call_args.kwargs["model"], "grok-4.6")
        self.assertTrue(send.call_args.kwargs["thinking_enabled"])
        self.assertEqual(send.call_args.kwargs["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()
