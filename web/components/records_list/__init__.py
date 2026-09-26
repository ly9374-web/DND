from pathlib import Path

import streamlit.components.v1 as components


_component = components.declare_component(
    "records_list", path=str(Path(__file__).parent / "frontend"),
)


def records_list(items, selected_id: str):
    value = _component(
        items=[
            {"id": item.id, "title": item.title or "未命名聊天", "updated_at": item.updated_at or ""}
            for item in items
        ],
        selected_id=selected_id,
        key="records_list",
        default=None,
    )
    return value if isinstance(value, dict) else None
