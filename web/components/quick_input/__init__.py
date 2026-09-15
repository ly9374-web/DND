from __future__ import annotations

import json
from pathlib import Path


def quick_input_script(options: list[dict[str, str]], *, scope: str, show_space: bool) -> str:
    options = [*options, {
        "id": "command:resend", "title": "重发", "label": "重发（操作）",
        "content": "/重发", "space": "", "kind": "command",
    }]
    # Contents are data, never executable markup (including literal </script>).
    payload = json.dumps({"options": options, "scope": scope, "showSpace": show_space}, ensure_ascii=False)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    source = (Path(__file__).parent / "quick_input.js").read_text(encoding="utf-8")
    return "<script>\n" + source + "\nwindow.parent.__dndQuickInput.update(" + payload + ");\n</script>"
