import unittest
from unittest.mock import patch

from app.api.chat_clients import OpenRouterChatAPIClient
from app.models import (
    CHAT_MODEL_LABELS,
    CHAT_MODEL_OPTIONS,
    chat_model_supports_reasoning,
    openrouter_api_model_for_chat_model,
)
from app.services import page2_service, prompt_assets


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = str(data)

    def json(self):
        return self._data


class OpenRouterChatTests(unittest.TestCase):
    MODEL_OPTION = "mistralLarge2512"
    API_MODEL = "mistralai/mistral-large-2512"

    def test_model_metadata(self):
        self.assertIn(self.MODEL_OPTION, CHAT_MODEL_OPTIONS)
        self.assertEqual(
            CHAT_MODEL_LABELS[self.MODEL_OPTION],
            "Mistral Large 3 2512",
        )
        self.assertEqual(
            openrouter_api_model_for_chat_model(self.MODEL_OPTION),
            self.API_MODEL,
        )
        self.assertFalse(chat_model_supports_reasoning(self.MODEL_OPTION))

    @patch("app.api.chat_clients.OpenRouterConfig.api_key", return_value="router-key")
    @patch("app.api.chat_clients.post_with_network_retry")
    def test_request_reuses_openrouter_key_and_sends_supported_text_parameters(
        self,
        post,
        _api_key,
    ):
        post.return_value = _FakeResponse({
            "choices": [{
                "message": {"content": "  返回内容  "},
                "finish_reason": "stop",
            }]
        })

        result = OpenRouterChatAPIClient.send_message(
            system_prompt="系统设定",
            context_messages=[
                {"role": "user", "content": "上一问"},
                {"role": "assistant", "content": "上一答"},
            ],
            user_message="继续",
            temperature=0.6,
            model=self.API_MODEL,
            max_tokens=1234,
        )

        self.assertEqual(result, "返回内容")
        self.assertEqual(post.call_args.args[0], OpenRouterChatAPIClient.CHAT_URL)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"], {
            "Authorization": "Bearer router-key",
            "Content-Type": "application/json",
        })
        self.assertEqual(kwargs["json_body"], {
            "model": self.API_MODEL,
            "messages": [
                {"role": "system", "content": "系统设定"},
                {"role": "user", "content": "上一问"},
                {"role": "assistant", "content": "上一答"},
                {"role": "user", "content": "继续"},
            ],
            "temperature": 0.6,
            "stream": False,
            "max_tokens": 1234,
        })
        self.assertNotIn("reasoning", kwargs["json_body"])
        self.assertNotIn("reasoning_effort", kwargs["json_body"])
        self.assertNotIn("thinking", kwargs["json_body"])

    @patch("app.api.chat_clients.OpenRouterConfig.api_key", return_value="")
    def test_missing_key_has_clear_error(self, _api_key):
        with self.assertRaisesRegex(RuntimeError, "缺少 OpenRouter API Key"):
            OpenRouterChatAPIClient.send_message("system", [], "hello")

    @patch("app.api.chat_clients.OpenRouterConfig.api_key", return_value="router-key")
    @patch("app.api.chat_clients.post_with_network_retry")
    def test_openrouter_error_is_not_treated_as_empty_output(self, post, _api_key):
        post.return_value = _FakeResponse(
            {"error": {"message": "insufficient credits"}},
            status_code=402,
        )
        with self.assertRaisesRegex(RuntimeError, "insufficient credits"):
            OpenRouterChatAPIClient.send_message("system", [], "hello")

    @patch.object(page2_service.OpenRouterChatAPIClient, "send_message", return_value="ok")
    def test_page2_dispatches_mistral_to_openrouter_without_reasoning(self, send):
        ctx = page2_service.default_context()
        ctx.selected_chat_model = self.MODEL_OPTION
        ctx.chat_reasoning_enabled = True
        ctx.temperature = 0.45

        result = page2_service._send_with_current_chat_model(
            ctx=ctx,
            context_messages=[],
            user_message="hello",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(send.call_args.kwargs, {
            "system_prompt": ctx.system_prompt,
            "context_messages": [],
            "user_message": "hello",
            "temperature": 0.45,
            "model": self.API_MODEL,
        })

    @patch.object(prompt_assets.OpenRouterChatAPIClient, "send_message", return_value="ok")
    def test_prompt_generation_dispatches_mistral_to_openrouter(self, send):
        result = prompt_assets._send_generation(
            self.MODEL_OPTION,
            "system",
            "prompt",
            reasoning_effort="high",
            max_tokens=4321,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(send.call_args.kwargs, {
            "system_prompt": "system",
            "context_messages": [],
            "user_message": "prompt",
            "temperature": 0.8,
            "model": self.API_MODEL,
            "max_tokens": 4321,
        })


if __name__ == "__main__":
    unittest.main()
