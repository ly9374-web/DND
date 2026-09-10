import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.services import public_prompt_seed
from app.services.public_prompt_bundle import build_public_bundle, CATEGORIES, decode_records
from tools.migrate_prompt_spaces import apply_migration, file_manifest, plan_migration


def sample_values():
    return {
        "worldBookRecords": [{"id": "w", "title": "任意世界", "content": "世界正文", "unknown": "DO_NOT_EXPORT"}],
        "characterCardRecords": [{"id": "c", "title": "角色", "content": "角色正文"}],
        "fixedTemplateRecords": [{"id": "t", "title": "", "background_intro": "背景", "dm_core_tasks": "任务", "response_format": "格式"}],
        "longTermMemoryRecords": [{"id": "m", "title": "规则", "content": "记忆正文"}],
        "quickInputRecords": [{"id": "q", "title": "快捷", "content": "快捷正文"}],
        "systemPromptRecords": [{
            "id": "f", "title": "任意设定", "prompt": "设定正文", "long_term_memory": "单独规则",
            "first_inputs": [{"id": "a", "title": "开局一", "content": "一"}, {"id": "b", "title": "开局二", "content": "二"}],
            "selected_first_input_id": "b", "final_template_id": "t", "final_world_book_id": "w",
            "final_long_term_memory_id": "m", "final_user_character_id": "c", "final_other_character_ids": ["c"],
        }],
        "selectedSystemPromptRecordID": "f", "selectedFixedTemplateRecordID": "t", "defaultLongTermMemoryRecordID": "m",
        "hiddenSystemPromptRecords": "PRIVATE_SENTINEL",
        "hiddenWorldBookRecords": "PRIVATE_SENTINEL",
        "apiKey": "KEY_SENTINEL", "chatRecordIndex": "CHAT_SENTINEL",
    }


def migration_values():
    def row(i, title):
        return {"id": i, "title": title, "content": "正文 " + i, "unknown": {"must": "survive"}, "created_at": "old"}
    return {
        "worldBookRecords": json.dumps([row("w", "火影忍者"), row("move", "世界")]),
        "hiddenWorldBookRecords": json.dumps([row("h", "世界")]),
        "characterCardRecords": [row(str(i), t) for i, t in enumerate(["日向雏田", "油女志乃", "犬冢牙", "角色"])],
        "hiddenCharacterCardRecords": [row("hc", "角色")],
        "systemPromptRecords": [dict(row("f", "火影忍者·油女志乃"), final_template_id="missing"), row("mf", "其他")],
        "hiddenSystemPromptRecords": [row("hf", "其他")],
        "fixedTemplateRecords": [row("t", ""), row("t2", "普通扮演")],
        "hiddenFixedTemplateRecords": [row("ht", "隐藏模板")],
        "longTermMemoryRecords": [row("m", "记忆")], "hiddenLongTermMemoryRecords": [row("hm", "记忆")],
        "quickInputRecords": [row("q", "快捷")], "hiddenQuickInputRecords": [row("hq", "快捷")],
        "systemPromptRecordNextIndex": 100, "hiddenSystemPromptRecordNextIndex": 200,
        "chatRecordNextIndex": 999, "selectedSystemPromptRecordID": "mf", "systemPrompt": "保留当前正文",
    }


class PublicPromptAssetTests(unittest.TestCase):
    def test_exports_all_ordinary_types_fields_and_stable_ids_without_private_data(self):
        values = sample_values()
        payload, warnings = build_public_bundle(values)
        self.assertEqual(warnings, [])
        text = json.dumps(payload)
        for forbidden in ("PRIVATE_SENTINEL", "KEY_SENTINEL", "CHAT_SENTINEL", "DO_NOT_EXPORT", "created_at"):
            self.assertNotIn(forbidden, text)
        self.assertTrue(all(len(payload[cat]) == 1 for cat in CATEGORIES))
        self.assertEqual(payload["fixed_templates"][0]["title"], "")
        values["worldBookRecords"].append({"id": "new", "title": "任意世界", "content": "重名也保留"})
        updated, _ = build_public_bundle(values)
        self.assertEqual(len(updated["world_books"]), 2)
        self.assertEqual(updated["world_books"][0], payload["world_books"][0])
        values["worldBookRecords"][0]["title"] = "改名"
        renamed, _ = build_public_bundle(values)
        self.assertEqual(renamed["world_books"][0]["asset_id"], payload["world_books"][0]["asset_id"])

    def test_missing_or_private_reference_is_not_exported_or_repaired_locally(self):
        values = sample_values()
        values["systemPromptRecords"][0]["final_template_id"] = "private-template"
        before = json.dumps(values)
        payload, warnings = build_public_bundle(values)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(payload["final_settings"][0]["sources"]["template"], "")
        self.assertNotIn("private-template", json.dumps(payload))
        self.assertEqual(json.dumps(values), before)

    def test_bad_data_fails_instead_of_skipping_records(self):
        for raw in ("broken", "{}", [None], [{"id": "x"}, {"id": "x"}]):
            with self.subTest(raw=raw):
                values = sample_values()
                values["worldBookRecords"] = raw
                with self.assertRaises(ValueError):
                    build_public_bundle(values)

    def test_fresh_import_restores_all_sources_and_first_inputs_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp) / "settings.json")
            path = Path(temp) / "bundle.json"
            payload, _ = build_public_bundle(sample_values())
            path.write_text(json.dumps(payload))
            with patch.object(public_prompt_seed, "settings", store), patch.object(public_prompt_seed, "PUBLIC_ASSETS_PATH", path):
                self.assertTrue(public_prompt_seed.seed_public_prompt_assets_if_needed())
                initial = store.path.read_bytes()
                self.assertFalse(public_prompt_seed.seed_public_prompt_assets_if_needed())
                self.assertEqual(store.path.read_bytes(), initial)
            final = decode_records(store.values, "systemPromptRecords")[0]
            for field, key in (("final_template_id", "fixedTemplateRecords"), ("final_world_book_id", "worldBookRecords"), ("final_long_term_memory_id", "longTermMemoryRecords"), ("final_user_character_id", "characterCardRecords")):
                self.assertEqual(final[field], decode_records(store.values, key)[0]["id"])
            self.assertEqual(final["first_input"], "二")
            self.assertEqual(len(final["first_inputs"]), 2)
            self.assertEqual(final["long_term_memory"], "单独规则")
            self.assertTrue(store.bool(AppStorageKeys.QUICK_INPUT_DEFAULT_SEEDED))
            self.assertFalse(any(k.startswith("hidden") for k in store.values))

    def test_existing_stores_including_deleted_assets_are_completely_untouched(self):
        cases = [
            {"publicPromptAssetSeedVersion": 1}, {"systemPromptRecords": "[]"},
            {"systemPromptRecords": '[{"title":"小说生成","unknown":"keep"}]'},
            {"hiddenSystemPromptRecords": '[{"title":"无prompt","unknown":"keep"}]'},
            {"systemPrompt": "legacy"}, {"chatRecordIndex": "legacy"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            for values in cases:
                with self.subTest(values=values):
                    path = Path(temp) / "settings.json"
                    path.write_text(json.dumps(values))
                    store = SettingsStore(path)
                    before = path.read_bytes()
                    with patch.object(public_prompt_seed, "settings", store):
                        self.assertFalse(public_prompt_seed.seed_public_prompt_assets_if_needed())
                    self.assertEqual(path.read_bytes(), before)

    def test_invalid_bundle_never_partially_writes(self):
        payload, _ = build_public_bundle(sample_values())
        payload["final_settings"][0]["sources"]["world_book"] = "missing"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bundle.json"
            path.write_text(json.dumps(payload))
            store = SettingsStore(Path(temp) / "settings.json")
            with patch.object(public_prompt_seed, "settings", store), patch.object(public_prompt_seed, "PUBLIC_ASSETS_PATH", path):
                with self.assertRaises(KeyError):
                    public_prompt_seed.seed_public_prompt_assets_if_needed()
            self.assertFalse(store.path.exists())

    def test_move_preserves_full_records_existing_hidden_fields_and_counters(self):
        values = migration_values()
        before = json.dumps(values)
        result, _ = plan_migration(values)
        self.assertEqual(json.dumps(values), before)
        self.assertEqual(len(decode_records(result, "hiddenWorldBookRecords")), 2)
        self.assertEqual(decode_records(result, "hiddenWorldBookRecords")[0], decode_records(values, "hiddenWorldBookRecords")[0])
        for key in ("fixedTemplateRecords", "hiddenFixedTemplateRecords", "longTermMemoryRecords", "hiddenLongTermMemoryRecords", "quickInputRecords", "hiddenQuickInputRecords", "chatRecordNextIndex", "systemPromptRecordNextIndex", "hiddenSystemPromptRecordNextIndex", "selectedSystemPromptRecordID", "systemPrompt"):
            self.assertEqual(result[key], values[key])
        second, _ = plan_migration(result)
        self.assertEqual(result, second)

    def test_migration_backup_and_chat_files_and_index_are_identical(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "data"
            (root / "ChatRecords").mkdir(parents=True)
            values = migration_values()
            index = [{"id": "chat", "file_name": "record-chat.json", "hidden": True, "unknown": "index extension"}]
            values["chatRecordIndex"] = json.dumps(index)
            (root / "user_defaults.json").write_text(json.dumps(values))
            (root / "ChatRecords/record-chat.json").write_text(json.dumps({"id": "chat", "hidden": True, "story_brain": {"unknown": "keep"}, "turns": [{"unknown": "keep"}]}))
            (root / "old-backup.json").write_text("arbitrary original backup")
            before = file_manifest(root)
            summary = apply_migration(root, Path(temp) / "backups")
            self.assertEqual(file_manifest(Path(summary["backup"]) / "data"), before)
            after = file_manifest(root)
            self.assertEqual({k: v for k, v in before.items() if k != "user_defaults.json"}, {k: v for k, v in after.items() if k != "user_defaults.json"})
            self.assertEqual(json.loads((root / "user_defaults.json").read_text())["chatRecordIndex"], values["chatRecordIndex"])
            self.assertEqual(summary["chat_records_unchanged"], 1)


if __name__ == "__main__":
    unittest.main()
