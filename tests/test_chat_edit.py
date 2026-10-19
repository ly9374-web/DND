import os
import tempfile
import unittest
from unittest.mock import patch

from streamlit.proto.WidgetStates_pb2 import WidgetStates
from streamlit.testing.v1 import AppTest


EDITOR_APP = '''
import streamlit as st
from unittest.mock import patch
from app.models import Page2ConversationTurn
from web.pages import page2

st.session_state.setdefault("page2_turns", [
    Page2ConversationTurn(id="one", user_message="原始用户记录", assistant_message="原始回复记录"),
    Page2ConversationTurn(id="two", user_message="第二条用户记录", assistant_message="第二条回复记录"),
])
with patch.object(page2, "_upsert_record"):
    for turn in st.session_state.page2_turns:
        for role, text in [("user", turn.user_message), ("assistant", turn.assistant_message)]:
            page2._render_editable_chat_message(
                turn=turn, role=role, text=text, story_brain_update_busy=False,
            )
'''


class ChatEditTests(unittest.TestCase):
    def setUp(self):
        data_dir = tempfile.TemporaryDirectory(prefix="dnd-chat-edit-test-")
        self.addCleanup(data_dir.cleanup)
        env = patch.dict(os.environ, {"VS_IMAGE_NOVEL_DATA_DIR": data_dir.name})
        env.start()
        self.addCleanup(env.stop)
        self.app = AppTest.from_string(EDITOR_APP).run()
        self.assertFalse(self.app.exception)

    def open_editor(self, role="assistant", turn_id="one"):
        self.app.button(key=f"page2_chat_edit_open_{role}_{turn_id}").click().run()
        self.assertFalse(self.app.exception)
        return self.app.text_area[0]

    def test_original_is_sent_even_before_browser_acknowledges_editor(self):
        for role, expected in [("user", "原始用户记录"), ("assistant", "原始回复记录")]:
            with self.subTest(role=role):
                self.open_editor(role)
                # Another rerun arrives before the browser has registered the new editor.
                self.app._run(widget_state=WidgetStates())
                editor = self.app.text_area[0]
                proto = editor.proto
                browser_initial_text = proto.value if proto.set_value else proto.default
                self.assertEqual(browser_initial_text, expected)
                self.assertEqual(editor.value, expected)
                self.assertFalse(self.app.exception)

    def test_drafts_including_empty_text_survive_reruns(self):
        self.open_editor()
        for draft in ["修改中的草稿\n第二行", "", "继续修改"]:
            with self.subTest(draft=draft):
                self.app.text_area[0].input(draft).run()
                self.app.run()
                self.assertEqual(self.app.text_area[0].value, draft)
                self.assertEqual(self.app.session_state["page2_turns"][0].assistant_message, "原始回复记录")
                self.assertFalse(self.app.exception)

    def test_cancel_and_switch_reopen_the_corresponding_original(self):
        self.open_editor().input("未保存的草稿").run()
        self.app.button(key="page2_chat_edit_cancel_assistant_one").click().run()
        self.assertEqual(self.open_editor().value, "原始回复记录")
        self.app.text_area[0].input("另一份未保存的草稿").run()
        self.assertEqual(self.open_editor("user", "two").value, "第二条用户记录")
        self.assertEqual(self.open_editor().value, "原始回复记录")

    def test_save_then_reopen_displays_updated_record(self):
        self.open_editor().input("已修改的回复").run()
        self.app.button(key="page2_chat_edit_save_assistant_one").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["page2_turns"][0].assistant_message, "已修改的回复")
        self.assertEqual(self.open_editor().value, "已修改的回复")

    def test_past_messages_remain_editable_during_background_operations(self):
        from web.pages import page2

        for kind in ("image_prompt", "image", "video_prompt", "video", "chat", "story_brain"):
            with self.subTest(kind=kind):
                task = page2._Page2OperationTask(kind=kind, label=kind, scope_id="test")
                self.app.session_state["page2_operation_queue"] = [task]
                self.app.run()
                self.assertFalse(self.app.button(key="page2_chat_edit_open_assistant_one").disabled)
                self.open_editor().input(f"{kind} 运行时修改").run()
                self.assertFalse(self.app.text_area[0].disabled)
                self.assertFalse(self.app.button(key="page2_chat_edit_save_assistant_one").disabled)
                self.assertFalse(self.app.button(key="page2_chat_edit_delete_assistant_one").disabled)
                self.app.button(key="page2_chat_edit_save_assistant_one").click().run()
                self.assertEqual(
                    self.app.session_state["page2_turns"][0].assistant_message,
                    f"{kind} 运行时修改",
                )
                self.assertFalse(self.app.exception)
        self.assertFalse(self.app.button(key="page2_chat_edit_open_user_one").disabled)
        self.open_editor("user").input("运行中修改用户消息").run()
        self.app.button(key="page2_chat_edit_save_user_one").click().run()
        self.assertEqual(self.app.session_state["page2_turns"][0].user_message, "运行中修改用户消息")

    def test_in_flight_message_cannot_be_edited_or_removed(self):
        self.app.session_state["page2_turns"][1].is_loading = True
        self.app.run()
        self.assertTrue(self.app.button(key="page2_chat_edit_open_user_two").disabled)
        from web.pages import page2
        with patch.object(page2.st, "session_state", {
            "page2_turns": self.app.session_state["page2_turns"],
        }), \
             patch.object(page2, "_upsert_record"):
            self.assertFalse(page2._apply_chat_edit({
                "turn_id": "two", "role": "user", "text": "",
            }))
        self.assertEqual(self.app.session_state["page2_turns"][1].user_message, "第二条用户记录")


if __name__ == "__main__":
    unittest.main()
