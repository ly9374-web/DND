import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import prompt_drafts


class PromptDraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "PromptDrafts" / "drafts.json"
        self.patcher = patch.object(prompt_drafts, "DRAFTS_PATH", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_drafts_are_scoped_and_round_trip_empty_values(self):
        prompt_drafts.apply_browser_snapshot(
            "user:1:ordinary",
            [
                {
                    "widget_key": "system_prompt_body_one",
                    "value": "编辑后的 prompt",
                    "baseline": "原 prompt",
                    "label": "最终设定 prompt",
                    "accepted": False,
                    "updated_at": 123,
                },
                {
                    "widget_key": "system_prompt_body_empty",
                    "value": "",
                    "baseline": "原 prompt",
                    "label": "清空的 prompt",
                },
            ],
        )
        prompt_drafts.apply_browser_snapshot(
            "user:1:hidden",
            [{"widget_key": "system_prompt_body_one", "value": "隐藏草稿"}],
        )

        ordinary = prompt_drafts.scoped_entries("user:1:ordinary")
        hidden = prompt_drafts.scoped_entries("user:1:hidden")
        self.assertEqual(ordinary["system_prompt_body_one"]["value"], "编辑后的 prompt")
        self.assertEqual(ordinary["system_prompt_body_empty"]["value"], "")
        self.assertEqual(hidden["system_prompt_body_one"]["value"], "隐藏草稿")

    def test_clear_only_removes_explicit_widget_or_scope(self):
        scope = "user:1:ordinary"
        prompt_drafts.apply_browser_snapshot(
            scope,
            [
                {"widget_key": "one", "value": "1"},
                {"widget_key": "two", "value": "2"},
            ],
        )
        prompt_drafts.apply_browser_snapshot(scope, [], ["one"])
        self.assertEqual(set(prompt_drafts.scoped_entries(scope)), {"two"})

        prompt_drafts.apply_browser_snapshot(
            "user:1:hidden", [{"widget_key": "private", "value": "secret"}]
        )
        prompt_drafts.clear_scope(scope)
        self.assertEqual(prompt_drafts.scoped_entries(scope), {})
        self.assertIn("private", prompt_drafts.scoped_entries("user:1:hidden"))

    def test_clear_wins_over_stale_draft_in_same_snapshot(self):
        prompt_drafts.apply_browser_snapshot("scope", [{"widget_key": "one", "value": "old"}])
        prompt_drafts.apply_browser_snapshot(
            "scope", [{"widget_key": "one", "value": "old"}], ["one"]
        )
        self.assertEqual(prompt_drafts.scoped_entries("scope"), {})

    def test_atomic_store_keeps_last_good_backup(self):
        prompt_drafts.apply_browser_snapshot(
            "scope", [{"widget_key": "one", "value": "first"}]
        )
        prompt_drafts.apply_browser_snapshot(
            "scope", [{"widget_key": "one", "value": "second"}]
        )
        backup = self.path.with_name("drafts.last-good.json")
        self.assertTrue(backup.exists())
        self.assertIn("first", backup.read_text(encoding="utf-8"))

        self.path.write_text("{broken", encoding="utf-8")
        recovered = prompt_drafts.scoped_entries("scope")
        self.assertEqual(recovered["one"]["value"], "first")
        self.assertTrue(list(self.path.parent.glob("drafts.json.corrupt-*")))


class PromptDraftBrowserBridgeTests(unittest.TestCase):
    def test_stale_scope_and_page_snapshots_are_ignored(self):
        from web import prompt_draft_guard as guard
        for event in [
            {"scope": "user:other:ordinary", "page": "settings:世界书"},
            {"scope": "user:1:ordinary", "page": "settings:角色卡"},
        ]:
            state = {"user_id": "1", "auth_mode": "user", "nav_page": "settings",
                     "ordinary_prompt_section": "世界书"}
            with patch.object(guard.st, "session_state", state), \
                 patch.object(guard, "prompt_draft_guard_component", return_value={
                     "event": "snapshot", "revision": "stale", "drafts": [], **event,
                 }), patch.object(prompt_drafts, "scoped_entries", return_value={}), \
                 patch.object(prompt_drafts, "apply_browser_snapshot") as apply:
                guard.render()
                apply.assert_not_called()

    def test_success_acknowledgement_clears_only_saved_fields(self):
        from web import prompt_draft_guard as guard
        state = {"user_id": "1", "auth_mode": "user", "body": "saved text"}
        with patch.object(guard.st, "session_state", state), \
             patch.object(prompt_drafts, "apply_browser_snapshot") as apply:
            guard.saved("body")
            apply.assert_called_once_with("user:1:ordinary", [], ["body"])
            ack = state["_prompt_draft_saved"]["user:1:ordinary:body"]
            self.assertEqual(ack["value"], "saved text")
            self.assertTrue(ack["revision"])


if __name__ == "__main__":
    unittest.main()
