"""Model payloads and durable image preferences, without paid API calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from app.api import media_clients
from app.config import AppStorageKeys, SettingsStore
from app.image_models import DEFAULT_IMAGE_PROVIDER, IMAGE_ASPECT_RATIOS, image_aspect_ratios
from app.services import page2_service


class ImageModelRequestTests(unittest.TestCase):
    def setUp(self):
        for target, kwargs in (
            ("ReplicateConfig.api_token", {"return_value": "test-token"}),
            ("debug_log", {}),
        ):
            patcher = patch("app.api.media_clients." + target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def generate(self, provider, **kwargs):
        data = {"status": "succeeded", "output": ["https://example.com/result.jpg"]}
        response = Mock(status_code=200, text=json.dumps(data), json=Mock(return_value=data))
        with patch.object(media_clients.requests, "post", return_value=response) as post:
            result = page2_service.generate_image(provider, "test prompt", **kwargs)
        self.assertEqual(result.provider, "flux2-dev" if provider == "flux" else provider)
        self.assertEqual(result.image_url_string, data["output"][0])
        return post.call_args

    def test_seedream_4k_single_image_with_and_without_references(self):
        for refs, ratio in (([], "16:9"), (["https://example.com/ref.jpg"], "match_input_image")):
            with self.subTest(ratio=ratio):
                call = self.generate("seedream-4.5", image_urls=refs, aspect_ratio=ratio)
                self.assertEqual(call.args[0], "https://api.replicate.com/v1/models/bytedance/seedream-4.5/predictions")
                self.assertEqual(call.kwargs["json"], {"input": {
                    "prompt": "test prompt", "size": "4K", "aspect_ratio": ratio,
                    "disable_safety_checker": True,
                    "image_input": refs, "sequential_image_generation": "disabled", "max_images": 1,
                }})

    def test_flux_dev_payload_and_old_flux_alias(self):
        for provider in ("flux2-dev", "flux"):
            with self.subTest(provider=provider):
                call = self.generate(provider, aspect_ratio="9:16")
                self.assertEqual(call.args[0], "https://api.replicate.com/v1/models/black-forest-labs/flux-2-dev/predictions")
                self.assertEqual(call.kwargs["json"], {"input": {
                    "prompt": "test prompt", "go_fast": True, "aspect_ratio": "9:16",
                    "disable_safety_checker": True,
                    "input_images": [], "output_format": "jpg", "output_quality": 80,
                }})
        refs = ["https://example.com/ref.jpg"]
        call = self.generate("flux2-dev", image_urls=refs, aspect_ratio="match_input_image")
        self.assertEqual(call.kwargs["json"]["input"]["input_images"], refs)

    def test_image25_all_qualities_and_references_reach_replicate(self):
        refs = ["https://example.com/ref.jpg"]
        for quality in ("low", "medium", "high", "xhigh", "max", "auto"):
            with self.subTest(quality=quality):
                call = self.generate("image2.5", quality=quality, aspect_ratio="3:2", image_urls=refs)
                self.assertEqual(call.args[0], "https://api.replicate.com/v1/models/openai/gpt-image-2.5-sunburst/predictions")
                self.assertEqual(call.kwargs["json"], {"input": {
                    "prompt": "test prompt", "quality": quality, "aspect_ratio": "3:2",
                    "input_images": refs, "number_of_images": 1, "moderation": "low", "output_format": "webp",
                }})
        self.assertEqual(self.generate("image2.5").kwargs["json"]["input"]["quality"], "low")
        self.assertEqual(self.generate("image2.5", aspect_ratio="16:9").kwargs["json"]["input"]["aspect_ratio"], "16:9")

    def test_image25_rejects_invalid_quality_before_network_call(self):
        with patch.object(media_clients.requests, "post") as post:
            for generate in (page2_service.generate_image, media_clients.ReplicateImageAPIClient.generate_image):
                with self.subTest(generate=generate), self.assertRaisesRegex(ValueError, "quality"):
                    generate("image2.5", "prompt", quality="invalid")
            post.assert_not_called()

    def test_removed_image2_is_rejected_before_network_call(self):
        with patch.object(media_clients.requests, "post") as post:
            for generate in (page2_service.generate_image, media_clients.ReplicateImageAPIClient.generate_image):
                with self.subTest(generate=generate), self.assertRaisesRegex(ValueError, "image2"):
                    generate("image2", "prompt")
            post.assert_not_called()

    def test_image25_quality_is_not_sent_to_other_models(self):
        for provider in ("nano", "nanoPro", "seedream-4.5", "flux2-dev"):
            with self.subTest(provider=provider):
                call = self.generate(provider, quality="high")
                self.assertNotIn("quality", call.kwargs["json"]["input"])

    def test_invalid_references_and_ratios_rejected_before_network_call(self):
        cases = [
            ("flux2-dev", "match_input_image", [], "至少"),
            ("seedream-4.5", "match_input_image", [], "至少"),
            ("flux2-dev", "1:1", ["https://example.com/ref.jpg"] * 5, "4 张"),
            ("seedream-4.5", "1:1", ["https://example.com/ref.jpg"] * 15, "14 张"),
            ("nano", "16:9", [], "图片尺寸"),
            ("image2.5", "match_input_image", [], "图片尺寸"),
        ]
        with patch.object(media_clients.requests, "post") as post:
            for provider, ratio, refs, message in cases:
                with self.subTest(provider=provider, ratio=ratio), self.assertRaisesRegex(ValueError, message):
                    page2_service.generate_image(provider, "prompt", image_urls=refs, aspect_ratio=ratio)
            post.assert_not_called()


class ImagePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def app(self):
        return AppTest.from_string('''
from unittest.mock import patch
from pathlib import Path
import streamlit as st
from app.config import SettingsStore
from app.models import ChatRecord
from web.pages import page2
store = SettingsStore(Path(PATH))
with patch.object(page2, "settings", store), patch.object(page2.url_favorites, "clear_preview_url"):
    page2._ensure_state()
    if st.button("新对话", key="new"):
        st.session_state.page2_operation_queue = []
        page2.start_new_conversation(reset_settings=True)
    if st.button("打开旧对话", key="old"):
        st.session_state.page2_operation_queue = []
        record = ChatRecord(id="old", title="旧对话", turns=[], system_prompt="prompt")
        with patch.object(page2, "get_arg", return_value="old"), patch.object(page2.chat_records, "load_record_by_id", return_value=record):
            page2._load_record_from_nav_if_needed()
    provider = page2._render_image_model_selector()
    quality = page2._render_image_quality_selector(provider)
    ratio = page2._render_image_aspect_ratio_selector(provider)
    if st.button("生成图片", key="generate"):
        try:
            page2._queue_image_generation(provider=provider, prompt="prompt", image_urls=[], aspect_ratio=ratio, mode="normal", subject="", quality=quality)
        except ValueError as exc:
            st.error(str(exc))
    if st.button("模拟任务失败", key="fail"):
        for task in st.session_state.page2_operation_queue:
            task.error = RuntimeError("test failure")
'''.replace("PATH", repr(str(self.path)))).run()

    def saved(self):
        return SettingsStore(self.path).get(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER)

    def test_old_image2_selection_migrates_to_image25(self):
        store = SettingsStore(self.path)
        store.set(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, "image2")
        app = self.app()
        self.assertEqual(app.selectbox(key="_page2_image_provider").value, "image2.5")
        self.assertEqual(self.saved(), "image2.5")
        self.assertNotIn("image2", app.selectbox(key="_page2_image_provider").options)
        self.assertFalse(app.exception)

    def test_image25_quality_defaults_low_and_survives_switches_and_restart(self):
        app = self.app()
        self.assertNotIn("page2_image25_quality", [item.key for item in app.selectbox])
        app.selectbox(key="_page2_image_provider").select("image2.5").run()
        self.assertEqual(app.selectbox(key="page2_image25_quality").value, "low")
        self.assertEqual(app.selectbox(key="page2_image25_quality").options,
                         ["low", "medium", "high", "xhigh", "max", "auto"])
        self.assertEqual(app.selectbox(key="page2_image_aspect_ratio_image2.5").options,
                         ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "auto"])
        app.selectbox(key="page2_image_aspect_ratio_image2.5").select("16:9").run()
        app.selectbox(key="page2_image25_quality").select("max").run()
        app.button(key="generate").click().run()
        self.assertEqual(app.session_state.page2_operation_queue[-1].payload["quality"], "max")
        self.assertEqual(SettingsStore(self.path).get(AppStorageKeys.PAGE2_IMAGE25_QUALITY), "max")
        app.selectbox(key="_page2_image_provider").select("nano").run()
        self.assertNotIn("page2_image25_quality", [item.key for item in app.selectbox])
        app.selectbox(key="_page2_image_provider").select("image2.5").run()
        self.assertEqual(app.selectbox(key="page2_image25_quality").value, "max")
        for button in ("new", "old"):
            app.button(key=button).click().run()
            self.assertEqual(app.selectbox(key="page2_image25_quality").value, "max")
        restarted = self.app()
        self.assertEqual(restarted.selectbox(key="page2_image25_quality").value, "max")
        self.assertEqual(restarted.selectbox(key="page2_image_aspect_ratio_image2.5").value, "16:9")
        self.assertFalse(app.exception)
        self.assertFalse(restarted.exception)

    def test_invalid_saved_image25_quality_falls_back_to_low(self):
        store = SettingsStore(self.path)
        store.set(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, "image2.5")
        store.set(AppStorageKeys.PAGE2_IMAGE25_QUALITY, "invalid")
        app = self.app()
        self.assertEqual(app.selectbox(key="page2_image25_quality").value, "low")
        self.assertFalse(app.exception)

    def test_last_selected_model_survives_new_old_and_restarted_sessions(self):
        app = self.app()
        self.assertEqual(app.selectbox(key="_page2_image_provider").value, DEFAULT_IMAGE_PROVIDER)
        app.selectbox(key="_page2_image_provider").select("image2.5").run()
        self.assertEqual(self.saved(), "image2.5")
        app.selectbox(key="_page2_image_provider").select("nano").run()
        app.button(key="new").click().run()
        self.assertEqual(app.selectbox(key="_page2_image_provider").value, "nano")
        app.selectbox(key="_page2_image_provider").select("flux2-dev").run()
        app.selectbox(key="_page2_image_provider").select("nano").run()
        app.button(key="old").click().run()
        self.assertEqual(app.selectbox(key="_page2_image_provider").value, "nano")
        restarted = self.app()
        self.assertEqual(restarted.selectbox(key="_page2_image_provider").value, "nano")
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(restarted.exception), 0)

    def test_failed_validation_does_not_enqueue_or_undo_selection(self):
        app = self.app()
        app.selectbox(key="_page2_image_provider").select("flux2-dev").run()
        app.selectbox(key="page2_image_aspect_ratio_flux2-dev").select("match_input_image").run()
        app.button(key="generate").click().run()
        self.assertIn("至少", app.error[0].value)
        self.assertEqual(self.saved(), "flux2-dev")
        self.assertEqual(app.session_state["page2_operation_queue"], [])
        self.assertEqual(len(app.exception), 0)

    def test_old_flux_preferences_migrate_and_ratios_remain_model_specific(self):
        store = SettingsStore(self.path)
        store.set(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, "flux")
        store.set(AppStorageKeys.PAGE2_IMAGE_ASPECT_RATIOS, {"flux": "3:4", "nano": "4:3"})
        app = self.app()
        self.assertEqual(app.selectbox(key="_page2_image_provider").value, "flux2-dev")
        self.assertEqual(self.saved(), "flux2-dev")
        self.assertEqual(app.selectbox(key="page2_image_aspect_ratio_flux2-dev").value, "3:4")
        app.selectbox(key="page2_image_aspect_ratio_flux2-dev").select("16:9").run()
        app.selectbox(key="_page2_image_provider").select("nano").run()
        self.assertEqual(app.selectbox(key="page2_image_aspect_ratio_nano").options, list(IMAGE_ASPECT_RATIOS))
        self.assertEqual(app.selectbox(key="page2_image_aspect_ratio_nano").value, "4:3")
        app.selectbox(key="_page2_image_provider").select("flux2-dev").run()
        self.assertEqual(app.selectbox(key="page2_image_aspect_ratio_flux2-dev").value, "16:9")
        self.assertIn("跟随参考图", app.selectbox(key="page2_image_aspect_ratio_flux2-dev").options)
        self.assertEqual(len(image_aspect_ratios("seedream-4.5")), 6)
        self.assertEqual(len(app.exception), 0)

    def test_removed_or_invalid_saved_model_falls_back_to_default(self):
        store = SettingsStore(self.path)
        store.set(AppStorageKeys.PAGE2_LAST_IMAGE_PROVIDER, "removed-model")
        app = self.app()
        self.assertEqual(app.selectbox(key="_page2_image_provider").value, DEFAULT_IMAGE_PROVIDER)
        self.assertEqual(len(app.exception), 0)


if __name__ == "__main__":
    unittest.main()
