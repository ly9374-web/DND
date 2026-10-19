"""Acceptance checks for editable code-owned prompts."""

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import toml

from app.default_prompts import internal_catalog
from app.default_prompts.first_inputs import (
    OLD_PLAYER_FIRST_INPUT_V1_SHA256,
)
from app.models import SystemPromptRecord
from app.services import page2_service
from app.services.image_prompts import build_image_prompt_system_prompt
from web.pages import page2


class InternalPromptCatalogTests(unittest.TestCase):
    def test_catalog_has_every_required_prompt_and_renders_user_values(self):
        catalog = internal_catalog.load_catalog()
        for section, keys in internal_catalog.REQUIRED.items():
            self.assertTrue(all(catalog[section][key].strip() for key in keys))

        self.assertEqual(page2._EMPTY_SPACE_CONTINUE_MESSAGE,
                         internal_catalog.prompt("chat", "empty_enter"))
        dice = internal_catalog.render("chat", "dice_result", point="17")
        self.assertIn("17", dice)
        self.assertNotIn("{point}", dice)
        video = internal_catalog.render("video", "user_message",
                                        image_prompt="图里写着 {story_text}", story_text="剧情")
        self.assertIn("图里写着 {story_text}", video)
        self.assertIn("剧情", video)
        with patch.object(page2_service, "roll_point", return_value=17):
            self.assertEqual(page2._roll_user_dice_message(), dice)

        self.assertEqual(build_image_prompt_system_prompt("first_person"),
                         internal_catalog.prompt("image", "first_person"))
        self.assertEqual(build_image_prompt_system_prompt("normal"),
                         internal_catalog.prompt("image", "normal"))
        self.assertEqual(build_image_prompt_system_prompt("unknown"),
                         internal_catalog.prompt("image", "normal"))
        self.assertEqual(build_image_prompt_system_prompt("closeup", " 蓝衣人 "),
                         internal_catalog.render("image", "closeup", subject="蓝衣人"))
        with self.assertRaisesRegex(ValueError, "主体不能为空"):
            build_image_prompt_system_prompt("closeup", " ")

        marker = internal_catalog.prompt("chat", "auto_roll_marker")
        self.assertTrue(page2_service.should_auto_roll_dice("故事结尾：" + marker))
        self.assertFalse(page2_service.should_auto_roll_dice(marker + "还有正文"))
        self.assertIn(marker, internal_catalog.prompt("defaults", "template_dm_core"))
        self.assertIn(marker, internal_catalog.prompt("defaults", "template_response_format"))

    def test_editing_file_changes_output_after_reload(self):
        catalog = toml.loads(internal_catalog.CATALOG_PATH.read_text(encoding="utf-8"))
        catalog["chat"]["empty_enter"] = "测试修改后的空输入"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "internal_prompts.toml"
            path.write_text(toml.dumps(catalog), encoding="utf-8")
            try:
                with patch.object(internal_catalog, "CATALOG_PATH", path):
                    internal_catalog.load_catalog.cache_clear()
                    self.assertEqual(internal_catalog.prompt("chat", "empty_enter"),
                                     "测试修改后的空输入")
            finally:
                internal_catalog.load_catalog.cache_clear()

    def test_missing_prompt_and_changed_template_variable_fail_clearly(self):
        original = toml.loads(internal_catalog.CATALOG_PATH.read_text(encoding="utf-8"))
        changes = []
        missing = toml.loads(toml.dumps(original))
        missing["chat"].pop("dice_result")
        changes.append((missing, "chat.dice_result"))
        wrong_variable = toml.loads(toml.dumps(original))
        wrong_variable["image"]["closeup"] = wrong_variable["image"]["closeup"].replace(
            "{subject}", "{person}", 1)
        changes.append((wrong_variable, "image.closeup"))
        mismatched_marker = toml.loads(toml.dumps(original))
        mismatched_marker["chat"]["auto_roll_marker"] = "请立刻掷骰。"
        changes.append((mismatched_marker, "自动掷骰"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "internal_prompts.toml"
            for changed, message in changes:
                with self.subTest(message=message):
                    path.write_text(toml.dumps(changed), encoding="utf-8")
                    try:
                        with patch.object(internal_catalog, "CATALOG_PATH", path):
                            internal_catalog.load_catalog.cache_clear()
                            with self.assertRaisesRegex(ValueError, message):
                                internal_catalog.load_catalog()
                    finally:
                        internal_catalog.load_catalog.cache_clear()

    def test_legacy_first_input_stays_single_when_current_default_is_edited(self):
        historical_content = (
            "*根据背景介绍，世界书和用户角色给用户一个初始场景， "
            "告诉用户现在他周围有什么，现在剧情是从什么时候什么地方开始的，"
            "最近几轮出现需要他处理的事件"
        )
        self.assertEqual(sha256(historical_content.encode("utf-8")).hexdigest(),
                         OLD_PLAYER_FIRST_INPUT_V1_SHA256)
        with patch("app.models.OLD_PLAYER_FIRST_INPUT", "新版开场"):
            record = SystemPromptRecord.from_dict({
                "title": "旧设定", "prompt": "正文",
                "first_inputs": [{"id": "old", "title": "自定义名称", "content": historical_content}],
                "selected_first_input_id": "old",
            })
        self.assertEqual(len(record.first_inputs), 1)
        self.assertEqual(record.first_inputs[0].content, historical_content)
        self.assertEqual(record.selected_first_input_id, "old")


if __name__ == "__main__":
    unittest.main()
