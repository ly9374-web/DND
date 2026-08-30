import json
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
