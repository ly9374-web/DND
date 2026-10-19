"""Read the code-owned prompt text from one editable TOML file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import toml


CATALOG_PATH = Path(__file__).with_name("internal_prompts.toml")

REQUIRED = {
    "chat": ("empty_enter", "dice_result", "auto_roll_marker", "unexpected_event"),
    "image": ("first_person", "closeup", "normal"),
    "video": ("system", "user_message"),
    "story_brain": ("system_default", "context_wrapper", "update_user_message"),
    "world_book": ("questions_system", "generation_system"),
    "character_card": ("questions_system", "generation_system", "brief", "random_brief"),
    "refinement": ("with_answers",),
    "defaults": ("final_first_input", "old_player_first_input", "template_background",
                 "template_dm_core", "template_response_format"),
}

PLACEHOLDERS = {
    ("chat", "dice_result"): {"point"},
    ("image", "closeup"): {"subject"},
    ("video", "user_message"): {"image_prompt", "story_text"},
    ("story_brain", "context_wrapper"): {"story_brain", "user_message"},
    ("story_brain", "update_user_message"): {"history", "current"},
    ("character_card", "brief"): {"identity", "relationship", "personality", "other"},
    ("refinement", "with_answers"): {"base_prompt", "questions", "answers"},
}
_placeholder = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict[str, str]]:
    """Validate once per process; restart the app after editing the file."""
    try:
        catalog = toml.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, toml.TomlDecodeError) as exc:
        raise ValueError(f"无法读取内置 Prompt 文件 {CATALOG_PATH}: {exc}") from exc

    for section, keys in REQUIRED.items():
        values = catalog.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"内置 Prompt 缺少 [{section}] 分区")
        for key in keys:
            value = values.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"内置 Prompt 缺少或清空了 {section}.{key}")
            expected = PLACEHOLDERS.get((section, key), set())
            if expected or _placeholder.search(value):
                actual = set(_placeholder.findall(value))
                if actual != expected:
                    raise ValueError(
                        f"内置 Prompt {section}.{key} 的变量应为 {sorted(expected)}，"
                        f"实际为 {sorted(actual)}"
                    )

    marker = catalog["chat"]["auto_roll_marker"]
    if not all(marker in catalog["defaults"][key]
               for key in ("template_dm_core", "template_response_format")):
        raise ValueError("自动掷骰触发语句与默认固定模板不一致")
    return catalog


def prompt(section: str, key: str) -> str:
    try:
        return load_catalog()[section][key]
    except KeyError as exc:
        raise ValueError(f"内置 Prompt 不存在：{section}.{key}") from exc


def render(section: str, key: str, **values: str) -> str:
    expected = PLACEHOLDERS.get((section, key), set())
    if set(values) != expected:
        raise ValueError(f"内置 Prompt {section}.{key} 需要变量：{sorted(expected)}")
    return _placeholder.sub(
        lambda match: str(values[match.group(1)]),
        prompt(section, key),
    )
