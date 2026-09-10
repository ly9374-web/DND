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
from app.services import page2_service
from app.video_models import VIDEO_MODELS, WAN_MODEL, WAN_DURATION_FRAMES, validate_video_duration


MINI = "bytedance/seedance-2.0-mini"
PRO = "bytedance/seedance-1-5-pro"
CONTENT_URL = "https://openrouter.ai/api/v1/videos/job-1/content?index=0"


def response(data=None, status=200, headers=None, content=b"video-with-audio"):
    return Mock(status_code=status, text=json.dumps(data), json=Mock(return_value=data),
                headers=headers or {}, iter_content=Mock(return_value=[content]))


class VideoGenerationTests(unittest.TestCase):
    def setUp(self):
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
        self.post.assert_not_called()
        self.get.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
