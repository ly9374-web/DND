"""Queue concurrency and result ownership, without model requests or user data writes."""
import threading
import unittest
from copy import deepcopy
from textwrap import indent
from unittest.mock import patch

from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn
from app.services import page2_service
from web.pages import page2


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def hold_start(task, fn):
    task._fn = fn
    task._started = True


def finish(task, result=None, error=None):
    task.result = result
    task.error = error
    task._started = True
    task._done.set()


class OperationConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState()
        for patcher in (
            patch.object(page2.st, "session_state", self.state),
            patch.object(page2, "_upsert_record"),
            patch.object(page2_service, "load_context_from_settings", side_effect=page2_service.default_context),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        page2._ensure_state()
        self.source = GeneratedImageRecord(id="source", provider="grok", prompt="old image", source_turn_id="t1")
        self.state.page2_generated_media = [self.source]
        self.state.page2_turns = [Page2ConversationTurn(id="t1", user_message="old", assistant_message="old reply")]
        self.state.page2_selected_media_id = "source"
        self.state.page2_video_source = "source"

    def enqueue(self, kind, **payload):
        return page2._enqueue_operation(kind, kind, payload, allow_same_kind=True)

    def test_actual_workers_start_three_lanes_once_and_keep_extra_tasks_queued(self):
        entered = {kind: threading.Event() for kind in ("chat", "image", "video")}
        release = threading.Event()
        self.addCleanup(release.set)
        tasks = [self.enqueue(kind) for kind in ("video", "image", "chat")]
        extras = [self.enqueue(kind) for kind in ("video", "image", "chat")]

        def prepare(task):
            def run():
                entered[task.kind].set()
                release.wait(5)
            return run

        with patch.object(page2, "_prepare_operation", side_effect=prepare) as preparation:
            page2._advance_operation_queues()
            for event in entered.values():
                self.assertTrue(event.wait(1), "All three workers must start before any is released")
            self.assertEqual({task.id for task in page2._active_operations()}, {task.id for task in tasks})
            self.assertEqual(self.state.page2_operation_queue, extras)
            self.assertFalse(page2._advance_operation_queues())
            self.assertEqual(preparation.call_count, 3)
        release.set()
        for task in tasks:
            self.assertTrue(task._done.wait(1))

    def test_chat_finishes_and_next_chat_sees_reply_while_both_media_tasks_run(self):
        video = self.enqueue("video", source_id="source", prompt="move", seconds=5)
        image = self.enqueue("image", provider="grok", prompt="image")
        first = self.enqueue("chat", user_text="first", story_brain_enabled=False)
        second = self.enqueue("chat", user_text="second", story_brain_enabled=False)
        with patch.object(page2._Page2OperationTask, "start", hold_start), \
             patch.object(page2_service, "send_message", return_value="second reply") as send:
            page2._advance_operation_queues()
            finish(first, "first reply")
            page2._advance_operation_queues()
            self.assertIs(self.state.page2_active_operation, second)
            self.assertFalse(video.is_done())
            self.assertFalse(image.is_done())
            second._fn()
        context = send.call_args.kwargs["turns"]
        self.assertEqual(context[-1].assistant_message, "first reply")
        self.assertEqual(send.call_args.kwargs["user_message"], "second")

    def test_memory_followup_runs_before_next_chat_without_waiting_for_video(self):
        video = self.enqueue("video")
        first = self.enqueue("chat")
        second = self.enqueue("chat")

        def apply(task):
            if task is first:
                page2._enqueue_operation("story_brain", "memory", first=True)

        with patch.object(page2._Page2OperationTask, "start", hold_start), \
             patch.object(page2, "_prepare_operation", return_value=lambda: None), \
             patch.object(page2, "_apply_operation", side_effect=apply):
            page2._advance_operation_queues()
            finish(first)
            page2._advance_operation_queues()
            memory = self.state.page2_active_operation
            self.assertEqual(memory.kind, "story_brain")
            self.assertFalse(second.is_started())
            finish(memory)
            page2._advance_operation_queues()
            self.assertIs(self.state.page2_active_operation, second)
            self.assertFalse(video.is_done())

    def test_pending_video_prompt_does_not_block_chat_or_its_image_dependency(self):
        image = self.enqueue("image")
        prompt = self.enqueue("video_prompt", image_operation_id=image.id)
        chat = self.enqueue("chat")
        with patch.object(page2._Page2OperationTask, "start", hold_start), \
             patch.object(page2, "_prepare_operation", return_value=lambda: None):
            page2._advance_operation_queues()
        self.assertTrue(image.is_started())
        self.assertTrue(chat.is_started())
        self.assertFalse(prompt.is_started())
        self.assertTrue(page2._operation_waiting_for_dependency(prompt))

    def test_pending_media_does_not_deadlock_a_later_required_prompt(self):
        prompt = self.enqueue("image_prompt")
        image = self.enqueue("image", prompt_operation_id=prompt.id)
        video_prompt = self.enqueue("video_prompt", image_operation_id=image.id)
        self.state.page2_operation_queue = [video_prompt, image, prompt]
        with patch.object(page2._Page2OperationTask, "start", hold_start), \
             patch.object(page2, "_prepare_operation", return_value=lambda: None):
            page2._advance_operation_queues()
        self.assertTrue(prompt.is_started())
        self.assertFalse(video_prompt.is_started())
        self.assertFalse(image.is_started())

    def test_failed_image_prevents_dependent_video_request_but_other_tasks_continue(self):
        image = self.enqueue("image", provider="grok", prompt="image")
        video = self.enqueue("video", image_operation_id=image.id, prompt="move")
        chat = self.enqueue("chat", user_text="hello", story_brain_enabled=False)
        other = self.enqueue("image", provider="grok", prompt="next")
        with patch.object(page2._Page2OperationTask, "start", hold_start), \
             patch.object(page2_service, "generate_video_from_image") as generate:
            page2._advance_operation_queues()
            finish(image, error=RuntimeError("failed image"))
            page2._advance_operation_queues()
            self.assertTrue(video.is_done())
            self.assertIn("前置的生图任务未成功", str(video.error))
            page2._advance_operation_queues()
            generate.assert_not_called()
        self.assertTrue(chat.is_started())
        self.assertTrue(other.is_started())
        self.assertIn("failed image", self.state.page2_operation_error)

    def test_media_completion_shows_image_and_preserves_chat_video_input_and_drafts(self):
        image = self.enqueue("image", pending_video_prompt="new image draft")
        video = self.enqueue("video")
        self.state.page2_operation_queue = []
        self.state.page2_active_media_operations = {"image": image, "video": video}
        self.state.page2_image_prompt = "user draft"
        self.state.page2_turns.append(Page2ConversationTurn(user_message="new", assistant_message="new reply"))
        self.state.page2_story_brain_short = "latest memory"
        expected_turns = deepcopy(self.state.page2_turns)
        finish(image, GeneratedImageRecord(id="new-image", provider="grok", prompt="image"))
        finish(video, GeneratedImageRecord(id="new-video", provider="grok", prompt="video", media_kind=GeneratedMediaKind.VIDEO))
        with patch.object(page2.url_favorites, "clear_preview_url") as clear, \
             patch.object(page2, "_upsert_record") as save:
            page2._advance_operation_queues()
            page2._advance_operation_queues()
            page2._apply_operation(image)
            clear.assert_called_once_with("page2")
            self.assertEqual(save.call_count, 2)
        self.assertEqual(self.state.page2_turns, expected_turns)
        self.assertEqual(self.state.page2_story_brain_short, "latest memory")
        self.assertEqual(self.state.page2_image_prompt, "user draft")
        self.assertEqual(self.state.page2_selected_media_id, "new-image")
        self.assertEqual(self.state.page2_video_source, "source")
        self.assertEqual(self.state.page2_pending_media_record_select, "new-image")
        self.assertNotIn("page2_pending_video_source", self.state)
        self.assertEqual([item.id for item in self.state.page2_generated_media], ["source", "new-image", "new-video"])
        self.assertEqual([item["media_id"] for item in self.state.page2_media_completions], ["new-video"])

    def test_save_failure_rolls_back_only_media_and_does_not_publish_success(self):
        task = self.enqueue("image")
        task.result = GeneratedImageRecord(provider="grok", prompt="image")
        with patch.object(page2, "_upsert_record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                page2._apply_operation(task)
        self.assertEqual(self.state.page2_generated_media, [self.source])
        self.assertNotIn(task.id, self.state.page2_operation_outputs)
        self.assertFalse(self.state.page2_media_completions)

    def test_stale_completion_is_discarded_before_any_write(self):
        task = self.enqueue("video")
        self.state.page2_operation_queue = []
        self.state.page2_active_media_operations = {"video": task}
        finish(task, GeneratedImageRecord(provider="grok", prompt="old video"))
        self.state.page2_operation_scope_id = "new-scope"
        with patch.object(page2, "_apply_operation") as apply:
            page2._advance_operation_queues()
            apply.assert_not_called()
        self.assertFalse(page2.page2_operation_queue_busy())
        self.assertIn("未写入旧任务结果", self.state.page2_operation_error)

    def test_video_and_request_settings_are_snapshots(self):
        ctx = page2_service.default_context()
        task = self.enqueue("video", ctx=ctx, source_id="source", prompt="move", seconds=5)
        ctx.selected_video_generation_provider = "changed"
        fn = page2._prepare_video_operation(task)
        self.source.image_url_string = "https://example.com/changed.png"
        with patch.object(page2_service, "generate_video_from_image") as generate:
            fn()
        kwargs = generate.call_args.kwargs
        self.assertIsNot(kwargs["source_record"], self.source)
        self.assertIsNone(kwargs["source_record"].image_url_string)
        self.assertNotEqual(kwargs["ctx"].selected_video_generation_provider, "changed")

    def test_edited_image_prompt_is_preserved_but_dependency_uses_generated_result(self):
        task = self.enqueue("image_prompt")
        self.state.page2_image_prompt = "original"
        page2._prepare_image_prompt_operation(task)
        self.state.page2_image_prompt = "user changed this"
        task.result = "model prompt"
        page2._apply_operation(task)
        self.assertEqual(self.state.page2_image_prompt, "user changed this")
        image = self.enqueue("image", prompt_operation_id=task.id, provider="grok")
        with patch.object(page2_service, "generate_image") as generate:
            page2._prepare_image_operation(image)()
        self.assertEqual(generate.call_args.kwargs["prompt"], "model prompt")

    def test_view_opens_exact_completion_and_preserves_other_notifications(self):
        tasks = [self.enqueue(kind) for kind in ("image", "video")]
        for task in tasks:
            task.result = GeneratedImageRecord(id=task.kind, provider="grok", prompt="result",
                media_kind=GeneratedMediaKind.IMAGE if task.kind == "image" else GeneratedMediaKind.VIDEO)
            page2._apply_operation(task)
        with patch.object(page2.url_favorites, "clear_preview_url") as clear:
            page2._view_completed_media(tasks[0].id, "image")
            clear.assert_called_once_with("page2")
        self.assertEqual(self.state.page2_selected_media_id, "image")
        self.assertEqual(self.state.page2_pending_media_record_select, "image")
        self.assertEqual(self.state.page2_pending_video_source, "image")
        self.assertEqual([item["media_id"] for item in self.state.page2_media_completions], ["video"])

    def test_old_selector_event_cannot_undo_view_but_new_selector_still_works(self):
        image = GeneratedImageRecord(id="new-image", provider="grok", prompt="new")
        self.state.page2_generated_media.append(image)
        old_key = page2._media_selectbox_key()
        self.state[old_key] = "source"
        with patch.object(page2.url_favorites, "clear_preview_url"):
            page2._view_completed_media("finished-task", image.id)
            page2._activate_media_selection(old_key)
            self.assertEqual(self.state.page2_selected_media_id, image.id)
            new_key = page2._media_selectbox_key()
            self.assertNotEqual(new_key, old_key)
            self.state[new_key] = "source"
            page2._activate_media_selection(new_key)
            self.assertEqual(self.state.page2_selected_media_id, "source")

    def test_switch_and_new_conversation_remain_blocked_when_only_media_is_running(self):
        task = self.enqueue("image")
        self.state.page2_operation_queue = []
        self.state.page2_active_media_operations = {"image": task}
        self.assertTrue(page2.page2_operation_queue_busy())
        self.assertTrue(page2._operation_kind_busy("image"))
        self.assertIs(page2._latest_earlier_operation("image"), task)
        self.assertFalse(page2.start_new_conversation())
        self.assertFalse(page2.branch_or_start_new_conversation())


class MediaSelectionTests(unittest.TestCase):
    def app(self, *, with_existing=False):
        from streamlit.testing.v1 import AppTest
        script = '''
import streamlit as st
from unittest.mock import patch
from app.models import GeneratedImageRecord
from app.video_models import WAN_MODEL
from web.pages import page2

page2._ensure_state()
if "fixture_seeded" not in st.session_state:
    st.session_state.fixture_seeded = True
    first = page2._enqueue_operation("image", "first", allow_same_kind=True)
    second = page2._enqueue_operation("image", "second", allow_same_kind=True)
    st.session_state.fixture_first = first
    st.session_state.fixture_second = second
    st.session_state.page2_video_source = "pending:" + first.id
    if WITH_EXISTING:
        st.session_state.page2_generated_media = [GeneratedImageRecord(id="existing", provider="grok", prompt="old")]
        st.session_state.page2_video_source = "existing"
if st.button("完成第一张"):
    first = st.session_state.fixture_first
    first.result = GeneratedImageRecord(id="first-result", provider="grok", prompt="first")
    with patch.object(page2, "_upsert_record"):
        page2._apply_operation(first)
    st.session_state.page2_operation_queue.remove(first)
page2._render_video_prompt_controls(st.session_state.page2_generated_media, WAN_MODEL)
'''.replace("WITH_EXISTING", repr(with_existing))
        return AppTest.from_string(
            "from unittest.mock import patch\nwith patch('web.pages.page2._upsert_record'):\n" + indent(script, "    ")
        ).run()

    def test_multiple_pending_images_keep_selection_and_save_draft_to_exact_task(self):
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        first = app.session_state.fixture_first
        second = app.session_state.fixture_second
        self.assertEqual(app.selectbox(key="page2_video_source").value, "pending:" + first.id)
        app.text_area(key="_page2_video_prompt").input("first image draft").run()
        self.assertEqual(first.payload["pending_video_prompt"], "first image draft")
        self.assertNotIn("pending_video_prompt", second.payload)
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox(key="page2_video_source").value, "first-result")
        self.assertEqual(app.text_area(key="_page2_video_prompt").value, "first image draft")

    def test_unrelated_image_completion_does_not_change_existing_video_input(self):
        app = self.app(with_existing=True)
        app.text_area(key="_page2_video_prompt").input("existing draft").run()
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox(key="page2_video_source").value, "existing")
        self.assertEqual(app.text_area(key="_page2_video_prompt").value, "existing draft")

    def test_first_completed_image_displays_automatically_and_survives_rerun(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from app.models import GeneratedImageRecord
from web.pages import page2

page2._ensure_state()
with patch.object(page2, "_upsert_record"), patch.object(page2.url_favorites, "get_preview_url", return_value=""), \\
     patch.object(page2.url_favorites, "render_url_favorites"):
    if "fixture_seeded" not in st.session_state:
        st.session_state.fixture_seeded = True
        task = page2._Page2OperationTask(kind="image", label="image", scope_id=page2._operation_scope_id())
        task.result = GeneratedImageRecord(id="new", provider="grok", prompt="new",
            image_data_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        page2._apply_operation(task)
    page2._render_media_column()
''').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox(key="page2_media_record_select_1").value, "new")
        self.assertFalse(any(button.label == "查看" for button in app.button))
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox(key="page2_media_record_select_1").value, "new")


if __name__ == "__main__":
    unittest.main()
