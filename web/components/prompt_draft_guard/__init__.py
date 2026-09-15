from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_component = components.declare_component(
    "dnd_prompt_draft_guard",
    path=str(Path(__file__).parent / "frontend"),
)


def prompt_draft_guard_component(**kwargs):
    return _component(default=None, **kwargs)
