import json
import unittest
from unittest.mock import Mock, patch

import requests

from app.api import media_clients


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
        for provider in ("nano", "nanoPro", "flux"):
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
