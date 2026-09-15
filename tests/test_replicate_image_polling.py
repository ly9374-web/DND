import json
import unittest
from unittest.mock import Mock, patch

import requests

from app.api import media_clients
from app.services import page2_service


def response(data, status=200, headers=None):
    return Mock(status_code=status, text=json.dumps(data), headers=headers or {},
                json=Mock(return_value=data))


class ReplicateImagePollingTests(unittest.TestCase):
    def setUp(self):
        self.client = media_clients.ReplicateImageAPIClient
        self.clock = 0
        self.starting = {"id": "test-task", "status": "starting", "output": None}
        self.ready = {"id": "test-task", "status": "succeeded",
                      "output": ["https://example.com/image.png"]}
        for target, kwargs in (
            ("debug_log", {}),
            ("ReplicateConfig.api_token", {"return_value": "test-token"}),
            ("time.monotonic", {"side_effect": lambda: self.clock}),
            ("time.sleep", {"side_effect": self.advance}),
        ):
            patcher = patch("app.api.media_clients." + target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def advance(self, seconds):
        self.clock += seconds

    def test_all_image_providers_poll_accepted_tasks_without_resubmitting(self):
        for provider in (
            "nano",
            "nanoPro",
            "flux",
            media_clients.ANILLUSTRIOUS_PROVIDER,
            media_clients.WAI_ILLUSTRIOUS_PROVIDER,
        ):
            with (
                self.subTest(provider=provider),
                patch.object(media_clients.requests, "post",
                             return_value=response(self.starting, 202)) as post,
                patch.object(media_clients.requests, "get", side_effect=[
                    response({**self.starting, "status": "processing"}),
                    response(self.ready),
                ]) as get,
            ):
                result = self.client.generate_image(provider, "普通风景")
                self.assertEqual(result.image_url, self.ready["output"][0])
                post.assert_called_once()
                self.assertEqual(get.call_count, 2)
                for call in get.call_args_list:
                    self.assertEqual(call.args[0],
                                     "https://api.replicate.com/v1/predictions/test-task")
                    self.assertEqual(call.kwargs["headers"],
                                     {"Authorization": "Bearer test-token"})

    def test_anillustrious_uses_pinned_community_version_and_expected_inputs(self):
        expected_dimensions = {
            "3:4": (768, 1024),
            "4:3": (1024, 768),
            "1:1": (1024, 1024),
        }
        for aspect_ratio, (width, height) in expected_dimensions.items():
            with (
                self.subTest(aspect_ratio=aspect_ratio),
                patch.object(media_clients.requests, "post",
                             return_value=response(self.ready)) as post,
            ):
                result = self.client.generate_image(
                    media_clients.ANILLUSTRIOUS_PROVIDER,
                    "from above,street\n1girl",
                    aspect_ratio=aspect_ratio,
                )

                self.assertEqual(result.image_url, self.ready["output"][0])
                post.assert_called_once_with(
                    "https://api.replicate.com/v1/predictions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer test-token",
                        "Prefer": "wait",
                    },
                    json={
                        "version": media_clients.ANILLUSTRIOUS_VERSION,
                        "input": {
                            "steps": 40,
                            "prompt": "from above,street\n1girl",
                            "width": width,
                            "height": height,
                            "scheduler": "DPM++ 2M SDE Karras",
                            "refiner": False,
                            "refiner_strength": 0.4,
                            "prompt_conjunction": True,
                        },
                    },
                    timeout=(15, 90),
                )

    def test_anillustrious_rejects_reference_images_before_submitting(self):
        with patch.object(media_clients.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "仅支持文生图"):
                self.client.generate_image(
                    media_clients.ANILLUSTRIOUS_PROVIDER,
                    "普通风景",
                    image_urls=["https://example.com/reference.png"],
                )
            post.assert_not_called()

    def test_wai_illustrious_uses_pinned_version_and_expected_inputs(self):
        expected_dimensions = {
            "3:4": (768, 1024),
            "4:3": (1024, 768),
            "1:1": (1024, 1024),
        }
        for aspect_ratio, (width, height) in expected_dimensions.items():
            with (
                self.subTest(aspect_ratio=aspect_ratio),
                patch.object(
                    media_clients.requests, "post", return_value=response(self.ready)
                ) as post,
            ):
                result = self.client.generate_image(
                    media_clients.WAI_ILLUSTRIOUS_PROVIDER,
                    "from above, black hair girl",
                    aspect_ratio=aspect_ratio,
                )

                self.assertEqual(result.image_url, self.ready["output"][0])
                post.assert_called_once_with(
                    "https://api.replicate.com/v1/predictions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer test-token",
                        "Prefer": "wait",
                    },
                    json={
                        "version": media_clients.WAI_ILLUSTRIOUS_VERSION,
                        "input": {
                            "batch_size": 1,
                            "cfg_scale": 5.86,
                            "clip_skip": 2,
                            "guidance_rescale": 1,
                            "height": height,
                            "model": "WAI-NSFW-Illustrious-SDXL-v12",
                            "pag_scale": 0,
                            "prepend_preprompt": True,
                            "prompt": "from above, black hair girl",
                            "scheduler": "DPM++ 2M SDE Karras",
                            "seed": -1,
                            "steps": 40,
                            "vae": "WAI-NSFW-Illustrious-SDXL-v12",
                            "width": width,
                        },
                    },
                    timeout=(15, 90),
                )

    def test_wai_illustrious_rejects_reference_images_before_submitting(self):
        with patch.object(media_clients.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "仅支持文生图"):
                self.client.generate_image(
                    media_clients.WAI_ILLUSTRIOUS_PROVIDER,
                    "1girl",
                    image_urls=["https://example.com/reference.png"],
                )
            post.assert_not_called()

    def test_anillustrious_enables_refiner_only_for_nonempty_prompt(self):
        for refiner_prompt, enabled, expected_prompt in (
            ("", False, None),
            ("   ", False, None),
            ("correct hands\nand details", True, "correct hands\nand details"),
        ):
            with (
                self.subTest(refiner_prompt=refiner_prompt),
                patch.object(
                    media_clients.requests, "post", return_value=response(self.ready)
                ) as post,
            ):
                self.client.generate_image(
                    media_clients.ANILLUSTRIOUS_PROVIDER,
                    "1girl",
                    refiner_prompt=refiner_prompt,
                )
                request_input = post.call_args.kwargs["json"]["input"]
                self.assertEqual(request_input["refiner"], enabled)
                self.assertEqual(request_input["refiner_strength"], 0.4)
                if expected_prompt is None:
                    self.assertNotIn("refiner_prompt", request_input)
                else:
                    self.assertEqual(
                        request_input["refiner_prompt"], expected_prompt
                    )

    def test_all_replicate_providers_retry_terminal_no_image_results_three_times(self):
        failed = {
            "id": "failed-task",
            "status": "failed",
            "output": None,
            "error": "GPU worker failed",
        }
        for provider in (
            "nano",
            "nanoPro",
            "flux",
            media_clients.ANILLUSTRIOUS_PROVIDER,
            media_clients.WAI_ILLUSTRIOUS_PROVIDER,
        ):
            with (
                self.subTest(provider=provider),
                patch.object(
                    media_clients.requests,
                    "post",
                    side_effect=[response(failed), response(self.ready)],
                ) as post,
            ):
                result = self.client.generate_image(provider, "1girl")
                self.assertEqual(result.image_url, self.ready["output"][0])
                self.assertEqual(post.call_count, 2)

        with patch.object(
            media_clients.requests, "post", return_value=response(failed)
        ) as post:
            with self.assertRaisesRegex(RuntimeError, "连续 3 次.*GPU worker failed"):
                self.client.generate_image(
                    media_clients.ANILLUSTRIOUS_PROVIDER, "1girl"
                )
        self.assertEqual(post.call_count, 3)

    def test_anillustrious_retries_success_without_output(self):
        empty = {"id": "empty-task", "status": "succeeded", "output": None}
        with patch.object(
            media_clients.requests,
            "post",
            side_effect=[response(empty), response(self.ready)],
        ) as post:
            result = self.client.generate_image(
                media_clients.ANILLUSTRIOUS_PROVIDER, "1girl"
            )
        self.assertEqual(result.image_url, self.ready["output"][0])
        self.assertEqual(post.call_count, 2)

    def test_anillustrious_content_policy_failure_is_resubmitted(self):
        rejected = {
            "id": "policy-task",
            "status": "failed",
            "output": None,
            "error": (
                "Prediction failed. E8412: We detected content that potentially "
                "violates our terms of service."
            ),
        }
        with patch.object(
            media_clients.requests,
            "post",
            side_effect=[response(rejected), response(self.ready)],
        ) as post:
            result = self.client.generate_image(
                media_clients.ANILLUSTRIOUS_PROVIDER, "1girl"
            )
        self.assertEqual(result.image_url, self.ready["output"][0])
        self.assertEqual(post.call_count, 2)

        with patch.object(
            media_clients.requests,
            "post",
            return_value=response(rejected),
        ) as post:
            with self.assertRaisesRegex(RuntimeError, "连续 3 次.*E8412"):
                self.client.generate_image(
                    media_clients.ANILLUSTRIOUS_PROVIDER, "1girl"
                )
        self.assertEqual(post.call_count, 3)

    def test_grok_retries_explicit_non_policy_failures_three_times(self):
        ready = {"data": [{"url": "https://example.com/grok.png"}]}
        with (
            patch.object(media_clients.XAIConfig, "image_api_key", return_value="key"),
            patch.object(
                media_clients.requests,
                "post",
                side_effect=[response({}, 500), response(ready)],
            ) as post,
        ):
            result = media_clients.GrokImageAPIClient.generate_image("landscape")
        self.assertEqual(result.image_url, "https://example.com/grok.png")
        self.assertEqual(post.call_count, 2)

        with (
            patch.object(media_clients.XAIConfig, "image_api_key", return_value="key"),
            patch.object(
                media_clients.requests, "post", return_value=response({}, 500)
            ) as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "连续 3 次.*HTTP 500"):
                media_clients.GrokImageAPIClient.generate_image("landscape")
        self.assertEqual(post.call_count, 3)

    def test_grok_content_moderation_failure_is_resubmitted(self):
        moderated = {
            "code": "imagine:content-moderated",
            "error": "Generated image rejected by content moderation.",
        }
        with (
            patch.object(media_clients.XAIConfig, "image_api_key", return_value="key"),
            patch.object(
                media_clients.requests,
                "post",
                side_effect=[
                    response(moderated, 400),
                    response({"data": [{"url": "https://example.com/grok.png"}]}),
                ],
            ) as post,
        ):
            result = media_clients.GrokImageAPIClient.generate_image("landscape")
        self.assertEqual(result.image_url, "https://example.com/grok.png")
        self.assertEqual(post.call_count, 2)

        with (
            patch.object(media_clients.XAIConfig, "image_api_key", return_value="key"),
            patch.object(
                media_clients.requests,
                "post",
                return_value=response(moderated, 400),
            ) as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "连续 3 次.*content-moderated"):
                media_clients.GrokImageAPIClient.generate_image("landscape")
        self.assertEqual(post.call_count, 3)

    def test_page2_service_routes_and_records_anillustrious_provider(self):
        generated = media_clients.GeneratedImageResult(
            image_data_base64="dGVzdA==",
        )
        with patch.object(
            page2_service.ReplicateImageAPIClient,
            "generate_image",
            return_value=generated,
        ) as generate:
            record = page2_service.generate_image(
                provider=media_clients.ANILLUSTRIOUS_PROVIDER,
                prompt="1girl, purple eyes",
                aspect_ratio="3:4",
            )

        generate.assert_called_once_with(
            provider=media_clients.ANILLUSTRIOUS_PROVIDER,
            prompt="1girl, purple eyes",
            image_urls=[],
            aspect_ratio="3:4",
            refiner_prompt="",
        )
        self.assertEqual(record.provider, media_clients.ANILLUSTRIOUS_PROVIDER)
        self.assertEqual(record.image_data_base64, "dGVzdA==")

    def test_page2_service_keeps_url_images_url_only_by_default(self):
        generated = media_clients.GeneratedImageResult(
            image_url="https://example.com/generated.png",
        )
        with patch.object(
            page2_service.ReplicateImageAPIClient,
            "generate_image",
            return_value=generated,
        ):
            record = page2_service.generate_image(
                provider=media_clients.ANILLUSTRIOUS_PROVIDER,
                prompt="forest",
            )

        self.assertEqual(record.image_url_string, generated.image_url)
        self.assertIsNone(record.image_data_base64)

    def test_sync_outputs_need_no_polling_even_while_processing(self):
        for status in ("succeeded", "processing"):
            for output in (self.ready["output"], self.ready["output"][0]):
                with (
                    self.subTest(status=status, output=output),
                    patch.object(media_clients.requests, "post", return_value=response(
                        {**self.ready, "status": status, "output": output})) as post,
                    patch.object(media_clients.requests, "get") as get,
                ):
                    self.assertEqual(self.client.generate_image("nanoPro", "风景").image_url,
                                     self.ready["output"][0])
                    post.assert_called_once()
                    get.assert_not_called()

    def test_terminal_failures_take_precedence_over_partial_outputs(self):
        for status in ("failed", "canceled", "aborted"):
            with self.subTest(status=status), patch.object(media_clients.requests, "get") as get:
                with self.assertRaisesRegex(RuntimeError, "provider reason.*test-task"):
                    self.client._wait_for_image(
                        {**self.ready, "status": status, "error": "provider reason"}, "token")
                get.assert_not_called()

    def test_failure_after_starting_surfaces_provider_reason(self):
        with patch.object(media_clients.requests, "get", return_value=response(
            {**self.starting, "status": "failed", "error": "Generation failed"}
        )):
            with self.assertRaisesRegex(RuntimeError, "Generation failed.*test-task"):
                self.client._wait_for_image(self.starting, "token")

    def test_completed_task_without_image_is_an_explicit_error(self):
        with self.assertRaisesRegex(RuntimeError, "未返回可用图片.*test-task"):
            self.client._wait_for_image({**self.ready, "output": None}, "token")

    def test_transient_query_errors_retry_only_get(self):
        with (
            patch.object(media_clients.requests, "post",
                         return_value=response(self.starting, 202)) as post,
            patch.object(media_clients.requests, "get", side_effect=[
                requests.ConnectionError("network down"),
                requests.Timeout("timeout"),
                response({}, 429, {"Retry-After": "12"}),
                response({}, 503),
                response(self.ready),
            ]) as get,
        ):
            self.assertEqual(self.client.generate_image("nano", "风景").image_url,
                             self.ready["output"][0])
            post.assert_called_once()
            self.assertEqual(get.call_count, 5)

    def test_persistent_query_failure_is_bounded_and_keeps_task_link(self):
        with patch.object(media_clients.requests, "get",
                          side_effect=requests.Timeout()) as get:
            with self.assertRaisesRegex(RuntimeError, "连续查询失败.*https://replicate.com/p/test-task"):
                self.client._wait_for_image(self.starting, "token")
            self.assertEqual(get.call_count, self.client.MAX_POLL_ERRORS)

    def test_auth_failure_is_not_retried(self):
        with patch.object(media_clients.requests, "get", return_value=response({}, 401)) as get:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401.*test-task"):
                self.client._wait_for_image(self.starting, "token")
            get.assert_called_once()

    def test_total_wait_deadline_stops_pending_tasks(self):
        with (
            patch.object(self.client, "POLL_TIMEOUT_SECONDS", 7),
            patch.object(media_clients.requests, "get", return_value=response(self.starting)) as get,
        ):
            with self.assertRaisesRegex(TimeoutError, "超时.*starting.*test-task"):
                self.client._wait_for_image(self.starting, "token")
            self.assertEqual(self.clock, 7)
            self.assertEqual(get.call_count, 2)

    def test_pending_response_without_id_fails_without_network_call(self):
        with patch.object(media_clients.requests, "get") as get:
            with self.assertRaisesRegex(RuntimeError, "缺少任务 ID"):
                self.client._wait_for_image({"status": "starting"}, "token")
            get.assert_not_called()

    def test_create_timeout_is_not_retried(self):
        with (
            patch.object(media_clients.requests, "post", side_effect=requests.Timeout()) as post,
            patch.object(media_clients.requests, "get") as get,
        ):
            with self.assertRaisesRegex(RuntimeError, "无法确认任务是否已创建"):
                self.client.generate_image("nano", "风景")
            post.assert_called_once()
            get.assert_not_called()

    def test_frontend_error_survives_refresh_until_dismissed(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string('''
import streamlit as st
from web.pages import page2
st.session_state.setdefault("page2_operation_error", "生图查询失败：test-task")
page2._render_operation_queue_status()
''').run()
        for _ in range(3):
            self.assertEqual(app.error[0].value, "生图查询失败：test-task")
            app.run()
        app.button(key="page2_dismiss_operation_error").click().run()
        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.exception), 0)
