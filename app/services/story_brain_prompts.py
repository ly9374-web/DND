"""Shared Story Brain prompts used by the ordinary application."""

from app.default_prompts.internal_catalog import prompt


DEFAULT_STORY_BRAIN_GENERATOR_PROMPT = prompt("story_brain", "system_default")


def remove_obsolete_foreshadow_rule(text: str) -> str:
    return str(text or "").replace(" 5.不记录与伏笔重复的内容", "").replace("不记录与伏笔重复的内容", "")
