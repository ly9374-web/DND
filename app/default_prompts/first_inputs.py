"""Built-in first-input presets shared by new records and legacy migrations."""

from app.default_prompts.internal_catalog import prompt

FIRST_INPUT_DEFAULTS_VERSION = 1
OLD_PLAYER_FIRST_INPUT_TITLE = "老玩家"
OLD_PLAYER_FIRST_INPUT = prompt("defaults", "old_player_first_input")
# Keep recognizing records created before this text became editable in TOML.
OLD_PLAYER_FIRST_INPUT_V1_SHA256 = "fb72f5bfef04c56c6c0564d3beeef2f3658990db5c711716774284144a2c4e67"
