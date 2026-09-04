import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="vs-ordinary-independent-test-"))
os.environ["VS_IMAGE_NOVEL_DATA_DIR"] = str(TEST_DATA_DIR)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import ChatRecord, Page2ConversationTurn
from app.services import chat_records, page2_service


class IndependentOrdinaryTests(unittest.TestCase):
    def test_long_story_brain_implementation_is_not_packaged(self):
        self.assertFalse((ROOT / "agent_extension").exists())
        self.assertFalse((ROOT / "graph_view.py").exists())
        self.assertFalse((ROOT / "story_brain.py").exists())
        self.assertFalse((ROOT / "web/components/story_brain_graph").exists())
        self.assertFalse((ROOT / ".python_app_data/AgentChatRecords").exists())

        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("pyvis", requirements)
        self.assertNotIn("networkx", requirements)
        self.assertNotIn("anthropic", requirements)

    def test_packaged_settings_have_no_agent_only_keys(self):
        settings_path = ROOT / ".python_app_data/user_defaults.json"
        if not settings_path.is_file():
            self.skipTest("private runtime settings are intentionally not packaged")
        values = json.loads(settings_path.read_text(encoding="utf-8"))
        forbidden_keys = {
            "agentActionDecisionHistoryTurns",
            "agentChatRecordIndex",
            "agentEvolutionRounds",
            "agentNPCHistoryTurns",
            "agentPlayerRouteHistoryTurns",
            "agentPromptRecordNextIndex",
            "agentPromptRecords",
            "agentSceneHistoryTurns",
            "agentSelectedChatModel",
            "agentStoryBrainTurns",
            "agentTemperature",
            "claudeApiKey",
            "guestAgentChatRecordIndex",
            "hiddenAgentPromptRecordNextIndex",
            "hiddenAgentPromptRecords",
            "page2StoryBrainMode",
            "selectedAgentPromptRecordID",
        }
        self.assertFalse(forbidden_keys.intersection(values))

    def test_every_packaged_chat_file_is_visible_in_the_index(self):
        settings_path = ROOT / ".python_app_data/user_defaults.json"
        if not settings_path.is_file():
            self.skipTest("private chat records are intentionally not packaged")
        values = json.loads(settings_path.read_text(encoding="utf-8"))
        index = json.loads(values.get("chatRecordIndex") or "[]")
        indexed_files = {str(item.get("file_name") or "") for item in index}
        record_files = {
            path.name
            for path in (ROOT / ".python_app_data/ChatRecords").glob("record-*.json")
        }
        self.assertEqual(indexed_files, record_files)

    def test_context_has_only_short_story_brain_mode(self):
        ctx = page2_service.default_context()
        self.assertFalse(hasattr(ctx, "story_brain_mode"))
        self.assertFalse(hasattr(page2_service, "STORY_BRAIN_LONG"))
        self.assertFalse(hasattr(page2_service, "STORY_BRAIN_MODES"))

    def test_short_story_brain_is_injected_without_archive(self):
        ctx = page2_service.default_context()
        ctx.system_prompt = "原始 system"
        turns = [
            Page2ConversationTurn(
                user_message="旧用户消息",
                assistant_message="旧回复",
            )
        ]

        with patch.object(
            page2_service.GrokAPIClient,
            "send_message",
            return_value="新回复",
        ) as send_message:
            result = page2_service.send_message(
                ctx=ctx,
                turns=turns,
                user_message="打开门",
                story_brain_short="雨夜的旅店",
                story_brain_enabled=True,
            )

        self.assertEqual(result, "新回复")
        kwargs = send_message.call_args.kwargs
        self.assertEqual(kwargs["system_prompt"], "原始 system")
        self.assertEqual(
            kwargs["user_message"],
            "<当前故事背景>雨夜的旅店</当前故事背景>\n\n打开门",
        )
        self.assertNotIn("characters", str(kwargs))
        self.assertNotIn("Memory Pack", str(kwargs))

    def test_archived_long_story_brain_round_trips_unchanged(self):
        archive = {
            "characters": [{"id": "char-1", "name": "旧角色", "secret": "原样保留"}],
            "relationships": [{"from": "旧角色", "to": "另一角色"}],
            "events": [{"type": "伏笔", "trigger": "旧触发条件"}],
            "unknown_future_field": {"nested": [1, 2, 3]},
        }
        record = ChatRecord.from_dict(
            {
                "id": "archive-round-trip",
                "title": "旧记录",
                "turns": [{"user_message": "继续", "assistant_message": "好的"}],
                "system_prompt": "系统设定",
                "generated_images": [],
                "story_brain": archive,
                "story_brain_short": "短 Story Brain",
            }
        )

        self.assertEqual(record.to_dict()["story_brain"], archive)

        saved_id = page2_service.upsert_chat_record(
            record_id=record.id,
            turns=record.turns,
            generated_media=record.generated_images,
            system_prompt=record.system_prompt,
            story_brain=record.story_brain,
            story_brain_short=record.story_brain_short,
        )
        loaded = chat_records.load_record_by_id(saved_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.story_brain, archive)
        self.assertEqual(loaded.story_brain_short, "短 Story Brain")

    def test_hidden_user_message_round_trips_and_stays_in_model_context(self):
        turn = Page2ConversationTurn(
            user_message="继续发展",
            assistant_message="故事继续",
            is_user_message_hidden=True,
        )

        restored = Page2ConversationTurn.from_dict(turn.to_dict())
        self.assertTrue(restored.is_user_message_hidden)
        self.assertEqual(
            page2_service.build_context_messages([restored], 1),
            [
                {"role": "user", "content": "继续发展"},
                {"role": "assistant", "content": "故事继续"},
            ],
        )

    def test_hidden_continue_message_is_not_used_as_record_title(self):
        turns = [
            Page2ConversationTurn(
                user_message="继续发展",
                assistant_message="雨幕中出现了一道门。",
                is_user_message_hidden=True,
            )
        ]

        self.assertEqual(
            page2_service.make_record_title(turns),
            "雨幕中出现了一道门。",
        )

    def test_auto_dice_roll_requires_exact_reply_ending(self):
        self.assertTrue(page2_service.should_auto_roll_dice("可以开始掷骰子。"))
        self.assertTrue(
            page2_service.should_auto_roll_dice(
                "##【行动判定】\n0—12：失败\n13—24：成功\n可以开始掷骰子。\n\n"
            )
        )
        self.assertFalse(
            page2_service.should_auto_roll_dice(
                "可以开始掷骰子。\n>当前时间：7月20日 17:25"
            )
        )
        self.assertFalse(
            page2_service.should_auto_roll_dice("正文提到可以开始掷骰子。随后继续。")
        )
        self.assertFalse(page2_service.should_auto_roll_dice("请求失败，请稍后重试。"))

    def test_dice_decision_marker_is_hidden_only_at_assistant_message_start(self):
        from web.pages import page2

        self.assertEqual(
            page2._assistant_message_for_display("本轮不掷骰子\n\n雨还在下。"),
            "雨还在下。",
        )
        self.assertEqual(
            page2._assistant_message_for_display("本轮掷骰子：\n##【行动判定】"),
            "##【行动判定】",
        )
        self.assertEqual(
            page2._assistant_message_for_display("正文提到本轮不掷骰子。"),
            "正文提到本轮不掷骰子。",
        )

    def test_completed_chat_queues_visible_auto_dice_followup(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        ctx = page2_service.default_context()
        turn = Page2ConversationTurn(
            user_message="尝试潜行",
            assistant_message=None,
            is_loading=True,
        )
        task = page2._Page2OperationTask(
            kind="chat",
            label="发送消息",
            scope_id="test-scope",
        )
        task.result = "##【行动判定】\n0—12：失败\n13—24：成功\n可以开始掷骰子。"
        task.meta = {
            "turn_id": turn.id,
            "ctx": ctx,
            "story_brain_enabled": False,
        }
        session_state = SessionState(
            page2_turns=[turn],
            page2_operation_queue=[],
            page2_active_operation=task,
            page2_operation_scope_id="test-scope",
            page2_story_brain_retry_pending=False,
        )

        with (
            patch.object(page2.st, "session_state", session_state),
            patch.object(page2, "_upsert_record"),
            patch.object(page2.page2_service, "roll_point", return_value=17),
        ):
            page2._apply_chat_operation(task)

        self.assertEqual(turn.assistant_message, task.result)
        self.assertFalse(turn.is_loading)
        self.assertEqual(len(session_state["page2_operation_queue"]), 1)
        followup = session_state["page2_operation_queue"][0]
        self.assertEqual(followup.kind, "chat")
        self.assertEqual(followup.label, "自动掷骰并发送")
        self.assertEqual(followup.payload["user_text"], "掷骰结果：“17”")
        self.assertFalse(followup.payload["hide_user_message"])
        self.assertTrue(followup.payload["is_auto_dice_followup"])

    def test_auto_dice_followup_does_not_recursively_roll(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        ctx = page2_service.default_context()
        turn = Page2ConversationTurn(
            user_message="掷骰结果：“17”",
            assistant_message=None,
            is_loading=True,
        )
        task = page2._Page2OperationTask(
            kind="chat",
            label="自动掷骰并发送",
            scope_id="test-scope",
        )
        task.result = "结算后又出现判定。\n可以开始掷骰子。"
        task.meta = {
            "turn_id": turn.id,
            "ctx": ctx,
            "story_brain_enabled": False,
            "is_auto_dice_followup": True,
        }
        session_state = SessionState(
            page2_turns=[turn],
            page2_operation_queue=[],
            page2_active_operation=task,
            page2_operation_scope_id="test-scope",
            page2_story_brain_retry_pending=False,
        )

        with (
            patch.object(page2.st, "session_state", session_state),
            patch.object(page2, "_upsert_record"),
        ):
            page2._apply_chat_operation(task)

        self.assertEqual(session_state["page2_operation_queue"], [])


if __name__ == "__main__":
    unittest.main()
