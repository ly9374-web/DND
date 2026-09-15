"""Final-setting memory is chosen at creation, never backfilled on reload."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.services import prompt_assets, public_prompt_seed, system_prompts


class FinalSettingMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = SettingsStore(Path(temporary.name) / "settings.json")
        for module in (system_prompts, prompt_assets, public_prompt_seed):
            patcher = patch.object(module, "settings", self.store)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.defaults = {
            hidden: prompt_assets.save_long_term_memory(
                "", "默认记忆", "隐藏创建规则" if hidden else "普通创建规则",
                hidden_space=hidden,
            )
            for hidden in (False, True)
        }

    def create(self, hidden):
        state = system_prompts.PromptState(False, [], [], 1, 1, "")
        # An ordinary record also exists in the real app's hidden space.
        system_prompts.save_prompt(
            state, record_id="", title="普通设定", prompt="prompt",
            long_term_memory=self.defaults[False].content,
            final_long_term_memory_id=self.defaults[False].id,
        )
        if hidden:
            state.hidden_space = True
            system_prompts.save_prompt(
                state, record_id="", title="隐藏设定", prompt="prompt",
                long_term_memory=self.defaults[True].content,
                final_long_term_memory_id=self.defaults[True].id,
            )
        return state, state.selected_record_id

    def test_creation_keeps_selected_rules_when_default_later_changes(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                state, record_id = self.create(hidden)
                selected_rules = self.defaults[hidden].content
                self.assertEqual(system_prompts.get_record(state, record_id).long_term_memory, selected_rules)
                prompt_assets.save_long_term_memory(
                    self.defaults[hidden].id, "默认记忆", "后续修改的默认规则", hidden_space=hidden,
                )
                reloaded = system_prompts.load_state(hidden_space=hidden)
                self.assertEqual(system_prompts.get_record(reloaded, record_id).long_term_memory, selected_rules)

    def test_cleared_memory_stays_empty_on_disk_and_repeated_loads(self):
        for hidden in (False, True):
            for value in ("", " \n\t "):
                with self.subTest(hidden=hidden, value=value):
                    state, record_id = self.create(hidden)
                    system_prompts.save_prompt(
                        state, record_id=record_id, title="清空后的设定", prompt="prompt",
                        long_term_memory=value,
                    )
                    key = (AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS if hidden
                           else AppStorageKeys.SYSTEM_PROMPT_RECORDS)
                    persisted = self.store.get(key)
                    record = next(item for item in json.loads(persisted) if item["id"] == record_id)
                    self.assertEqual(record["long_term_memory"], "")
                    with patch.object(system_prompts, "_persist", wraps=system_prompts._persist) as persist:
                        for _ in range(3):
                            reloaded = system_prompts.load_state(hidden_space=hidden)
                            self.assertEqual(system_prompts.get_record(reloaded, record_id).long_term_memory, "")
                        persist.assert_not_called()
                    self.assertEqual(self.store.get(key), persisted)

    def test_saving_other_fields_does_not_repopulate_cleared_memory(self):
        state, record_id = self.create(False)
        system_prompts.save_prompt(
            state, record_id=record_id, title="设定", prompt="prompt", long_term_memory="",
        )
        state = system_prompts.load_state()
        system_prompts.save_prompt(state, record_id=record_id, title="改名", prompt="新的 prompt")
        reloaded = system_prompts.load_state()
        record = system_prompts.get_record(reloaded, record_id)
        self.assertEqual(record.title, "改名")
        self.assertEqual(record.long_term_memory, "")


if __name__ == "__main__":
    unittest.main()
