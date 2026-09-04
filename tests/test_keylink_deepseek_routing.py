import unittest
from unittest.mock import patch

from app.api.chat_clients import DeepSeekAPIClient


class _FakeResponse:
    status_code = 200
    text = '{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'

    def json(self):
        return {
            "choices": [
                {
                    "message": {"content": "ok"},
                    "finish_reason": "stop",
                }
            ]
        }


class KeyLinkDeepSeekRoutingTests(unittest.TestCase):
    def _send(self, model="deepseek-v4-pro"):
        return DeepSeekAPIClient.send_message(
            system_prompt="system",
            context_messages=[],
            user_message="hello",
            temperature=0.7,
            model=model,
            thinking_enabled=True,
            reasoning_effort="high",
            max_tokens=50000,
        )

    @patch("app.api.chat_clients.requests.post", return_value=_FakeResponse())
    @patch("app.api.chat_clients.DeepSeekConfig.api_key", return_value="deepseek-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.api_key", return_value="keylink-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.enabled", return_value=True)
    def test_pro_uses_keylink_with_minimal_body_when_enabled_and_key_exists(
        self,
        _enabled,
        _keylink_key,
        deepseek_key,
        post,
    ):
        self.assertEqual(self._send(), "ok")

        deepseek_key.assert_not_called()
        _, kwargs = post.call_args
        self.assertEqual(
            post.call_args.args[0],
            "https://keylinkclub.com/v1/chat/completions",
        )
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer keylink-key")
        self.assertEqual(
            kwargs["json"],
            {
                "model": "deepseek-v4-pro",
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "hello"},
                ],
                "temperature": 0.7,
            },
        )

    @patch("app.api.chat_clients.requests.post", return_value=_FakeResponse())
    @patch("app.api.chat_clients.DeepSeekConfig.api_key", return_value="deepseek-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.api_key", return_value="keylink-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.enabled", return_value=False)
    def test_pro_uses_original_deepseek_when_switch_is_off(
        self,
        _enabled,
        keylink_key,
        _deepseek_key,
        post,
    ):
        self.assertEqual(self._send(), "ok")

        keylink_key.assert_not_called()
        self.assertEqual(post.call_args.args[0], DeepSeekAPIClient.CHAT_URL)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer deepseek-key",
        )

    @patch("app.api.chat_clients.requests.post", return_value=_FakeResponse())
    @patch("app.api.chat_clients.DeepSeekConfig.api_key", return_value="deepseek-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.api_key", return_value="")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.enabled", return_value=True)
    def test_pro_uses_original_deepseek_when_keylink_key_is_empty(
        self,
        _enabled,
        _keylink_key,
        _deepseek_key,
        post,
    ):
        self.assertEqual(self._send(), "ok")
        self.assertEqual(post.call_args.args[0], DeepSeekAPIClient.CHAT_URL)

    @patch("app.api.chat_clients.requests.post", return_value=_FakeResponse())
    @patch("app.api.chat_clients.DeepSeekConfig.api_key", return_value="deepseek-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.api_key", return_value="keylink-key")
    @patch("app.api.chat_clients.KeyLinkDeepSeekV4ProConfig.enabled", return_value=True)
    def test_flash_always_uses_original_deepseek(
        self,
        enabled,
        keylink_key,
        _deepseek_key,
        post,
    ):
        self.assertEqual(self._send(model="deepseek-v4-flash"), "ok")

        enabled.assert_not_called()
        keylink_key.assert_not_called()
        self.assertEqual(post.call_args.args[0], DeepSeekAPIClient.CHAT_URL)


if __name__ == "__main__":
    unittest.main()
