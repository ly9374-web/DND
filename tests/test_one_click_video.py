"""Full workflow orchestration with deterministic requests and no user-data writes."""
import unittest
from unittest.mock import patch

from app.models import GeneratedImageRecord, GeneratedMediaKind, Page2ConversationTurn
from app.services import page2_service
from app.video_models import WAN_MODEL, WAN3_MODEL
from web.pages import page2


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def run_immediately(task, fn):
    task._fn = fn
    task._started = True
    task._run()


class OneClickVideoTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState()
        self.calls = []
        self.fail_kind = ""
        for patcher in (
            patch.object(page2.st, "session_state", self.state),
            patch.object(page2, "_upsert_record"),
            patch.object(page2.settings, "set"),
            patch.object(page2.media_store, "available", return_value=True),
            patch.object(page2.media_store, "cache_generated", side_effect=lambda record: record),
            patch.object(page2._Page2OperationTask, "start", run_immediately),
            patch.object(page2_service, "load_context_from_settings", side_effect=page2_service.default_context),
            patch.object(page2_service, "generate_image_prompt", side_effect=self.image_prompt),
            patch.object(page2_service, "generate_image", side_effect=self.image),
            patch.object(page2_service, "generate_video_prompt", side_effect=self.video_prompt),
            patch.object(page2_service, "generate_video_from_image", side_effect=self.video),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        page2._ensure_state()
        self.state.page2_turns = [Page2ConversationTurn(id="t1", assistant_message="story one")]
        self.state.page2_image_prompt_mode = "closeup"
        self.state.page2_image_prompt = "old image draft"
        self.state.page2_selected_media_id = "existing-preview"
        self.state.page2_video_source = "existing-input"

    def record_call(self, kind, kwargs):
        self.calls.append((kind, kwargs))
        if kind == self.fail_kind:
            raise RuntimeError("simulated " + kind + " failure")

    def image_prompt(self, **kwargs):
        self.record_call("image_prompt", kwargs)
        return "picture: " + kwargs["turns"][-1].assistant_message

    def image(self, **kwargs):
        self.record_call("image", kwargs)
        return GeneratedImageRecord(provider=kwargs["provider"], prompt=kwargs["prompt"])

    def video_prompt(self, **kwargs):
        self.record_call("video_prompt", kwargs)
        return "motion: " + kwargs["story_text"]

    def video(self, **kwargs):
        self.record_call("video", kwargs)
        return GeneratedImageRecord(provider="grok", prompt=kwargs["prompt"], media_kind=GeneratedMediaKind.VIDEO)

    def queue(self, **overrides):
        ctx = page2_service.default_context()
        ctx.selected_video_generation_provider = WAN_MODEL
        return page2._queue_one_click_video(**{
            "ctx": ctx, "provider": "grok", "image_urls": [], "aspect_ratio": "3:4",
            **overrides,
        })

    def drain(self):
        for _ in range(40):
            if not page2.page2_operation_queue_busy():
                return
            page2._advance_operation_queues()
        self.fail("Workflow did not finish")

    def test_full_chain_uses_new_results_and_click_time_settings(self):
        ctx = page2_service.default_context()
        ctx.selected_video_generation_provider = "bytedance/seedance-1-5-pro"
        urls = ["https://example.com/reference.png"]
        tasks = self.queue(ctx=ctx, image_urls=urls)
        original_chat_model = ctx.selected_chat_model
        ctx.selected_video_generation_provider = WAN_MODEL
        ctx.selected_chat_model = "changed"
        urls.append("https://example.com/later.png")
        self.drain()
        self.assertEqual([kind for kind, _ in self.calls], ["image_prompt", "image", "video_prompt"])
        image_prompt, image, video_prompt = [kwargs for _, kwargs in self.calls]
        self.assertEqual(image_prompt["mode"], "normal")
        self.assertEqual(image_prompt["subject"], "")
        self.assertEqual(image["prompt"], "picture: story one")
        self.assertEqual(image["image_urls"], ["https://example.com/reference.png"])
        self.assertEqual(image["aspect_ratio"], "3:4")
        self.assertEqual(video_prompt["image_prompt"], image["prompt"])
        self.assertEqual(tasks[1].result.video_prompt, "motion: story one")
        self.assertEqual(tasks[1].result.source_turn_id, "t1")
        self.assertEqual(len(tasks), 3)
        for request in (image_prompt, video_prompt):
            self.assertEqual(request["ctx"].selected_chat_model, original_chat_model)
            self.assertEqual(request["ctx"].selected_video_generation_provider, "bytedance/seedance-1-5-pro")
        self.assertEqual(self.state.page2_image_prompt_mode, "closeup")
        self.assertEqual(self.state.page2_selected_media_id, "existing-preview")
        self.assertEqual(self.state.page2_video_source, "existing-input")
        self.assertEqual(self.state.page2_pending_video_source, tasks[1].result.id)
        self.assertEqual(len(self.state.page2_generated_media), 1)
        self.assertFalse(page2._one_click_video_busy())

    def test_earlier_chat_is_used_and_later_chat_does_not_change_source(self):
        self.state.page2_turns = []
        with patch.object(page2_service, "send_message", return_value="queued story"):
            page2._queue_chat_message(ctx=page2_service.default_context(), user_text="continue",
                                     hide_user_message=False, story_brain_enabled=False)
            tasks = self.queue()
            page2._advance_operation_queues()  # earlier chat
            page2._advance_operation_queues()  # apply chat, start image prompt
            source_turn = self.state.page2_turns[-1]
            self.state.page2_turns.append(Page2ConversationTurn(id="later", assistant_message="later story"))
            self.drain()
        self.assertEqual(tasks[1].result.source_turn_id, source_turn.id)
        self.assertEqual(tasks[1].result.video_prompt, "motion: queued story")

    def test_image_draft_edits_survive_and_delayed_video_edits_are_ignored(self):
        tasks = self.queue()
        page2._advance_operation_queues()  # image prompt request done, not yet applied
        self.state.page2_image_prompt = "edited image draft"
        page2._advance_operation_queues()  # image prompt applied, image requested
        page2._advance_operation_queues()  # image applied, video prompt requested
        self.state["_page2_video_prompt"] = "delayed video edit"
        page2._save_video_prompt_field(tasks[1].result.id, "")
        self.drain()
        self.assertEqual(self.state.page2_image_prompt, "edited image draft")
        self.assertEqual(tasks[1].result.video_prompt, "motion: story one")
        self.assertEqual(self.state.page2_operation_error, "")

    def test_each_failed_stage_cancels_successors_and_preserves_completed_image(self):
        for fail_index, kind in enumerate(("image_prompt", "image", "video_prompt")):
            with self.subTest(kind=kind):
                self.calls.clear()
                self.state.page2_generated_media = []
                self.state.page2_operation_error = ""
                self.fail_kind = kind
                self.queue()
                self.drain()
                self.assertEqual(len(self.calls), fail_index + 1)
                self.assertEqual(len(self.state.page2_generated_media), int(fail_index >= 2))
                self.assertEqual(self.state.page2_operation_error.count("已停止本次一键生成"), 1)
                self.assertFalse(page2._one_click_video_busy())
                if kind == "video_prompt":
                    self.assertEqual(self.state.page2_pending_video_source, self.state.page2_generated_media[-1].id)
                else:
                    self.assertNotIn("page2_pending_video_source", self.state)

    def test_duplicate_workflows_rejected_and_failure_does_not_cancel_other_tasks(self):
        self.fail_kind = "image_prompt"
        first = self.queue()
        with self.assertRaisesRegex(ValueError, "正在执行"):
            self.queue()
        unrelated = page2._enqueue_operation("image", "manual image", {
            "provider": "grok", "prompt": "unrelated picture",
        }, allow_same_kind=True)
        page2._advance_operation_queues()
        self.drain()
        self.assertFalse(first[1].is_started())
        self.assertEqual(unrelated.result.prompt, "unrelated picture")
        self.assertFalse(page2._one_click_video_busy())
        self.fail_kind = ""
        survivor = self.queue()
        self.drain()
        self.assertEqual(survivor[1].result.video_prompt, "motion: story one")

    def test_revoked_story_and_save_failure_stop_chain(self):
        tasks = self.queue()
        page2._advance_operation_queues()
        page2._advance_operation_queues()
        self.state.page2_turns = [Page2ConversationTurn(id="replacement", assistant_message="replacement")]
        self.drain()
        self.assertEqual([kind for kind, _ in self.calls], ["image_prompt", "image"])
        self.assertIn("原轮次已撤回", self.state.page2_operation_error)
        self.assertTrue(tasks[2].error)
        self.calls.clear()
        with patch.object(page2, "_upsert_record", side_effect=OSError("disk full")):
            self.queue()
            self.drain()
        self.assertEqual([kind for kind, _ in self.calls], ["image_prompt", "image"])
        self.assertIn("disk full", self.state.page2_operation_error)

    def test_preflight_failure_never_publishes_partial_workflow(self):
        for kwargs in (
            {"provider": "flux2-dev", "aspect_ratio": "match_input_image"},
            {"provider": "invalid"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.queue(**kwargs)
            self.assertFalse(page2.page2_operation_queue_busy())
        self.assertFalse(page2.page2_operation_queue_busy())
        self.state.page2_turns = []
        with self.assertRaisesRegex(ValueError, "助手回复"):
            self.queue()
        self.assertFalse(self.calls)

    def test_refiner_follows_provider_and_video_settings_are_not_needed(self):
        ctx = page2_service.default_context()
        ctx.selected_video_generation_provider = "not-used-until-manual-video"
        tasks = self.queue(ctx=ctx, provider=page2_service.ANILLUSTRIOUS_PROVIDER, refiner_prompt="hands")
        self.assertEqual(tasks[1].payload["refiner_prompt"], "hands")
        self.assertTrue(all("seconds" not in task.payload and "generate_audio" not in task.payload for task in tasks))
        self.drain()
        self.state.pop("page2_pending_video_source")
        other = self.queue(refiner_prompt="stale hidden refiner")
        self.assertEqual(other[1].payload["refiner_prompt"], "")

    def test_image25_workflow_uses_queued_quality(self):
        tasks = self.queue(provider="image2.5", aspect_ratio="3:2", quality="high")
        self.state.page2_image25_quality = "low"
        self.drain()
        image_call = next(kwargs for kind, kwargs in self.calls if kind == "image")
        self.assertEqual(tasks[1].payload["quality"], "high")
        self.assertEqual(image_call["quality"], "high")

    def test_image25_ordinary_generation_uses_queued_quality(self):
        page2._queue_image_generation(
            provider="image2.5", aspect_ratio="1:1", quality="medium", prompt="scene",
            image_urls=[], mode="normal", subject="",
        )
        self.state.page2_image25_quality = "high"
        self.drain()
        image_call = next(kwargs for kind, kwargs in self.calls if kind == "image")
        self.assertEqual(image_call["quality"], "medium")

    def test_nonempty_selected_or_pending_draft_blocks_workflow_but_whitespace_allows_it(self):
        source = GeneratedImageRecord(id="existing-input", provider="grok", prompt="image", video_prompt="motion")
        self.state.page2_generated_media = [source]
        with self.assertRaisesRegex(ValueError, "已有内容"):
            self.queue()
        source.video_prompt = " \n "
        self.queue()
        self.drain()
        # Completion is selected on the next render; a delayed click must already see its draft.
        with self.assertRaisesRegex(ValueError, "已有内容"):
            self.queue()
        self.state.pop("page2_pending_video_source")
        pending = page2._enqueue_operation("image", "pending image", {"pending_video_prompt": "motion"})
        self.state.page2_video_source = "pending:" + pending.id
        with self.assertRaisesRegex(ValueError, "已有内容"):
            self.queue()

    def test_preview_actions_cannot_change_locked_video_input(self):
        other = GeneratedImageRecord(id="other", provider="grok", prompt="other")
        self.state.page2_generated_media = [other]
        self.queue()
        with patch.object(page2.url_favorites, "clear_preview_url"), \
             patch.object(page2.url_favorites, "activate_generated_media_selection"):
            page2._view_completed_media("other-task", "other")
            key = page2._media_selectbox_key()
            self.state[key] = "other"
            page2._activate_media_selection(key)
        self.assertEqual(self.state.page2_video_source, "existing-input")
        self.assertNotIn("page2_pending_video_source", self.state)
        self.assertEqual(self.state.page2_selected_media_id, "other")

    def test_workflow_does_not_clear_a_newer_manual_selection(self):
        old = GeneratedImageRecord(provider="grok", prompt="old")
        newer = GeneratedImageRecord(provider="grok", prompt="newer")
        self.state.page2_generated_media = [old, newer]
        page2._set_reference_selection([old.id], preview=False)
        tasks = self.queue(image_urls=["media://" + old.id])
        # A delayed selection event cannot erase a newer user choice on completion.
        page2._set_reference_selection([newer.id], preview=False)
        self.drain()
        self.assertEqual(page2._manual_reference_ids(), [newer.id])
        self.assertEqual(self.state.page2_pending_video_source, newer.id)
        self.assertEqual(tasks[1].result.video_prompt, "motion: story one")

    def test_same_image_button_deduplicates_entire_prompt_image_chain(self):
        kwargs = dict(provider="grok", prompt="", image_urls=[], aspect_ratio="3:4", mode="normal", subject="")
        first = page2._queue_image_generation(**kwargs)
        second = page2._queue_image_generation(**{**kwargs, "prompt": "changed"})
        self.assertIs(first, second)
        self.assertEqual(len(self.state.page2_operation_queue), 2)
        page2._advance_operation_queues()
        self.assertIsNotNone(page2._media_button_task("image"))
        page2._advance_operation_queues()
        self.assertIsNotNone(page2._media_button_task("image"))
        self.drain()
        self.assertIsNone(page2._media_button_task("image"))
        self.assertEqual([kind for kind, _ in self.calls], ["image_prompt", "image"])
        self.assertIsNot(page2._queue_image_generation(**kwargs), first)

    def test_manual_video_button_cannot_bypass_lock_with_a_different_label_or_image(self):
        first = page2._enqueue_media_operation("video_prompt", "生视频prompt", {"source_id": "first"}, action="video")
        second = page2._enqueue_media_operation("video", "生视频", {"source_id": "second", "prompt": "edited"}, action="video")
        self.assertIs(first, second)
        self.assertEqual(len(self.state.page2_operation_queue), 1)
        self.drain()  # missing source fails before making a request
        self.assertIsNone(page2._media_button_task("video"))

    def test_distinct_buttons_can_queue_independently_and_failures_unlock(self):
        first = page2._enqueue_media_operation("image_prompt", "生成图片prompt", {}, action="image_prompt")
        self.assertIs(page2._enqueue_media_operation("image_prompt", "生成图片prompt", {}, action="image_prompt"), first)
        image = page2._queue_image_generation(provider="grok", prompt="ready", image_urls=[], aspect_ratio="3:4", mode="normal", subject="")
        self.queue()  # workflow is another button; its own steps never duplicate a user click
        self.assertEqual(len(self.state.page2_operation_queue), 5)
        self.fail_kind = "image_prompt"
        self.drain()
        self.assertIsNotNone(image.result)
        self.assertIsNone(page2._media_button_task("image_prompt"))
        self.assertIsNone(page2._media_button_task("image"))
        self.assertFalse(page2._one_click_video_busy())


class OneClickVideoUITests(unittest.TestCase):
    def setUp(self):
        # Widget callbacks run before the fixture script's `with patch` block.
        # Keep fake media IDs isolated from real on-disk expiry tombstones then too.
        import tempfile
        from pathlib import Path
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        from app.config import SettingsStore
        for patcher in (
            patch.object(page2.media_store, "MEDIA_DIR", Path(directory.name) / "media"),
            patch.object(page2, "_upsert_record"),
            patch.object(page2, "settings", SettingsStore(Path(directory.name) / "settings.json")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from app.models import GeneratedImageRecord, Page2ConversationTurn
from app.services import page2_service
from web.pages import page2
page2._ensure_state()
if "seeded" not in st.session_state:
    st.session_state.seeded = True
    st.session_state.page2_turns = [Page2ConversationTurn(id="t1", assistant_message="story")]
    st.session_state.page2_generated_media = [GeneratedImageRecord(id="old", provider="grok", prompt="old", source_turn_id="t1")]
def immediate(task, fn):
    task._fn = fn
    task._started = True
    task._run()
def prompt(**kwargs):
    if st.session_state.get("fixture_fail_prompt"):
        raise RuntimeError("fixture prompt failure")
    return "generated motion"
def image(**kwargs):
    if st.session_state.get("fixture_fail_image"):
        raise RuntimeError("fixture image failure")
    return GeneratedImageRecord(provider=kwargs["provider"], prompt=kwargs["prompt"])
def upload_requirements(*args, **kwargs):
    if not st.session_state.get("fixture_oversized"):
        return []
    return [{"record_id": "old", "size_bytes": 2 * 1024 * 1024, "budget_bytes": 900 * 1024}]
def upload_requirement(*args, **kwargs):
    values = upload_requirements(*args, **kwargs)
    return values[0] if values else None
with patch.object(page2.settings, "set"), patch.object(page2, "_upsert_record"), \\
     patch.object(page2.media_store, "request_cache"), \\
     patch.object(page2.media_store, "available", return_value=True), \\
     patch.object(page2.media_store, "cache_generated", side_effect=lambda record: record), \\
     patch.object(page2.url_favorites, "get_preview_url", return_value=""), \\
     patch.object(page2.url_favorites, "render_url_favorites"), \\
     patch.object(page2._Page2OperationTask, "start", new=immediate), \\
     patch.object(page2_service, "generate_image_prompt", return_value="generated image prompt"), \\
     patch.object(page2_service, "generate_image", side_effect=image), \\
     patch.object(page2_service, "generate_video_prompt", side_effect=prompt), \\
     patch.object(page2_service, "generate_video_from_image", side_effect=AssertionError("unexpected video request")), \\
     patch.object(page2_service, "load_context_from_settings", side_effect=page2_service.default_context), \\
     patch.object(page2.image_references, "temporary_upload_requirements", side_effect=upload_requirements), \\
     patch.object(page2.image_references, "temporary_upload_requirement", side_effect=upload_requirement), \\
     patch.object(page2.CloudinaryConfig, "api_key", return_value="test-key"), \\
     patch.object(page2.CloudinaryConfig, "api_secret", return_value="test-secret"):
    if st.button("推进队列", key="fixture_advance"):
        page2._advance_operation_queues()
    page2._render_media_column()
''').run()

    def finish_workflow(self, app):
        for _ in range(4):
            app.button(key="fixture_advance").click().run()
            self.assertFalse(app.exception)

    def test_wan3_text_and_image_modes_queue_resolution(self):
        app = self.app()
        app.selectbox(key="page2_video_model").set_value(WAN3_MODEL).run()
        self.assertEqual(app.selectbox(key="page2_wan3_resolution").options,
                         ["480p", "720p", "1080p"])
        self.assertEqual(app.selectbox(key="page2_wan3_resolution").value, "480p")
        app.selectbox(key="page2_wan3_input_mode").set_value("纯文字").run()
        app.selectbox(key="page2_wan3_aspect_ratio").set_value("9:16").run()
        app.text_area(key="_page2_video_prompt").input("山间日落").run()
        app.button(key="page2_generate_video").click().run()
        text_task = app.session_state.page2_operation_queue[-1]
        self.assertEqual(text_task.payload["prompt"], "山间日落")
        self.assertTrue(text_task.payload["text_only"])
        self.assertEqual(text_task.payload["source_id"], "")
        self.assertEqual(text_task.payload["aspect_ratio"], "9:16")
        self.assertEqual(text_task.payload["resolution"], "480p")
        app.session_state.page2_operation_queue = []
        app.selectbox(key="page2_wan3_input_mode").set_value("图片首帧").run()
        app.selectbox(key="page2_wan3_resolution").set_value("1080p").run()
        app.text_area(key="_page2_video_prompt").input("让图片动起来").run()
        app.button(key="page2_generate_video").click().run()
        image_task = app.session_state.page2_operation_queue[-1]
        self.assertFalse(image_task.payload["text_only"])
        self.assertEqual(image_task.payload["source_id"], "old")
        self.assertEqual(image_task.payload["resolution"], "1080p")
        self.assertFalse(app.exception)

    def test_wan3_text_mode_submits_without_image_source(self):
        app = self.app()
        app.session_state.page2_generated_media = []
        app.run()
        app.selectbox(key="page2_video_model").set_value(WAN3_MODEL).run()
        app.selectbox(key="page2_wan3_input_mode").set_value("纯文字").run()
        self.assertFalse(app.text_area(key="_page2_video_prompt").disabled)
        app.text_area(key="_page2_video_prompt").input("云从山间飘过").run()
        app.button(key="page2_generate_video").click().run()
        task = app.session_state.page2_operation_queue[-1]
        self.assertTrue(task.payload["text_only"])
        self.assertEqual(task.payload["source_id"], "")
        self.assertFalse(app.exception)

    def test_library_multiselect_drives_preview_both_generators_and_manual_priority(self):
        from app.services import media_store
        from test_image_references import png
        temporary = media_store.import_image(png(), "other-chat-temp.png")
        saved = media_store.import_image(png(color="red"), "other-chat-saved.png", permanent=True)
        app = self.app()
        self.assertEqual(len(app.multiselect[0].options), 3)  # two library images + this chat's image
        self.assertTrue(any("永久保存" in item for item in app.multiselect[0].options))
        self.assertTrue(any("当前聊天" in item for item in app.multiselect[0].options))
        app.multiselect[0].set_value([temporary.id, saved.id]).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.page2_selected_media_id, saved.id)
        self.assertEqual(app.session_state.page2_video_source, saved.id)
        app.text_area(key="_page2_video_prompt").input("library motion").run()
        app.button(key="page2_generate_video").click().run()
        self.assertEqual(app.session_state.page2_operation_queue[-1].payload["source_id"], saved.id)
        # Inspect the queued video without executing a paid request.
        app.session_state.page2_operation_queue = []
        app.button(key="page2_generate_image").click().run()
        image = next(task for task in app.session_state.page2_operation_queue if task.kind == "image")
        self.assertEqual(image.payload["image_urls"], ["media://" + temporary.id, "media://" + saved.id])
        # Changing selection while a normal generation waits changes only the next task.
        app.multiselect[0].set_value([temporary.id]).run()
        for _ in range(3):
            app.button(key="fixture_advance").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(image.payload["image_urls"], ["media://" + temporary.id, "media://" + saved.id])
        self.assertEqual(app.session_state.page2_video_source, temporary.id)
        self.assertEqual(app.session_state.page2_selected_media_id, temporary.id)
        app.multiselect[0].set_value([]).run()
        self.assertEqual(app.session_state.page2_video_source, image.result.id)
        restored = next(item for item in media_store.list_images() if item.id == saved.id)
        self.assertEqual(restored.video_prompt, "library motion")

    def test_workflow_consumes_references_only_after_successful_image(self):
        app = self.app()
        app.multiselect[0].set_value(["old"]).run()
        app.session_state.fixture_fail_image = True
        app.button(key="page2_generate_video_workflow").click().run()
        self.finish_workflow(app)
        self.assertEqual(app.multiselect[0].value, ["old"])
        self.assertEqual(app.session_state.page2_video_source, "old")
        app.session_state.fixture_fail_image = False
        app.button(key="page2_generate_video_workflow").click().run()
        tasks = app.session_state.page2_operation_queue
        self.assertEqual(tasks[1].payload["image_urls"], ["media://old"])
        self.finish_workflow(app)
        self.assertEqual(app.multiselect[0].value, [])
        self.assertEqual(app.session_state.page2_video_source, tasks[1].result.id)
        self.assertEqual(app.session_state.page2_selected_media_id, tasks[1].result.id)
        self.assertEqual(app.text_area(key="_page2_video_prompt").value, "generated motion")

    def test_image25_full_page_sends_quality_from_both_buttons(self):
        for button in ("page2_generate_image", "page2_generate_video_workflow"):
            with self.subTest(button=button):
                app = self.app()
                app.selectbox(key="_page2_image_provider").set_value("image2.5").run()
                self.assertEqual(app.selectbox(key="page2_image25_quality").options,
                                 ["low", "medium", "high", "xhigh", "max", "auto"])
                app.selectbox(key="page2_image25_quality").set_value("high").run()
                app.button(key=button).click().run()
                image = next(task for task in app.session_state.page2_operation_queue if task.kind == "image")
                self.assertEqual(image.payload["quality"], "high")
                app.selectbox(key="page2_image25_quality").set_value("low").run()
                self.assertEqual(image.payload["quality"], "high")
                self.assertFalse(app.exception)

    def test_oversized_reference_requires_dialog_confirmation_before_queueing(self):
        app = self.app()
        app.session_state.fixture_oversized = True
        app.multiselect[0].set_value(["old"]).run()
        app.button(key="page2_generate_image").click().run()
        self.assertFalse(app.session_state.page2_operation_queue)
        self.assertEqual(
            app.button(key="page2_confirm_cloudinary_upload").label,
            "上传并继续",
        )
        app.button(key="page2_cancel_cloudinary_upload").click().run()
        self.assertFalse(app.session_state.page2_operation_queue)
        app.button(key="page2_generate_image").click().run()
        app.button(key="page2_confirm_cloudinary_upload").click().run()
        image = next(
            task for task in app.session_state.page2_operation_queue if task.kind == "image"
        )
        self.assertTrue(image.payload["allow_temporary_upload"])

    def test_full_page_snapshots_image_settings_and_manual_video_uses_latest_settings(self):
        app = self.app()
        app.selectbox(key="_page2_image_provider").set_value("flux2-dev").run()
        app.selectbox(key="page2_image_aspect_ratio_flux2-dev").set_value("match_input_image").run()
        next(field for field in app.text_area if field.label.startswith("参考图片 URL")).input("https://example.com/ref.png").run()
        app.selectbox(key="_page2_image_prompt_mode").set_value("first_person").run()
        app.selectbox(key="page2_video_model").set_value("bytedance/seedance-1-5-pro").run()
        app.selectbox(key="page2_video_seconds").set_value(8).run()
        app.toggle(key="page2_video_generate_audio").set_value(False).run()
        app.button(key="page2_generate_video_workflow").click().run()
        self.assertFalse(app.exception)
        tasks = app.session_state.page2_operation_queue
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0].payload["mode"], "normal")
        self.assertEqual(app.selectbox(key="_page2_image_prompt_mode").value, "first_person")
        self.assertEqual(tasks[1].payload["provider"], "flux2-dev")
        self.assertEqual(tasks[1].payload["aspect_ratio"], "match_input_image")
        self.assertEqual(tasks[1].payload["image_urls"], ["https://example.com/ref.png"])
        self.finish_workflow(app)
        generated_id = tasks[1].result.id
        self.assertEqual(app.session_state.page2_video_source, generated_id)
        self.assertEqual(app.text_area(key="_page2_video_prompt").value, "generated motion")
        self.assertEqual(app.button(key="page2_generate_video").label, "生视频")
        app.selectbox(key="page2_video_model").set_value("bytedance/seedance-2.0-mini").run()
        app.selectbox(key="page2_video_seconds").set_value(10).run()
        app.toggle(key="page2_video_generate_audio").set_value(True).run()
        app.text_area(key="_page2_video_prompt").input("edited motion").run()
        app.button(key="page2_generate_video").click().run()
        video = app.session_state.page2_operation_queue[-1]
        self.assertEqual(video.kind, "video")
        self.assertEqual(video.payload["source_id"], generated_id)
        self.assertEqual(video.payload["prompt"], "edited motion")
        self.assertEqual(video.payload["seconds"], 10)
        self.assertTrue(video.payload["generate_audio"])
        self.assertEqual(video.payload["ctx"].selected_video_generation_provider, "bytedance/seedance-2.0-mini")
        app.text_area(key="_page2_video_prompt").input("").run()
        app.selectbox(key="_page2_image_provider").set_value(page2_service.ANILLUSTRIOUS_PROVIDER).run()
        app.text_area(key="_page2_refiner_prompt").input("refine hands").run()
        app.button(key="page2_generate_video_workflow").click().run()
        self.assertFalse(app.exception)
        second = app.session_state.page2_operation_queue[-3:]
        self.assertEqual(second[1].payload["refiner_prompt"], "refine hands")
        self.assertEqual(second[1].payload["image_urls"], [])

    def test_three_steps_lock_controls_then_handoff_without_generating_video(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.assertFalse(app.button(key="page2_generate_video").disabled)  # latest chat image is the default
        self.assertFalse(app.button(key="page2_generate_video_workflow").disabled)
        app.button(key="page2_generate_video_workflow").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state.page2_operation_queue), 3)
        self.assertEqual(app.session_state.page2_video_source, "old")
        for _ in range(4):
            self.assertTrue(app.multiselect[0].disabled)
            self.assertTrue(app.text_area(key="_page2_video_prompt").disabled)
            self.assertTrue(app.button(key="page2_generate_video").disabled)
            self.assertTrue(app.button(key="page2_generate_video_workflow").disabled)
            app.button(key="fixture_advance").click().run()
            self.assertFalse(app.exception)
        self.assertFalse(app.multiselect[0].disabled)
        self.assertFalse(app.text_area(key="_page2_video_prompt").disabled)
        self.assertFalse(app.button(key="page2_generate_video").disabled)
        self.assertEqual(app.button(key="page2_generate_video").label, "生视频")
        self.assertTrue(app.button(key="page2_generate_video_workflow").disabled)
        self.assertFalse(app.session_state.page2_operation_queue)
        self.assertFalse(app.session_state.page2_active_media_operations)
        self.assertEqual(len(app.session_state.page2_generated_media), 2)  # old + new image only
        self.assertTrue(all(item.media_kind == GeneratedMediaKind.IMAGE for item in app.session_state.page2_generated_media))
        self.assertFalse(app.session_state.page2_selected_media_id)

    def test_left_button_keeps_manual_behavior_and_right_requires_empty_draft(self):
        app = self.app()
        app.multiselect[0].set_value(["old"]).run()
        self.assertEqual(app.button(key="page2_generate_video").label, "生视频prompt")
        app.button(key="page2_generate_video").click().run()
        self.assertEqual(app.session_state.page2_operation_queue[-1].kind, "video_prompt")
        self.assertTrue(app.button(key="page2_generate_video").disabled)
        app.button(key="fixture_advance").click().run()
        app.button(key="fixture_advance").click().run()
        self.assertFalse(app.button(key="page2_generate_video").disabled)
        app.text_area(key="_page2_video_prompt").input("manual motion").run()
        self.assertEqual(app.button(key="page2_generate_video").label, "生视频")
        self.assertTrue(app.button(key="page2_generate_video_workflow").disabled)
        app.button(key="page2_generate_video").click().run()
        self.assertTrue(app.button(key="page2_generate_video").disabled)
        self.assertEqual(app.session_state.page2_operation_queue[-1].payload["prompt"], "manual motion")
        self.assertFalse(app.exception)
        app.text_area(key="_page2_video_prompt").input(" \n ").run()
        self.assertFalse(app.button(key="page2_generate_video_workflow").disabled)
        self.assertEqual(app.button(key="page2_generate_video").label, "生视频prompt")
        self.assertTrue(app.button(key="page2_generate_video").disabled)

    def test_image_buttons_lock_while_queued_and_restore_after_completion(self):
        app = self.app()
        app.button(key="page2_generate_image").click().run()
        self.assertTrue(app.button(key="page2_generate_image").disabled)
        self.assertFalse(app.button(key="page2_generate_image_prompt").disabled)
        self.assertEqual(len(app.session_state.page2_operation_queue), 2)
        app.button(key="page2_generate_image_prompt").click().run()
        self.assertTrue(app.button(key="page2_generate_image_prompt").disabled)
        self.assertEqual(len(app.session_state.page2_operation_queue), 3)
        app.button(key="fixture_advance").click().run()
        self.assertTrue(app.button(key="page2_generate_image").disabled)
        app.button(key="fixture_advance").click().run()
        self.assertTrue(app.button(key="page2_generate_image").disabled)
        app.button(key="fixture_advance").click().run()
        self.assertFalse(app.button(key="page2_generate_image").disabled)
        self.assertFalse(app.button(key="page2_generate_image_prompt").disabled)

    def test_failure_unlocks_controls_preserves_input_and_allows_retry(self):
        app = self.app()
        app.multiselect[0].set_value(["old"]).run()
        app.session_state["fixture_fail_prompt"] = True
        app.button(key="page2_generate_video_workflow").click().run()
        self.finish_workflow(app)
        # Image succeeded before prompt failure: its references were consumed,
        # and the new image remains the automatic video input.
        self.assertEqual(app.session_state.page2_video_source, app.session_state.page2_generated_media[-1].id)
        self.assertEqual(app.multiselect[0].value, [])
        self.assertFalse(app.multiselect[0].disabled)
        self.assertFalse(app.text_area(key="_page2_video_prompt").disabled)
        self.assertFalse(app.button(key="page2_generate_video").disabled)
        self.assertFalse(app.button(key="page2_generate_video_workflow").disabled)
        self.assertIn("fixture prompt failure", app.session_state.page2_operation_error)
        self.assertEqual(len(app.session_state.page2_generated_media), 2)
        app.session_state["fixture_fail_prompt"] = False
        app.button(key="page2_generate_video_workflow").click().run()
        self.assertEqual(len(app.session_state.page2_operation_queue), 3)


if __name__ == "__main__":
    unittest.main()
