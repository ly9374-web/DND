import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.services import quick_inputs
from web.components.quick_input import quick_input_script


class QuickInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SettingsStore(Path(self.temp.name) / "settings.json")
        self.patcher = patch.object(quick_inputs, "settings", self.store)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_default_seed_is_public_editable_and_never_resurrected(self):
        first = quick_inputs.load_quick_inputs()[0]
        self.assertEqual(first.title, "图片")
        self.assertEqual(quick_inputs.load_quick_inputs(True)[0].id, first.id)
        quick_inputs.save_quick_input(first.id, "新名称", "自定义内容")
        self.assertEqual(quick_inputs.load_quick_inputs()[0].content, "自定义内容")
        quick_inputs.delete_quick_input(first.id)
        self.assertEqual(quick_inputs.load_quick_inputs(), [])
        self.assertTrue(self.store.bool(AppStorageKeys.QUICK_INPUT_DEFAULT_SEEDED))

    def test_visibility_and_edit_ownership(self):
        public = quick_inputs.load_quick_inputs()[0]
        private = quick_inputs.save_quick_input("", "图片", "私密内容", hidden_space=True)
        self.assertEqual([r.id for r in quick_inputs.load_quick_inputs()], [public.id])
        self.assertEqual(len(quick_inputs.load_quick_inputs(True)), 2)
        self.assertEqual([r["space"] for r in quick_inputs.quick_input_options(True)], ["普通", "私密"])
        self.assertNotIn("私密内容", json.dumps(quick_inputs.quick_input_options(), ensure_ascii=False))
        quick_inputs.save_quick_input(public.id, "图片", "公开修改", hidden_space=True)
        self.assertEqual(quick_inputs.load_quick_inputs()[0].content, "公开修改")
        with self.assertRaises(ValueError):
            quick_inputs.save_quick_input(private.id, "图片", "非法修改")
        with self.assertRaises(ValueError):
            quick_inputs.delete_quick_input(private.id)
        self.assertEqual(quick_inputs.load_quick_inputs(True)[1].content, "私密内容")

    def test_duplicate_names_validated_in_original_space(self):
        public = quick_inputs.load_quick_inputs()[0]
        quick_inputs.save_quick_input("", "普通条目", "内容")
        quick_inputs.save_quick_input("", "普通条目", "私密内容", hidden_space=True)
        with self.assertRaises(ValueError):
            quick_inputs.save_quick_input(public.id, "普通条目", "内容", hidden_space=True)
        with self.assertRaises(ValueError):
            quick_inputs.save_quick_input("", " 图片 ", "内容")
        with self.assertRaises(ValueError):
            quick_inputs.save_quick_input("", "", "内容")
        with self.assertRaises(ValueError):
            quick_inputs.save_quick_input("", "名称", "   ")

    def test_persistence_and_whitespace_preservation(self):
        value = "  第一行\n\n第二行\n "
        quick_inputs.save_quick_input("", "换行", value)
        reloaded = SettingsStore(Path(self.temp.name) / "settings.json")
        with patch.object(quick_inputs, "settings", reloaded):
            self.assertEqual(quick_inputs.load_quick_inputs()[-1].content, value)

    def test_script_treats_shortcut_contents_as_data(self):
        value = '</script><script>alert("x")</script>'
        script = quick_input_script(
            [{"id": "1", "title": value, "content": value, "space": "普通"}],
            scope="ordinary", show_space=False,
        )
        self.assertEqual(script.count("</script>"), 1)
        self.assertNotIn(value, script)

    def test_chat_creation_uses_account_but_continuation_keeps_ownership(self):
        from app.models import Page2ConversationTurn
        from app.services import page2_service
        for current_private, existing_private in [(False, None), (True, None), (True, False), (True, True)]:
            with self.subTest(current=current_private, existing=existing_private):
                index = [] if existing_private is None else [SimpleNamespace(
                    id="record-test", title="已有聊天", created_at="2026-01-01", hidden=existing_private,
                )]
                with (
                    patch.object(page2_service.ChatRecordStore, "load_index", return_value=index),
                    patch.object(page2_service.ChatRecordStore, "save_or_update_record") as save,
                ):
                    page2_service.upsert_chat_record(
                        record_id="record-test", turns=[Page2ConversationTurn(user_message="继续")],
                        generated_media=[], system_prompt="", story_brain={}, hidden=current_private,
                    )
                expected = current_private if existing_private is None else existing_private
                self.assertEqual(save.call_args.args[0].hidden, expected)

    def test_management_create_save_and_duplicate_feedback(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
import streamlit as st
from web.pages.settings import _render_quick_inputs
st.session_state.hidden_unlocked = False
_render_quick_inputs(False)
''').run()
        self.assertEqual(len(app.exception), 0)
        app.button(key="quick_input_new").click().run()
        app.text_input[0].input("问候")
        app.text_area[0].input("你好\n再见")
        app.button(key="quick_input_save").click().run()
        self.assertEqual(quick_inputs.load_quick_inputs()[-1].title, "问候")
        self.assertEqual(app.text_area[0].value, "你好\n再见")
        app.button(key="quick_input_new").click().run()
        app.text_input[0].input("问候")
        app.text_area[0].input("重名")
        app.button(key="quick_input_save").click().run()
        self.assertIn("同一空间", app.error[0].value)
        self.assertEqual(len(app.exception), 0)


if __name__ == "__main__":
    unittest.main()
