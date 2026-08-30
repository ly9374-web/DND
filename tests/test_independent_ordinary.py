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


if __name__ == "__main__":
    unittest.main()
