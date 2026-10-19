"""The opt-in log records the actual chat request without model output."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from app import config, log_store
from app.models import Page2ConversationTurn
from app.services import page2_service


class ChatRequestLoggingTests(unittest.TestCase):
    def test_full_sent_body_parameters_and_duration_without_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            response = Mock(status_code=200, text="MODEL_REPLY_SHOULD_NOT_BE_LOGGED")
            response.json.return_value = {
                "choices": [{"message": {"content": "MODEL_REPLY_SHOULD_NOT_BE_LOGGED"}}]
            }
            ctx = page2_service.default_context()
            ctx.selected_chat_model = "mistralLarge2512"
            ctx.temperature = 0.43
            ctx.system_prompt = "系统规则：" + "长内容" * 11000
            ctx.context_turn_count = 2
            turns = [Page2ConversationTurn(user_message="前一轮问题", assistant_message="前一轮回复")]

            terminal = io.StringIO()
            with patch.object(config, "DATA_DIR", data_dir), \
                 patch.object(config, "settings", Mock(load=Mock(), bool=Mock(return_value=True))), \
                 patch.object(log_store, "start_maintenance"), \
                 patch("app.api.chat_clients.OpenRouterConfig.api_key", return_value="private-key"), \
                 patch("app.api.chat_clients.requests.post", return_value=response) as post, \
                 redirect_stdout(terminal):
                reply = page2_service.send_message(
                    ctx=ctx, turns=turns, user_message="这一轮问题",
                    story_brain_short="当前记忆", story_brain_enabled=True,
                )

            self.assertEqual(reply, "MODEL_REPLY_SHOULD_NOT_BE_LOGGED")
            events = [json.loads(line) for path in (data_dir / "logs").glob("chat-*.jsonl")
                      for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["model_request", "model_duration"])
            request, duration = events
            self.assertEqual(request["request_id"], duration["request_id"])
            self.assertEqual(request["label"], "聊天")
            self.assertEqual(request["body"], post.call_args.kwargs["json"])
            self.assertEqual(request["body"]["messages"][0],
                             {"role": "system", "content": ctx.system_prompt})
            self.assertEqual(request["body"]["messages"][1:3], [
                {"role": "user", "content": "前一轮问题"},
                {"role": "assistant", "content": "前一轮回复"},
            ])
            self.assertIn("这一轮问题", request["body"]["messages"][-1]["content"])
            self.assertIn("当前记忆", request["body"]["messages"][-1]["content"])
            self.assertEqual(request["body"]["temperature"], 0.43)
            self.assertEqual(request["body"]["model"], "mistralai/mistral-large-2512")
            self.assertEqual(duration["status"], "完成")
            self.assertGreaterEqual(duration["elapsed_seconds"], 0)
            combined = terminal.getvalue() + json.dumps(events, ensure_ascii=False)
            self.assertNotIn("MODEL_REPLY_SHOULD_NOT_BE_LOGGED", combined)
            self.assertNotIn("private-key", combined)

    def test_disabled_log_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            response = Mock(status_code=200, text="private model reply")
            response.json.return_value = {"choices": [{"message": {"content": "private model reply"}}]}
            ctx = page2_service.default_context()
            ctx.selected_chat_model = "mistralLarge2512"
            terminal = io.StringIO()
            with patch.object(config, "DATA_DIR", data_dir), \
                 patch.object(config, "settings", Mock(load=Mock(), bool=Mock(return_value=False))), \
                 patch("app.api.chat_clients.OpenRouterConfig.api_key", return_value="private-key"), \
                 patch("app.api.chat_clients.requests.post", return_value=response), \
                 redirect_stdout(terminal):
                page2_service.send_message(
                    ctx=ctx, turns=[], user_message="private input", story_brain_enabled=False,
                )
            self.assertFalse(terminal.getvalue())
            self.assertFalse((data_dir / "logs").exists())


if __name__ == "__main__":
    unittest.main()
