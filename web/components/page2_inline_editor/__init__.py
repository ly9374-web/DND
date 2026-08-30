from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


_component = components.declare_component(
    "page2_inline_editor",
    path=str(Path(__file__).parent / "frontend"),
)


def page2_inline_editor(key: str = "page2_inline_editor") -> dict[str, Any] | None:
    value = _component(key=key, default=None)
    return value if isinstance(value, dict) else None
