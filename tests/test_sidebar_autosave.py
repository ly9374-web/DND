import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.services import page2_service, system_prompts
from web.pages import page2


class SidebarAutosaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SettingsStore(Path(self.temp.name) / "settings.json")

    def test_sidebar_values_update_context_without_touching_system_prompt(self):
        ctx = page2_service.default_context()
        ctx.system_prompt = "保留手动保存的 System Prompt"

        changed = page2._apply_sidebar_context_values(
            ctx,
            context_turn_count=12,
            selected_chat_model="deepseekPro",
            chat_reasoning="关思考",
            story_brain_update_model="grok",
            story_brain_reasoning="开思考",
            story_brain_turns=9,
            temperature=0.55,
            unexpected_event_enabled=True,
            unexpected_event_threshold=73,
        )

        self.assertTrue(changed)
        self.assertEqual(ctx.system_prompt, "保留手动保存的 System Prompt")
        self.assertEqual(ctx.context_turn_count, 12)
        self.assertEqual(ctx.selected_chat_model, "deepseekPro")
        self.assertFalse(ctx.chat_reasoning_enabled)
        self.assertEqual(ctx.story_brain_update_model, "grok")
        self.assertTrue(ctx.story_brain_reasoning_enabled)
        self.assertEqual(ctx.story_brain_turns, 9)
        self.assertEqual(ctx.temperature, 0.55)
        self.assertTrue(ctx.unexpected_event_enabled)
        self.assertEqual(ctx.unexpected_event_threshold, 73)

        unchanged = page2._apply_sidebar_context_values(
            ctx,
            context_turn_count=12,
            selected_chat_model="deepseekPro",
            chat_reasoning="关思考",
            story_brain_update_model="grok",
            story_brain_reasoning="开思考",
            story_brain_turns=9,
            temperature=0.55,
            unexpected_event_enabled=True,
            unexpected_event_threshold=73,
        )
        self.assertFalse(unchanged)

    def test_context_save_is_atomic_and_skips_unchanged_values(self):
        ctx = page2_service.default_context()
        with (
            patch.object(page2_service, "settings", self.store),
            patch.object(
                self.store,
                "set_many_atomic",
                wraps=self.store.set_many_atomic,
            ) as set_many,
        ):
            self.assertTrue(page2_service.save_context_to_settings(ctx))
            self.assertEqual(set_many.call_count, 1)

            self.assertFalse(page2_service.save_context_to_settings(ctx))
            self.assertEqual(set_many.call_count, 1)

            ctx.temperature = 1.25
            self.assertTrue(page2_service.save_context_to_settings(ctx))
            self.assertEqual(set_many.call_count, 2)

        self.assertEqual(
            self.store.int(AppStorageKeys.PAGE2_CONTEXT_TURN_COUNT),
            ctx.context_turn_count,
        )
        self.assertEqual(
            self.store.float(AppStorageKeys.PAGE2_TEMPERATURE),
            1.25,
        )

    def test_mistral_forces_reasoning_off_for_chat_and_story_brain(self):
        ctx = page2_service.default_context()
        page2._apply_sidebar_context_values(
            ctx,
            context_turn_count=8,
            selected_chat_model="mistralLarge2512",
            chat_reasoning="开思考",
            story_brain_update_model="mistralLarge2512",
            story_brain_reasoning="开思考",
            story_brain_turns=6,
            temperature=0.8,
            unexpected_event_enabled=False,
            unexpected_event_threshold=98,
        )
        self.assertFalse(ctx.chat_reasoning_enabled)
        self.assertFalse(ctx.story_brain_reasoning_enabled)

    def test_rendered_sidebar_has_no_confirm_and_saves_changed_widget(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(
            """
import streamlit as st
from web.pages import page2

st.session_state.auth_mode = "user"
st.session_state.hidden_unlocked = False
page2.render_sidebar_context()
"""
        )
        with (
            patch.object(page2, "settings", self.store),
            patch.object(page2_service, "settings", self.store),
            patch.object(page2, "_load_record_from_nav_if_needed"),
            patch.object(system_prompts, "load_state", return_value=object()),
            patch.object(system_prompts, "visible_records", return_value=[]),
            patch.object(
                self.store,
                "set_many_atomic",
                wraps=self.store.set_many_atomic,
            ) as set_many,
        ):
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertNotIn("确认", [button.label for button in app.button])
            self.assertEqual(set_many.call_count, 0)

            app.number_input(key="page2_sidebar_context_turn_count").set_value(12).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                self.store.int(AppStorageKeys.PAGE2_CONTEXT_TURN_COUNT),
                12,
            )
            self.assertEqual(set_many.call_count, 1)
            self.assertIn("设置已自动保存", [item.value for item in app.success])

            app.run()
            self.assertEqual(set_many.call_count, 1)

            app.selectbox(key="page2_sidebar_selected_chat_model").select(
                "mistralLarge2512"
            ).run()
            self.assertEqual(len(app.exception), 0)
            chat_reasoning = app.selectbox(key="page2_sidebar_chat_reasoning")
            self.assertEqual(chat_reasoning.value, "关思考")
            self.assertTrue(chat_reasoning.disabled)

            app.selectbox(key="page2_sidebar_story_brain_update_model").select(
                "mistralLarge2512"
            ).run()
            self.assertEqual(len(app.exception), 0)
            story_reasoning = app.selectbox(key="page2_sidebar_story_brain_reasoning")
            self.assertEqual(story_reasoning.value, "关思考")
            self.assertTrue(story_reasoning.disabled)


if __name__ == "__main__":
    unittest.main()
