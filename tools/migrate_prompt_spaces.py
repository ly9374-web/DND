"""One-time, offline prompt move. Run with the app stopped; default is a dry run."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.services.public_prompt_bundle import decode_records

# This is a one-time move policy, never an export or startup filter.
KEEP = {
    "worldBookRecords": {"火影忍者"},
    "characterCardRecords": {"日向雏田", "油女志乃", "犬冢牙"},
    "systemPromptRecords": {"火影忍者·油女志乃"},
}


def hidden_key(key):
    return "hidden" + key[0].upper() + key[1:]


def plan_migration(values: dict) -> tuple[dict, dict]:
    result = deepcopy(values)
    report = {}
    for key, keep in KEEP.items():
        hidden = hidden_key(key)
        ordinary, private = decode_records(values, key), decode_records(values, hidden)
        ids = [r.get("id") for r in ordinary + private]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"{key} 有重复或缺失 ID，停止迁移")
        # Never guess if the intended retained asset is missing or ambiguous.
        for title in keep:
            if sum(r.get("title") == title for r in ordinary) != 1:
                raise ValueError(f"普通区保留项 {title} 缺失或重名，停止迁移")
        retained = [r for r in ordinary if r.get("title") in keep]
        moved = [r for r in ordinary if r.get("title") not in keep]
        report[key] = {"retained": len(retained), "moved": len(moved), "hidden_before": len(private), "hidden_after": len(private) + len(moved)}
        if not moved:
            continue
        for dest, rows in ((key, retained), (hidden, private + moved)):
            result[dest] = rows if isinstance(values.get(dest), list) else json.dumps(rows, ensure_ascii=False)
        before = {r["id"]: r for r in ordinary + private}
        after = {r["id"]: r for r in decode_records(result, key) + decode_records(result, hidden)}
        if before != after or decode_records(result, hidden)[:len(private)] != private:
            raise ValueError("记录完整性检查失败，停止迁移")
    allowed = set(KEEP) | {hidden_key(key) for key in KEEP}
    if {k: v for k, v in result.items() if k not in allowed} != {k: v for k, v in values.items() if k not in allowed}:
        raise ValueError("非迁移字段发生变化，停止迁移")
    return result, report


def file_manifest(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def verify_chat_index(data_dir: Path, values: dict) -> int:
    count = 0
    indexed_paths = set()
    for key, scope in (("chatRecordIndex", ""), ("guestChatRecordIndex", "guest")):
        ids = set()
        for entry in decode_records(values, key):
            name = entry.get("file_name", entry.get("fileName", ""))
            path = data_dir / "ChatRecords" / scope / name
            if not name or path in indexed_paths or entry.get("id") in ids or not path.is_file():
                raise ValueError("聊天索引重复或文件缺失，停止迁移")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("id") != entry.get("id") or bool(raw.get("hidden")) != bool(entry.get("hidden")):
                raise ValueError("聊天记录和索引不一致，停止迁移")
            indexed_paths.add(path)
            ids.add(entry.get("id"))
            count += 1
    actual_paths = set((data_dir / "ChatRecords").rglob("*.json"))
    if actual_paths != indexed_paths:
        raise ValueError("存在没有索引的聊天文件，停止迁移；不会清理这些文件")
    return count


def apply_migration(data_dir: Path, backup_root: Path) -> dict:
    path = data_dir / "user_defaults.json"
    original = path.read_bytes()
    values = json.loads(original)
    result, report = plan_migration(values)
    chats = verify_chat_index(data_dir, values)
    before = file_manifest(data_dir)
    backup = backup_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True, mode=0o700)
    shutil.copytree(data_dir, backup / "data")
    if file_manifest(backup / "data") != before or file_manifest(data_dir) != before or path.read_bytes() != original:
        raise ValueError("备份校验失败或数据仍在变化，停止迁移")
    (backup / "manifest.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    old_export = ROOT / "public_prompt_assets" / "naruto_public.json"
    if old_export.exists():
        shutil.copy2(old_export, backup / old_export.name)
    if result != values:
        temporary = path.with_name(".prompt-space-migration.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if file_manifest(data_dir) != {**before, temporary.name: hashlib.sha256(temporary.read_bytes()).hexdigest()}:
            temporary.unlink()
            raise ValueError("迁移期间数据变化，停止写入")
        os.replace(temporary, path)
    after = file_manifest(data_dir)
    expected = dict(before)
    expected["user_defaults.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if after != expected or json.loads(path.read_bytes()) != result:
        raise ValueError(f"写入后校验失败，请从备份恢复：{backup}")
    verify_chat_index(data_dir, result)
    summary = {"backup": str(backup), "chat_records_unchanged": chats, "changes": report, "verified": True}
    (backup / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".python_app_data")
    parser.add_argument("--backup-root", type=Path, default=ROOT / ".prompt_migration_backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        report = apply_migration(args.data_dir, args.backup_root)
    else:
        values = json.loads((args.data_dir / "user_defaults.json").read_text(encoding="utf-8"))
        _, report = plan_migration(values)
        report["chat_records_checked"] = verify_chat_index(args.data_dir, values)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
