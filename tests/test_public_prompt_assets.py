import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppStorageKeys, SettingsStore
from app.models import SystemPromptRecord
from app.services import public_prompt_seed


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ASSETS_PATH = ROOT / "public_prompt_assets" / "naruto_public.json"


class PublicPromptAssetTests(unittest.TestCase):
    def test_public_asset_titles_match_the_explicit_allowlist(self):
        payload = json.loads(PUBLIC_ASSETS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["title"] for item in payload["world_books"]],
            ["火影忍者"],
        )
        self.assertEqual(
            [item["title"] for item in payload["character_cards"]],
            ["日向雏田", "油女志乃", "犬冢牙"],
        )
        self.assertEqual(
            [item["title"] for item in payload["fixed_templates"]],
            ["默认模板"],
        )
        self.assertEqual(
            [item["title"] for item in payload["final_settings"]],
            ["火影忍者·油女志乃"],
        )

    def test_public_assets_have_no_private_setting_fields(self):
        payload_text = PUBLIC_ASSETS_PATH.read_text(encoding="utf-8")
        forbidden_fields = {
            "apiKey",
            "apiToken",
            "apiSecret",
            "chatRecordIndex",
            "generated_images",
            "created_at",
            "updated_at",
            "final_world_book_id",
            "final_user_character_id",
        }
        for field in forbidden_fields:
            self.assertNotIn(f'"{field}"', payload_text)

    def test_first_run_seeds_only_the_public_final_setting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            with patch.object(public_prompt_seed, "settings", store):
                self.assertTrue(public_prompt_seed.seed_public_prompt_assets_if_needed())
                self.assertFalse(public_prompt_seed.seed_public_prompt_assets_if_needed())

            final_settings = json.loads(
                store.get(AppStorageKeys.SYSTEM_PROMPT_RECORDS, "[]")
            )
            self.assertEqual(
                [item["title"] for item in final_settings],
                ["火影忍者·油女志乃"],
            )
            self.assertEqual(
                store.get(AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID),
                final_settings[0]["id"],
            )

    def test_migration_removes_old_builtins_and_preserves_custom_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            records = [
                SystemPromptRecord(title="小说生成", prompt="旧内置内容"),
                SystemPromptRecord(title="无prompt", prompt=""),
                SystemPromptRecord(title="用户自己的设定", prompt="保留我"),
            ]
            store.set(
                AppStorageKeys.SYSTEM_PROMPT_RECORDS,
                json.dumps([item.to_dict() for item in records], ensure_ascii=False),
            )

            with patch.object(public_prompt_seed, "settings", store):
                self.assertTrue(public_prompt_seed.seed_public_prompt_assets_if_needed())

            migrated = json.loads(
                store.get(AppStorageKeys.SYSTEM_PROMPT_RECORDS, "[]")
            )
            titles = [item["title"] for item in migrated]
            self.assertNotIn("小说生成", titles)
            self.assertNotIn("无prompt", titles)
            self.assertIn("用户自己的设定", titles)
            self.assertIn("火影忍者·油女志乃", titles)

    def test_legacy_default_prompt_file_is_empty(self):
        default_prompt_path = ROOT / "app" / "default_prompts" / "system_prompt.json"
        payload = json.loads(default_prompt_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("records"), [])


if __name__ == "__main__":
    unittest.main()
