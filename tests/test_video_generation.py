import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PIL import Image

from app.api import media_clients
from app.config import AppStorageKeys, SettingsStore
from app.models import GeneratedImageRecord
from app.services import page2_service, media_store
from app.video_models import (
    VIDEO_MODELS, WAN_MODEL, WAN3_MODEL, WAN_DURATION_FRAMES,
    validate_video_duration, validate_video_resolution,
)


MINI = "bytedance/seedance-2.0-mini"
PRO = "bytedance/seedance-1-5-pro"
CONTENT_URL = "https://openrouter.ai/api/v1/videos/job-1/content?index=0"


def response(data=None, status=200, headers=None, content=b"video-with-audio"):
    return Mock(status_code=status, text=json.dumps(data), json=Mock(return_value=data),
                headers=headers or {}, content=content,
                iter_content=Mock(return_value=[content]))


class VideoGenerationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(media_store, "MEDIA_DIR", Path(directory.name)),
            patch.object(media_store, "cache_generated", side_effect=lambda record: record),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        # All HTTP calls are mocked. No credentials, paid generations or real downloads.
        for name in ("post", "get"):
            patcher = patch.object(media_clients.requests, name,
                                   side_effect=AssertionError("Unexpected HTTP call"))
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        for name in ("OpenRouterConfig.api_key", "ReplicateConfig.api_token"):
            patcher = patch("app.api.media_clients." + name, return_value="test-key")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = 0
        for name, kwargs in (("monotonic", {"side_effect": lambda: self.clock}),
                             ("sleep", {"side_effect": self.advance})):
            patcher = patch.object(media_clients.time, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def advance(self, seconds):
        self.clock += seconds

    def generate(self, model=MINI, seconds=5, audio=True):
        return media_clients.OpenRouterVideoAPIClient.generate_video(
            model=model, seconds=seconds, generate_audio=audio,
            prompt="角色说：你好", image_url="https://example.com/image.png", aspect_ratio="3:4",
        )

    def test_seedance_payloads_audio_and_authenticated_content(self):
        for model, seconds, audio in ((MINI, 15, True), (PRO, 12, False)):
            self.post.side_effect = None
            self.post.return_value = response({"id": "job-1", "status": "pending",
                                               "polling_url": "https://untrusted.example/job"}, 202)
            self.get.side_effect = [response({"status": "in_progress"}),
                                    response({"status": "completed"})]
            self.assertEqual(self.generate(model, seconds, audio), CONTENT_URL)
            body = self.post.call_args.kwargs["json"]
            self.assertEqual((body["model"], body["duration"], body["generate_audio"]),
                             (model, seconds, audio))
            self.assertEqual(body["resolution"], "720p")
            self.assertEqual(body["frame_images"][0]["frame_type"], "first_frame")
            self.assertEqual(self.get.call_args.args[0], "https://openrouter.ai/api/v1/videos/job-1")
            self.assertEqual(self.get.call_args.kwargs["headers"], {"Authorization": "Bearer test-key"})

    def test_wan3_text_and_image_requests_use_selected_resolution(self):
        for resolution in ("480p", "720p", "1080p"):
            for image_url in (None, "https://example.com/image.png"):
                with self.subTest(resolution=resolution, image_url=image_url):
                    self.post.side_effect = None
                    self.post.return_value = response({"id": "job-1", "status": "completed"}, 202)
                    result = media_clients.OpenRouterVideoAPIClient.generate_video(
                        model=WAN3_MODEL, prompt="山间日落", seconds=30,
                        aspect_ratio="16:9", resolution=resolution,
                        image_url=image_url, generate_audio=False,
                    )
                    self.assertEqual(result, CONTENT_URL)
                    body = self.post.call_args.kwargs["json"]
                    self.assertEqual(body["model"], WAN3_MODEL)
                    self.assertEqual(body["resolution"], resolution)
                    self.assertEqual(body["duration"], 30)
                    self.assertEqual(body["aspect_ratio"], "16:9")
                    self.assertFalse(body["generate_audio"])
                    self.assertNotIn("quality", body)
                    if image_url:
                        self.assertEqual(body["frame_images"][0]["image_url"]["url"], image_url)
                    else:
                        self.assertNotIn("frame_images", body)

    def test_wan3_rejects_unsupported_resolution_and_other_models_need_images(self):
        with self.assertRaises(ValueError):
            validate_video_resolution(WAN3_MODEL, "4K")
        with self.assertRaisesRegex(ValueError, "输入图片"):
            media_clients.OpenRouterVideoAPIClient.generate_video(
                model=MINI, prompt="scene", seconds=5, aspect_ratio="16:9",
            )
        self.post.assert_not_called()

    def test_rejects_unsupported_durations_before_network(self):
        for model, seconds in ((MINI, 3), (MINI, 16), (PRO, 13), (PRO, 7.5), (WAN_MODEL, 4), (MINI, True)):
            with self.subTest(model=model, seconds=seconds), self.assertRaises(ValueError):
                validate_video_duration(model, seconds)
        self.post.assert_not_called()

    def test_wan_uses_selected_frames_without_changing_speed(self):
        self.post.side_effect = None
        self.post.return_value = response({"output": "https://example.com/video.mp4"})
        for seconds, frames in WAN_DURATION_FRAMES.items():
            media_clients.ReplicateVideoAPIClient.generate_wan_2_2_i2v_fast(
                "https://example.com/image.png", "动起来", seconds,
            )
            body = self.post.call_args.kwargs["json"]["input"]
            self.assertEqual((body["num_frames"], body["frames_per_second"]), (frames, 16))
        self.get.assert_not_called()

    def test_post_timeout_is_not_resubmitted(self):
        self.post.side_effect = requests.Timeout()
        with self.assertRaisesRegex(RuntimeError, "无法确认任务是否已创建"):
            self.generate()
        self.post.assert_called_once()
        self.get.assert_not_called()

    def test_transient_poll_errors_retry_get_only(self):
        self.post.side_effect = None
        self.post.return_value = response({"id": "job-1", "status": "pending"}, 202)
        self.get.side_effect = [requests.Timeout(), response({}, 429), response({}, 503),
                                response({"status": "completed"})]
        self.assertEqual(self.generate(), CONTENT_URL)
        self.post.assert_called_once()
        self.assertEqual(self.get.call_count, 4)

    def test_terminal_statuses_and_deadline_preserve_task_id(self):
        self.post.side_effect = None
        self.post.return_value = response({"id": "job-1", "status": "pending"}, 202)
        for status in ("failed", "cancelled", "expired"):
            self.get.side_effect = [response({"status": status, "error": "reason"})]
            with self.assertRaisesRegex(RuntimeError, "reason.*job-1"):
                self.generate()
        with patch.object(media_clients.OpenRouterVideoAPIClient, "POLL_TIMEOUT_SECONDS", 1):
            with self.assertRaisesRegex(TimeoutError, "job-1"):
                self.generate()

    def test_download_redirect_does_not_forward_api_key_and_preserves_bytes(self):
        redirect = response(status=302, headers={"Location": "https://delivery.example/video.mp4"})
        video = response(headers={"Content-Type": "video/mp4"})
        self.get.side_effect = [redirect, video]
        self.assertEqual(media_clients.download_video_bytes(CONTENT_URL, api_key="secret"),
                         b"video-with-audio")
        self.assertEqual(self.get.call_args_list[0].kwargs["headers"], {"Authorization": "Bearer secret"})
        self.assertEqual(self.get.call_args_list[1].kwargs["headers"], {})
        redirect.close.assert_called_once()
        video.close.assert_called_once()

    def test_download_missing_key_and_expired_link_are_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "API Key"):
            media_clients.download_video_bytes(CONTENT_URL)
        self.get.assert_not_called()
        self.get.side_effect = [response(status=410)]
        with self.assertRaisesRegex(RuntimeError, "失效"):
            media_clients.download_video_bytes(CONTENT_URL, api_key="test")

    def test_service_preserves_fractional_duration_and_matches_portrait_ratio(self):
        buffer = io.BytesIO()
        Image.new("RGB", (30, 40)).save(buffer, format="PNG")
        source = GeneratedImageRecord(provider="grok", prompt="图片",
                                      image_data_base64=base64.b64encode(buffer.getvalue()).decode())
        ctx = page2_service.default_context()
        with patch.object(media_clients.CloudinaryUploader, "upload_image_bytes", return_value="https://example.com/image.png"), \
             patch.object(media_clients.ReplicateVideoAPIClient, "generate_wan_2_2_i2v_fast", return_value="https://example.com/video.mp4"), \
             patch.object(media_clients.OpenRouterVideoAPIClient, "generate_video", return_value=CONTENT_URL) as seedance:
            result = page2_service.generate_video_from_image(ctx=ctx, source_record=source, prompt="动", seconds=7.5)
            self.assertEqual(result.duration_seconds, 121 / 16)
            self.assertEqual(GeneratedImageRecord.from_dict(result.to_dict()).duration_seconds, 121 / 16)
            ctx.selected_video_generation_provider = PRO
            result = page2_service.generate_video_from_image(ctx=ctx, source_record=source, prompt="说话", seconds=12)
            self.assertEqual(seedance.call_args.kwargs["aspect_ratio"], "3:4")
            self.assertTrue(seedance.call_args.kwargs["generate_audio"])
            self.assertEqual(result.video_generation_provider, PRO)
            ctx.selected_video_generation_provider = WAN3_MODEL
            result = page2_service.generate_video_from_image(
                ctx=ctx, source_record=source, prompt="风吹树叶", seconds=30,
                resolution="1080p",
            )
            self.assertEqual(seedance.call_args.kwargs["resolution"], "1080p")
            self.assertEqual(seedance.call_args.kwargs["aspect_ratio"], "3:4")
            self.assertEqual(result.video_generation_provider, WAN3_MODEL)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_wan3_text_service_has_no_source_image(self):
        ctx = page2_service.default_context()
        ctx.selected_video_generation_provider = WAN3_MODEL
        with patch.object(media_clients.OpenRouterVideoAPIClient, "generate_video", return_value=CONTENT_URL) as generate:
            record = page2_service.generate_video_from_text(
                ctx=ctx, prompt="山间日落", seconds=5, aspect_ratio="9:16", resolution="480p",
            )
        self.assertEqual(record.video_url_string, CONTENT_URL)
        self.assertEqual(record.source_image_id, "")
        self.assertEqual(record.video_generation_provider, WAN3_MODEL)
        self.assertEqual(generate.call_args.kwargs["aspect_ratio"], "9:16")
        self.assertEqual(generate.call_args.kwargs["resolution"], "480p")

    def test_settings_migration_retires_keys_without_touching_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            history = [{"video_generation_provider": "domoai", "video_url_string": "https://example.com/old.mp4"}]
            path.write_text(json.dumps({"domoaiApiKey": "old", "zhipuApiKey": "old",
                                        "page2SelectedVideoGenerationProvider": "zhipu", "history": history}))
            store = SettingsStore(path)
            self.assertEqual(store.get(AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER), WAN_MODEL)
            self.assertEqual(store.get("history"), history)
            self.assertNotIn("domoaiApiKey", store.values)
            self.assertNotIn("zhipuApiKey", store.values)


class VideoUITests(unittest.TestCase):
    def test_right_panel_video_choices_survive_model_switch_and_restart(self):
        from streamlit.testing.v1 import AppTest
        from web.pages import page2

        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            def app():
                return AppTest.from_string('''
from web.pages import page2
from app.video_models import WAN3_MODEL
model = page2._render_video_model_selector()
page2._render_video_duration_controls(model)
if model == WAN3_MODEL:
    page2._render_wan3_resolution_selector()
    if page2._render_wan3_input_mode(None):
        page2._render_wan3_aspect_ratio_selector()
''').run()

            with patch.object(page2, "settings", store):
                current = app()
                current.selectbox(key="page2_video_model").set_value(WAN3_MODEL).run()
                current.selectbox(key="page2_video_seconds").set_value(12).run()
                current.toggle(key="page2_video_generate_audio").set_value(False).run()
                current.selectbox(key="page2_wan3_resolution").set_value("1080p").run()
                current.selectbox(key="page2_wan3_aspect_ratio").set_value("9:16").run()
                self.assertEqual(store.get(AppStorageKeys.PAGE2_VIDEO_DURATIONS)[WAN3_MODEL], 12)
                self.assertFalse(store.get(AppStorageKeys.PAGE2_VIDEO_AUDIO_BY_MODEL)[WAN3_MODEL])
                self.assertEqual(store.get(AppStorageKeys.PAGE2_WAN3_RESOLUTION), "1080p")
                self.assertEqual(store.get(AppStorageKeys.PAGE2_WAN3_INPUT_MODE), "纯文字")
                self.assertEqual(store.get(AppStorageKeys.PAGE2_WAN3_ASPECT_RATIO), "9:16")

                current.selectbox(key="page2_video_model").set_value(MINI).run()
                self.assertEqual(current.selectbox(key="page2_video_seconds").value, 5)
                self.assertTrue(current.toggle(key="page2_video_generate_audio").value)
                current.selectbox(key="page2_video_seconds").set_value(8).run()
                current.selectbox(key="page2_video_model").set_value(WAN3_MODEL).run()
                self.assertEqual(current.selectbox(key="page2_video_seconds").value, 12)
                self.assertFalse(current.toggle(key="page2_video_generate_audio").value)
                self.assertEqual(current.selectbox(key="page2_wan3_aspect_ratio").value, "9:16")

                restarted = app()
                self.assertEqual(restarted.selectbox(key="page2_video_model").value, WAN3_MODEL)
                self.assertEqual(restarted.selectbox(key="page2_video_seconds").value, 12)
                self.assertFalse(restarted.toggle(key="page2_video_generate_audio").value)
                self.assertEqual(restarted.selectbox(key="page2_wan3_resolution").value, "1080p")
                self.assertEqual(restarted.selectbox(key="page2_wan3_input_mode").value, "纯文字")
                self.assertEqual(restarted.selectbox(key="page2_wan3_aspect_ratio").value, "9:16")
                self.assertFalse(current.exception)
                self.assertFalse(restarted.exception)

    def test_image_prompt_mode_survives_restart(self):
        from streamlit.testing.v1 import AppTest
        from web.pages import page2

        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            with patch.object(page2, "settings", store):
                def app():
                    return AppTest.from_string('''
from web.pages import page2
page2._ensure_state()
page2._render_image_prompt_mode_selector()
''').run()

                current = app()
                current.selectbox(key="_page2_image_prompt_mode").set_value("closeup").run()
                self.assertEqual(store.get(AppStorageKeys.PAGE2_IMAGE_PROMPT_MODE), "closeup")
                restarted = app()
                self.assertEqual(restarted.selectbox(key="_page2_image_prompt_mode").value, "closeup")
                self.assertFalse(current.exception)
                self.assertFalse(restarted.exception)

    def test_queue_preserves_fractional_seconds_and_audio_choice(self):
        from web.pages import page2
        record = GeneratedImageRecord(id="source", provider="grok", prompt="图片")
        task = page2._Page2OperationTask(kind="video", label="生成视频", scope_id="test", payload={
            "ctx": page2_service.default_context(), "source_id": "source",
            "seconds": 7.5, "generate_audio": False,
        })
        session = Mock(page2_generated_media=[record])
        with patch.object(page2.st, "session_state", session), \
             patch.object(page2_service, "generate_video_from_image") as generate:
            page2._prepare_video_operation(task)()
            self.assertEqual(generate.call_args.kwargs["seconds"], 7.5)
            self.assertFalse(generate.call_args.kwargs["generate_audio"])

    def test_wan3_text_task_runs_without_image_source(self):
        from web.pages import page2
        ctx = page2_service.default_context()
        ctx.selected_video_generation_provider = WAN3_MODEL
        task = page2._Page2OperationTask(kind="video", label="生成视频", scope_id="test", payload={
            "ctx": ctx, "text_only": True, "prompt": "山间日落", "seconds": 5,
            "aspect_ratio": "16:9", "resolution": "480p", "generate_audio": True,
        })
        session = Mock(page2_generated_media=[])
        with patch.object(page2.st, "session_state", session), \
             patch.object(page2_service, "generate_video_from_text") as generate:
            page2._prepare_video_operation(task)()
            self.assertEqual(generate.call_args.kwargs["prompt"], "山间日落")
            self.assertEqual(generate.call_args.kwargs["resolution"], "480p")

    def test_model_switch_resets_invalid_duration_and_applies_immediately(self):
        from streamlit.testing.v1 import AppTest
        from web.pages import page2
        with patch.object(page2.settings, "get", return_value=MINI), patch.object(page2.settings, "set") as save:
            app = AppTest.from_string('''
from web.pages import page2
model = page2._render_video_model_selector()
page2._render_video_duration_controls(model)
''').run()
            self.assertEqual(len(app.exception), 0)
            app.selectbox(key="page2_video_seconds").set_value(15).run()
            app.selectbox(key="page2_video_model").set_value(PRO).run()
            self.assertEqual(app.selectbox(key="page2_video_seconds").value, 5)
            self.assertTrue(app.toggle[0].value)
            save.assert_called_with(AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER, PRO)
            app.selectbox(key="page2_video_model").set_value(WAN_MODEL).run()
            app.selectbox(key="page2_video_seconds").set_value(7.5).run()
            self.assertEqual(app.selectbox(key="page2_video_seconds").value, 7.5)
            self.assertEqual(len(app.toggle), 0)
            self.assertEqual(len(app.exception), 0)

    def test_preview_and_download_do_not_fetch_on_render(self):
        from streamlit.testing.v1 import AppTest
        from web.pages import page2
        with patch.object(page2, "download_video_bytes", return_value=b"video") as fetch, \
             patch.object(page2.OpenRouterConfig, "api_key", return_value="test-key"):
            app = AppTest.from_string('''
from web.pages import page2
from app.models import GeneratedImageRecord
record = GeneratedImageRecord(id="video-1", provider="grok", prompt="",
    video_url_string="https://openrouter.ai/api/v1/videos/job-1/content?index=0")
page2._render_video_preview(record)
''').run()
            self.assertEqual(len(app.exception), 0)
            fetch.assert_not_called()
            app.button(key="page2_load_video_video-1").click().run()
            fetch.assert_called_once()
            app.run()
            fetch.assert_called_once()
            self.assertEqual(len(app.exception), 0)


class ImagePersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        image = io.BytesIO()
        Image.new("RGB", (1, 1), "red").save(image, format="PNG")
        self.image_bytes = image.getvalue()
        for patcher in (
            patch.object(media_store, "MEDIA_DIR", Path(directory.name)),
            patch.object(media_store, "request_cache"),
            patch.object(media_store, "download_image_bytes", return_value=self.image_bytes),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_image_download_and_expiry_probe(self):
        image = response(status=200, content=b"image-bytes")
        with patch.object(media_clients.requests, "get", return_value=image):
            self.assertEqual(
                media_clients.download_image_bytes("https://example.com/image.png"),
                b"image-bytes",
            )

        for status, expected in ((200, False), (403, False), (404, True), (410, True), (503, False)):
            with self.subTest(status=status):
                result = response(status=status)
                with patch.object(media_clients.requests, "get", return_value=result):
                    self.assertEqual(
                        media_clients.image_url_is_expired("https://example.com/image.png"),
                        expected,
                    )
                result.close.assert_called_once()

    def test_save_and_delete_image_record(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        selected = GeneratedImageRecord(
            id="image-1", provider="nanoPro", prompt="forest",
            image_url_string="https://example.com/image.png",
        )
        other = GeneratedImageRecord(id="image-2", provider="nanoPro", prompt="other")
        session = SessionState(
            page2_generated_media=[selected, other],
            page2_selected_media_id="image-1",
        )
        with patch.object(page2.st, "session_state", session), \
             patch.object(page2, "_upsert_record") as upsert:
            page2._save_image_record(selected)
            self.assertTrue(media_store.info(selected)["saved"])
            self.assertEqual(media_store.read_bytes(selected), self.image_bytes)
            upsert.assert_called_once()

            upsert.reset_mock()
            page2._delete_media_record(selected.id)
            self.assertEqual(session.page2_generated_media, [other])
            self.assertEqual(session.page2_selected_media_id, other.id)
            upsert.assert_called_once()

    def test_deleting_selected_image_previews_latest_remaining_image(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        images = [
            GeneratedImageRecord(id=f"image-{number}", provider="nanoPro", prompt=str(number))
            for number in (1, 2, 3)
        ]
        session = SessionState(
            page2_generated_media=images,
            page2_selected_media_id="image-3",
        )
        with (
            patch.object(page2.st, "session_state", session),
            patch.object(page2.media_store, "delete"),
            patch.object(page2.media_store, "available", return_value=True),
            patch.object(page2.url_favorites, "clear_preview_url"),
            patch.object(page2, "_upsert_record"),
        ):
            page2._delete_media_record("image-3")
            self.assertEqual(session.page2_selected_media_id, "image-2")
            self.assertEqual(session.page2_pending_media_record_select, "image-2")
            self.assertEqual(session.page2_media_selection_nonce, 1)

            page2._delete_media_record("image-2")
            self.assertEqual(session.page2_selected_media_id, "image-1")

            page2._delete_media_record("image-1")
            self.assertEqual(session.page2_selected_media_id, "")
            self.assertNotIn("page2_pending_media_record_select", session)

    def test_deleted_image_preview_switches_in_rendered_selector(self):
        from streamlit.testing.v1 import AppTest

        encoded = base64.b64encode(self.image_bytes).decode("ascii")
        app = AppTest.from_string(f'''
import streamlit as st
from unittest.mock import patch
from app.models import GeneratedImageRecord
from web.pages import page2

page2._ensure_state()
if not st.session_state.get("fixture_initialized"):
    st.session_state.fixture_initialized = True
    st.session_state.page2_generated_media = [
        GeneratedImageRecord(id=f"image-{{number}}", provider="nanoPro", prompt=str(number),
                             image_data_base64="{encoded}")
        for number in (1, 2, 3)
    ]
    st.session_state.page2_selected_media_id = "image-3"
with patch.object(page2.media_store, "request_cache"), \\
     patch.object(page2.media_store, "available", return_value=True), \\
     patch.object(page2.media_store, "delete"), \\
     patch.object(page2, "_upsert_record"), \\
     patch.object(page2.url_favorites, "get_preview_url", return_value=""), \\
     patch.object(page2.url_favorites, "clear_preview_url"):
    if st.button("模拟删除第三张"):
        page2._delete_media_record("image-3")
    page2._render_media_column()
''').run()

        self.assertEqual(len(app.exception), 0)
        next(button for button in app.button if button.label == "模拟删除第三张").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.page2_selected_media_id, "image-2")
        self.assertEqual(app.selectbox(key="page2_media_record_select_1").value, "image-2")

    def test_image_media_ui_has_three_actions_without_provider_caption(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from app.models import GeneratedImageRecord
from web.pages import page2

page2._ensure_state()
if not st.session_state.get("fixture_initialized"):
    st.session_state.fixture_initialized = True
    st.session_state.page2_generated_media = [GeneratedImageRecord(
        id="image-1", provider="nanoPro", prompt="forest",
        image_url_string="https://example.com/image.png",
    )]
    st.session_state.page2_selected_media_id = "image-1"
with patch.object(page2, "_upsert_record"), \
     patch.object(page2.url_favorites, "get_preview_url", return_value=""):
    page2._render_media_column()
''').run()

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(button.label == "删除" for button in app.button))
        self.assertTrue(any(button.label == "保存" for button in app.button))
        self.assertEqual(len(app.get("download_button")), 1)
        captions = [caption.value for caption in app.caption]
        self.assertNotIn("image • provider=nanoPro", captions)

        app.button(key="page2_save_image_image-1").click().run()
        self.assertTrue(media_store.info(app.session_state.page2_generated_media[0])["saved"])
        self.assertEqual(len(app.exception), 0)

    def test_expired_unsaved_image_is_removed_from_selector(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from app.models import GeneratedImageRecord
from web.pages import page2

page2._ensure_state()
if not st.session_state.get("fixture_initialized"):
    st.session_state.fixture_initialized = True
    st.session_state.page2_generated_media = [GeneratedImageRecord(
        id="expired-image", provider="nanoPro", prompt="forest",
        image_url_string="https://example.com/expired.png",
    )]
    st.session_state.page2_selected_media_id = "expired-image"
    from app.api.media_clients import MediaExpiredError
    with patch.object(page2.media_store, "download_image_bytes", side_effect=MediaExpiredError("gone")):
        page2.media_store.cache_generated(st.session_state.page2_generated_media[0])
with patch.object(page2, "_upsert_record"), \
     patch.object(page2.url_favorites, "get_preview_url", return_value=""):
    page2._render_media_column()
''').run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.session_state.page2_generated_media), 1)
        self.assertFalse(media_store.available(app.session_state.page2_generated_media[0]))
        self.assertNotIn(
            "page2_media_record_select",
            [selectbox.key for selectbox in app.get("selectbox")],
        )


if __name__ == "__main__":
    unittest.main()
