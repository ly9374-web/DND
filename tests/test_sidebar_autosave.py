import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.models import ChatRecord, SystemPromptRecord
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
            selected_tts_voice=ctx.selected_tts_voice,
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
            selected_tts_voice=ctx.selected_tts_voice,
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

    def test_new_conversation_refreshes_global_prompt_from_selected_record(self):
        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        selected = SystemPromptRecord(
            id="selected-final-setting",
            title="当前最终设定",
            prompt="Prompt 页面中的最新正文",
        )
        state = system_prompts.PromptState(
            hidden_space=True,
            records=[],
            hidden_records=[selected],
            next_index=1,
            hidden_next_index=2,
            selected_record_id=selected.id,
        )
        self.store.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, selected.id)
        self.store.set(AppStorageKeys.SYSTEM_PROMPT, "历史聊天残留的旧正文")
        session = SessionState(hidden_unlocked=True)

        with (
            patch.object(page2.st, "session_state", session),
            patch.object(system_prompts, "settings", self.store),
            patch.object(system_prompts, "load_state", return_value=state),
            patch.object(page2.url_favorites, "clear_preview_url"),
        ):
            self.assertTrue(page2.start_new_conversation(reset_settings=True))

        self.assertEqual(session.page2_final_setting_id, selected.id)
        self.assertEqual(
            self.store.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID),
            selected.id,
        )
        self.assertEqual(
            self.store.get(AppStorageKeys.SYSTEM_PROMPT),
            selected.prompt,
        )

    def test_opening_history_refreshes_its_prompt_record_instead_of_old_snapshot(self):
        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        selected = SystemPromptRecord(
            id="selected-final-setting",
            title="当前最终设定",
            prompt="Prompt 页面中的最新正文",
        )
        state = system_prompts.PromptState(
            hidden_space=True,
            records=[],
            hidden_records=[selected],
            next_index=1,
            hidden_next_index=2,
            selected_record_id=selected.id,
        )
        history = ChatRecord(
            id="history-record",
            title="历史聊天",
            turns=[],
            system_prompt="历史聊天保存的旧正文",
            final_setting_id=selected.id,
        )
        self.store.set(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID, selected.id)
        self.store.set(AppStorageKeys.SYSTEM_PROMPT, history.system_prompt)
        session = SessionState(hidden_unlocked=True)

        with (
            patch.object(page2.st, "session_state", session),
            patch.object(system_prompts, "settings", self.store),
            patch.object(system_prompts, "load_state", return_value=state),
            patch.object(page2, "get_arg", return_value=history.id),
            patch.object(page2.chat_records, "load_record_by_id", return_value=history),
            patch.object(page2.url_favorites, "clear_preview_url"),
        ):
            page2._ensure_state()
            page2._load_record_from_nav_if_needed()

        self.assertEqual(session.page2_final_setting_id, selected.id)
        self.assertEqual(
            self.store.get(AppStorageKeys.SYSTEM_PROMPT),
            selected.prompt,
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
            selected_tts_voice=ctx.selected_tts_voice,
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
            self.assertEqual(app.slider(key="page2_sidebar_image_poll_attempts").value, 3)
            app.slider(key="page2_sidebar_image_poll_attempts").set_value(30).run()
            self.assertEqual(
                self.store.int(AppStorageKeys.PAGE2_IMAGE_POLL_ATTEMPTS), 30,
            )
            self.assertEqual(page2_service.load_context_from_settings().image_poll_attempts, 30)
            app.slider(key="page2_sidebar_image_poll_attempts").set_value(3).run()
            self.assertEqual(self.store.int(AppStorageKeys.PAGE2_IMAGE_POLL_ATTEMPTS), 3)
            set_many.reset_mock()
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

            self.assertEqual(
                app.toggle(key="page2_sidebar_auto_image_enabled").label,
                "自动生图",
            )
            self.assertFalse(
                any(slider.key == "page2_sidebar_auto_image_interval" for slider in app.slider)
            )

            app.toggle(key="page2_sidebar_auto_image_enabled").set_value(True).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                app.slider(key="page2_sidebar_auto_image_interval").value,
                5,
            )
            self.assertTrue(self.store.bool(AppStorageKeys.PAGE2_AUTO_IMAGE_ENABLED))
            self.assertEqual(app.session_state["page2_auto_image_next_turn"], 1)
            self.assertTrue(app.session_state["page2_auto_image_first_turn_pending"])

            app.slider(key="page2_sidebar_auto_image_interval").set_value(12).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["page2_auto_image_next_turn"], 1)
            app.run()
            self.assertEqual(app.session_state["page2_auto_image_next_turn"], 1)

            app.toggle(key="page2_sidebar_auto_image_enabled").set_value(False).run()
            self.assertEqual(len(app.exception), 0)
            self.assertIsNone(app.session_state["page2_auto_image_next_turn"])
            self.assertFalse(app.session_state["page2_auto_image_first_turn_pending"])
            self.assertFalse(
                any(slider.key == "page2_sidebar_auto_image_interval" for slider in app.slider)
            )


if __name__ == "__main__":
    unittest.main()
