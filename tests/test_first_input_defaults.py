import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.default_prompts.first_inputs import (
    FIRST_INPUT_DEFAULTS_VERSION,
    OLD_PLAYER_FIRST_INPUT,
)
from app.models import SystemPromptRecord
from app.services import prompt_assets, system_prompts


class FirstInputDefaultsTests(unittest.TestCase):
    def test_new_final_settings_keep_original_selected_and_add_old_player(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            with patch.object(system_prompts, "settings", store):
                for hidden in (False, True):
                    state = system_prompts.PromptState(hidden, [], [], 1, 1, "")
                    system_prompts.save_prompt(
                        state, record_id="", title="新设定", prompt="系统正文",
                        first_input=prompt_assets.DEFAULT_FINAL_FIRST_INPUT,
                    )
                    record = system_prompts.get_record(state, state.selected_record_id)
                    self.assertEqual([i.title for i in record.first_inputs], ["默认首轮输入", "老玩家"])
                    self.assertEqual(record.first_inputs[1].content, OLD_PLAYER_FIRST_INPUT)
                    self.assertEqual(record.selected_first_input_id, record.first_inputs[0].id)
                    self.assertEqual(system_prompts.first_input_for_record(state, record.id), prompt_assets.DEFAULT_FINAL_FIRST_INPUT)
                    system_prompts.select_first_input(state, record.id, record.first_inputs[1].id)
                    self.assertEqual(system_prompts.first_input_for_record(state, record.id), OLD_PLAYER_FIRST_INPUT)

    def test_legacy_and_named_records_migrate_in_both_spaces_once(self):
        legacy = {"id": "normal", "title": "旧设定", "prompt": "原正文", "firstInput": "旧开场"}
        named = {
            "id": "hidden", "title": "隐藏设定", "prompt": "隐藏正文",
            "firstInputs": [{"id": "a", "title": "开场一", "content": "一"},
                            {"id": "b", "title": "开场二", "content": "二"}],
            "selectedFirstInputID": "b",
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.set_many_atomic({
                AppStorageKeys.SYSTEM_PROMPT_RECORDS: json.dumps([legacy]),
                AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS: json.dumps([named]),
                AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID: "normal",
                AppStorageKeys.SYSTEM_PROMPT: "原正文",
            })
            with (
                patch.object(system_prompts, "settings", store),
                patch.object(system_prompts, "seed_public_prompt_assets_if_needed"),
                patch.object(prompt_assets, "load_long_term_memories", return_value=[]),
                patch.object(prompt_assets, "default_long_term_memory_record", return_value=None),
            ):
                state = system_prompts.load_state()
                normal, hidden = state.records[0], state.hidden_records[0]
                self.assertEqual(normal.first_input, "旧开场")
                self.assertEqual(hidden.first_input, "二")
                self.assertEqual(hidden.selected_first_input_id, "b")
                self.assertEqual([normal.prompt, hidden.prompt], ["原正文", "隐藏正文"])
                self.assertEqual([len(normal.first_inputs), len(hidden.first_inputs)], [2, 3])
                for record in (normal, hidden):
                    self.assertEqual(record.first_inputs[-1].content, OLD_PLAYER_FIRST_INPUT)
                before = store.path.read_bytes()
                system_prompts.load_state()
                self.assertEqual(store.path.read_bytes(), before)

                # User edits/deletions persist across save and startup migration.
                system_prompts.update_first_input(
                    state, normal.id, normal.first_inputs[-1].id,
                    title="自定义老玩家", content="我改过的开场",
                )
                system_prompts.delete_first_input(state, hidden.id, hidden.first_inputs[-1].id)
                state = system_prompts.load_state()
                self.assertEqual(state.records[0].first_inputs[-1].content, "我改过的开场")
                self.assertEqual(len(state.hidden_records[0].first_inputs), 2)
                self.assertEqual(state.hidden_records[0].selected_first_input_id, "b")

    def test_existing_preset_is_not_duplicated_or_overwritten(self):
        for title, content in (("老玩家", "用户改写"), ("自定义名称", OLD_PLAYER_FIRST_INPUT)):
            with self.subTest(title=title):
                record = SystemPromptRecord.from_dict({
                    "title": "旧设定", "prompt": "正文",
                    "first_inputs": [{"id": "old", "title": title, "content": content}],
                    "selected_first_input_id": "old",
                })
                self.assertEqual(len(record.first_inputs), 1)
                self.assertEqual(record.first_inputs[0].content, content)
                self.assertTrue(record.first_inputs_need_migration)
                saved = record.to_dict()
                self.assertEqual(saved["first_input_defaults_version"], FIRST_INPUT_DEFAULTS_VERSION)
                self.assertEqual(SystemPromptRecord.from_dict(saved).to_dict(), saved)

    def test_bundled_defaults_contain_both_presets_without_duplicate_on_load(self):
        path = Path(__file__).resolve().parents[1] / "public_prompt_assets" / "ordinary.json"
        for source in json.loads(path.read_text())["final_settings"]:
            self.assertEqual(len(source["first_inputs"]), 2)
            record = SystemPromptRecord(first_inputs=source["first_inputs"], title=source["title"], prompt=source["prompt"])
            self.assertEqual(len(record.first_inputs), 2)
            self.assertEqual(record.first_inputs[-1].title, "老玩家")
            self.assertEqual(record.first_inputs[-1].content, OLD_PLAYER_FIRST_INPUT)


if __name__ == "__main__":
    unittest.main()
