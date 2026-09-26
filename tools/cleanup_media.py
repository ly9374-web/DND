"""Optional manual cleanup; the running app also invokes this automatically."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.media_store import MEDIA_DIR, cleanup, migrate_media_directory, migrate_media_layout


if __name__ == "__main__":
    migrate_media_directory()
    migrate_media_layout()
    cleanup()
    print(f"过期媒体清理完成。媒体目录：{MEDIA_DIR.parent / '.临时'} 和 {MEDIA_DIR.parent / '.保存'}")
