"""Export current ordinary prompts. Never publish the runtime settings file."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.services.public_prompt_bundle import build_public_bundle

OUTPUT_PATH = ROOT / "public_prompt_assets" / "ordinary.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("VS_IMAGE_NOVEL_DATA_DIR") or ROOT / ".python_app_data") / "user_defaults.json")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true", help="只核对导出是否与当前普通区一致，不写文件")
    args = parser.parse_args()
    values = json.loads(args.source.read_text(encoding="utf-8"))
    payload, warnings = build_public_bundle(values)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    for warning in warnings:
        print(warning, file=sys.stderr)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("普通区导出已过期，请重新运行导出脚本。")
        print("普通区导出与本地一致。")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, args.output)
    print(args.output)
    print(json.dumps({key: len(value) for key, value in payload.items() if isinstance(value, list)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
