"""Media lifetime tests use temporary directories and never call paid APIs."""
import base64
import io
import json
from pathlib import Path
import stat
import threading
from unittest.mock import Mock

import pytest
import requests
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.api import media_clients
from app.config import SettingsStore
from app.models import ChatRecord, GeneratedImageRecord, GeneratedMediaKind
from app.services import media_store, page2_service
from app import storage


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "MEDIA_DIR", tmp_path / "Media")
    monkeypatch.setattr(media_store, "LEGACY_MEDIA_DIR", tmp_path / "old" / "Media")
    monkeypatch.setattr(media_store, "CHAT_RECORDS_DIR", tmp_path / "Chats")
    monkeypatch.setattr(storage, "CHAT_RECORDS_DIR", tmp_path / "Chats")
    monkeypatch.setattr(storage, "settings", SettingsStore(tmp_path / "settings.json"))
    monkeypatch.setattr(media_store, "request_cache", Mock())
    monkeypatch.setattr(media_clients.requests, "get", Mock(side_effect=AssertionError("Unexpected network")))
    clock = [1_800_000_000.0]
    monkeypatch.setattr(media_store.time, "time", lambda: clock[0])
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    image = output.getvalue()
    monkeypatch.setattr(media_store, "download_image_bytes", Mock(return_value=image))
    monkeypatch.setattr(media_store, "download_video_bytes", Mock(return_value=b"video-with-audio"))
    monkeypatch.setattr(media_store.OpenRouterConfig, "api_key", lambda: "test-key")
    return clock, image


def record(kind="image", **kwargs):
    return GeneratedImageRecord(provider="test", prompt="prompt", media_kind=GeneratedMediaKind(kind),
        image_url_string="https://example.com/image.png" if kind == "image" else None,
        video_url_string="https://openrouter.ai/api/v1/videos/job/content" if kind == "video" else None,
        **kwargs)


@pytest.mark.parametrize("kind", ["image", "video"])
def test_exact_48_hours_and_no_resurrection(sandbox, kind):
    clock, _ = sandbox
    item = media_store.cache_generated(record(kind))
    path = media_store.local_path(item)
    assert path.is_file()
    assert "临时" in str(path)
    if kind == "video":
        media_store.download_video_bytes.assert_called_once_with(item.video_url_string, api_key="test-key")
    deadline = media_store.info(item)["expires_at"]
    clock[0] = deadline - 1
    assert media_store.available(item)
    # Reading again never extends the original deadline.
    media_store.cache_record(item)
    assert media_store.info(item)["expires_at"] == deadline
    clock[0] = deadline
    assert not media_store.available(item)
    media_store.cleanup()
    assert not path.exists()
    assert not media_store.available(GeneratedImageRecord.from_dict(item.to_dict()))
    with pytest.raises(media_clients.MediaExpiredError):
        media_store.cache_record(item)


@pytest.mark.parametrize("kind", ["image", "video"])
def test_save_survives_deadline_and_chat_deletion(sandbox, kind):
    clock, _ = sandbox
    item = media_store.cache_generated(record(kind))
    old_path = media_store.local_path(item)
    clock[0] += media_store.RETENTION_SECONDS - 1
    media_store.save(item)
    path = media_store.local_path(item)
    assert path.parent == media_store.MEDIA_DIR.parent / ".保存"
    if kind == "image" and hasattr(stat, "UF_HIDDEN"):
        assert path.stat().st_flags & stat.UF_HIDDEN
    assert not old_path.exists()
    chat = ChatRecord(title="test", turns=[], system_prompt="", generated_images=[item])
    storage.ChatRecordStore.save_or_update_record(chat)
    storage.ChatRecordStore.delete_record(chat.id)
    clock[0] += 100 * media_store.RETENTION_SECONDS
    media_store.cleanup()
    # The reconstructed record uses only on-disk manifests, not session state.
    restored = GeneratedImageRecord.from_dict(item.to_dict())
    assert media_store.local_path(restored) == path
    assert path.is_file() and media_store.info(restored)["expires_at"] is None


def test_legacy_saved_image_archived_before_chat_deleted(sandbox):
    _, image = sandbox
    item = record(image_data_base64=base64.b64encode(image).decode())
    chat = ChatRecord(title="legacy", turns=[], system_prompt="", generated_images=[item])
    storage.ChatRecordStore.save_or_update_record(chat)
    storage.ChatRecordStore.delete_record(chat.id)
    assert media_store.info(item)["saved"]
    assert media_store.read_bytes(item) == image


def test_legacy_archive_failure_keeps_chat_and_index(sandbox, monkeypatch):
    _, image = sandbox
    item = record(image_data_base64=base64.b64encode(image).decode())
    chat = ChatRecord(title="legacy", turns=[], system_prompt="", generated_images=[item])
    storage.ChatRecordStore.save_or_update_record(chat)
    monkeypatch.setattr(media_store, "save", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        storage.ChatRecordStore.delete_record(chat.id)
    assert storage.ChatRecordStore.load_index()[0].id == chat.id
    assert storage.ChatRecordStore.record_file_path(storage.ChatRecordStore.record_file_name(chat.id)).exists()


def test_new_base64_is_temporary_but_legacy_base64_is_permanent(sandbox):
    _, image = sandbox
    encoded = base64.b64encode(image).decode()
    new = media_store.cache_generated(record(image_data_base64=encoded))
    old = record(image_data_base64=encoded)
    media_store.cache_record(old)
    assert new.image_data_base64 is None
    assert not media_store.info(new)["saved"]
    assert media_store.info(old)["saved"]


@pytest.mark.parametrize("failure, status", [
    (media_clients.MediaExpiredError("gone"), "expired"),
    (requests.Timeout(), "retry"),
    (RuntimeError("HTTP 403: access denied"), "retry"),
])
def test_download_failure_is_not_confused_with_expiry(sandbox, monkeypatch, failure, status):
    monkeypatch.setattr(media_store, "download_image_bytes", Mock(side_effect=failure))
    item = media_store.cache_generated(record())
    assert media_store.info(item)["status"] == status
    assert media_store.available(item) == (status == "retry")


def test_delete_while_download_in_flight_does_not_recreate_file(sandbox, monkeypatch):
    _, image = sandbox
    entered, release = threading.Event(), threading.Event()
    def download(url):
        entered.set()
        assert release.wait(3)
        return image
    monkeypatch.setattr(media_store, "download_image_bytes", download)
    item = record()
    worker = threading.Thread(target=media_store.cache_generated, args=(item,))
    worker.start()
    assert entered.wait(3)
    media_store.delete(item)
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert media_store.info(item)["status"] == "deleted"
    assert not list(media_store.MEDIA_DIR.parent.rglob("*.png"))


def test_video_source_uses_local_copy_after_provider_url_expires(sandbox):
    _, image = sandbox
    item = media_store.cache_generated(record())
    assert page2_service._video_input_image_bytes(item) == image
    sandbox[0][0] += media_store.RETENTION_SECONDS
    with pytest.raises(ValueError, match="过期"):
        page2_service._video_input_image_bytes(item)


def test_startup_cleanup_without_opening_original_chat(sandbox):
    clock, _ = sandbox
    item = media_store.cache_generated(record())
    path = media_store.local_path(item)
    clock[0] += media_store.RETENTION_SECONDS + 10
    media_store.cleanup()
    assert not path.exists()


def test_cloud_only_manifest_is_deferred_without_reading_or_resurrecting_media(sandbox, monkeypatch):
    _, image = sandbox
    manifest = media_store.MEDIA_DIR / ".index" / "cloud-only.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("cloud placeholder", encoding="utf-8")
    saved_image = media_store.MEDIA_DIR.parent / ".保存" / "unindexed.png"
    saved_image.parent.mkdir(parents=True)
    saved_image.write_bytes(image)

    original_read_text = Path.read_text
    reads = []

    def track_read_text(path, *args, **kwargs):
        if path == manifest:
            reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", track_read_text)
    monkeypatch.setattr(media_store, "_is_dataless", lambda path: path == manifest)

    media_store.migrate_media_layout()
    media_store.cleanup()
    assert media_store.list_images() == []
    assert reads == []
    assert saved_image.exists()
    assert manifest.exists()


def test_cloud_only_manifest_lookup_fails_without_overwriting_it(sandbox, monkeypatch):
    item = record()
    manifest = media_store._manifest_path(item.id)
    manifest.parent.mkdir(parents=True)
    manifest.write_text("cloud placeholder", encoding="utf-8")
    monkeypatch.setattr(media_store, "_is_dataless", lambda path: path == manifest)

    with pytest.raises(OSError, match="iCloud"):
        media_store.info(item)
    assert manifest.read_text(encoding="utf-8") == "cloud placeholder"


@pytest.mark.parametrize("kind", ["image", "video"])
def test_ui_save_action_without_storage_details(sandbox, kind):
    item = media_store.cache_generated(record(kind))
    app = AppTest.from_string(f'''
import streamlit as st
from app.models import GeneratedImageRecord
from web.pages import page2
from unittest.mock import patch
page2._ensure_state()
item = GeneratedImageRecord.from_dict({item.to_dict()!r})
st.session_state.page2_generated_media = [item]
st.session_state.page2_selected_media_id = item.id
with patch.object(page2, "_upsert_record"), patch.object(page2.url_favorites, "get_preview_url", return_value=""):
    page2._render_media_column()
''').run()
    assert not app.exception
    assert not any("临时保存至" in caption.value or "文件位置：" in caption.value for caption in app.caption)
    assert not any("本地图片和视频" in expander.label for expander in app.expander)
    app.button(key=f"page2_save_{kind}_{item.id}").click().run()
    assert not app.exception
    assert media_store.info(item)["saved"]
    assert app.button(key=f"page2_save_{kind}_{item.id}").disabled
    assert any("已永久保存" in caption.value for caption in app.caption)
    assert not any("文件位置：" in caption.value for caption in app.caption)


def test_both_selectors_exclude_unselected_expired_images_and_videos(sandbox):
    clock, _ = sandbox
    expired_image = media_store.cache_generated(record())
    expired_video = media_store.cache_generated(record("video"))
    selected_expired = expired_image.id
    clock[0] += media_store.RETENTION_SECONDS
    fresh = media_store.cache_generated(record())
    items = [expired_image.to_dict(), expired_video.to_dict(), fresh.to_dict()]
    app = AppTest.from_string(f'''
import streamlit as st
from app.models import GeneratedImageRecord
from web.pages import page2
from unittest.mock import patch
page2._ensure_state()
st.session_state.page2_generated_media = [GeneratedImageRecord.from_dict(d) for d in {items!r}]
st.session_state.page2_selected_media_id = {selected_expired!r}
with patch.object(page2.url_favorites, "get_preview_url", return_value=""):
    page2._render_media_column()
''').run()
    assert not app.exception
    assert len(app.selectbox(key="page2_media_record_select").options) == 2
    assert app.selectbox(key="page2_media_record_select").options[0] == "无图片／正常背景"
    assert len(app.multiselect[0].options) == 1
    assert app.session_state.page2_video_source == fresh.id
    assert app.session_state.page2_selected_media_id == ""


def test_explicit_expiry_not_permission_failure(sandbox):
    for status, text, expected in [(403, "Access denied", False), (401, "Invalid key", False),
                                   (403, "Request has expired", True), (410, "gone", True)]:
        assert media_clients._response_media_expired(Mock(status_code=status, text=text)) == expected


def test_migration_visits_old_chats_and_guest_scope(sandbox):
    root = media_store.CHAT_RECORDS_DIR
    (root / "guest").mkdir(parents=True)
    for folder in (root, root / "guest"):
        (folder / "record-a.json").write_text(json.dumps({"generatedImages": [record().to_dict()]}))
    media_store.migrate_existing()
    assert media_store.request_cache.call_count == 2


def test_guest_reset_keeps_legacy_saved_image(sandbox):
    _, image = sandbox
    item = record(image_data_base64=base64.b64encode(image).decode())
    chat = ChatRecord(title="guest", turns=[], system_prompt="", generated_images=[item])
    storage.ChatRecordStore.save_or_update_record(chat, scope="guest")
    storage.ChatRecordStore.clear_scope("guest")
    assert not storage.ChatRecordStore.load_index(scope="guest")
    assert media_store.info(item)["saved"]
    assert media_store.read_bytes(item) == image


def test_migration_does_not_open_cloud_placeholder(sandbox, monkeypatch):
    cloud = Mock()
    cloud.stat.return_value.st_flags = 0x40000000
    root = Mock()
    root.rglob.return_value = [cloud]
    monkeypatch.setattr(media_store, "CHAT_RECORDS_DIR", root)
    media_store.migrate_existing()
    cloud.read_text.assert_not_called()
    media_store.request_cache.assert_not_called()


def test_missing_local_file_removed_without_redownloading(sandbox):
    item = media_store.cache_generated(record())
    media_store.local_path(item).unlink()
    assert not media_store.available(item)
    with pytest.raises(media_clients.MediaExpiredError):
        media_store.cache_record(item)
    media_store.download_image_bytes.assert_called_once()


def test_move_media_preserves_bytes_saved_flag_and_original_deadline(sandbox, monkeypatch):
    old = media_store.MEDIA_DIR
    temporary = media_store.cache_generated(record())
    permanent = media_store.cache_generated(record("video"))
    media_store.save(permanent)
    deadline = media_store.info(temporary)["expires_at"]
    before = {item.id: media_store.read_bytes(item) for item in (temporary, permanent)}
    monkeypatch.setattr(media_store, "LEGACY_MEDIA_DIR", old)
    monkeypatch.setattr(media_store, "MEDIA_DIR", old.parent / ".media")
    media_store.migrate_media_directory()
    for item in (temporary, permanent):
        assert media_store.read_bytes(item) == before[item.id]
        assert media_store.local_path(item).is_relative_to(media_store.MEDIA_DIR.parent)
    assert media_store.info(temporary)["expires_at"] == deadline
    assert media_store.info(permanent)["saved"]
    media_store.migrate_media_directory()  # Idempotent after a restart.
    assert media_store.read_bytes(permanent) == before[permanent.id]
    if media_store.os.name == "posix":
        assert old.is_symlink() and old.resolve() == media_store.MEDIA_DIR


def test_migration_conflicts_do_not_overwrite_files(sandbox):
    old, new = media_store.LEGACY_MEDIA_DIR, media_store.MEDIA_DIR
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "same.jpg").write_bytes(b"original")
    (new / "same.jpg").write_bytes(b"different")
    with pytest.raises(ValueError, match="同名"):
        media_store.migrate_media_directory()
    assert (old / "same.jpg").read_bytes() == b"original"
    assert (new / "same.jpg").read_bytes() == b"different"


def test_layout_migration_flattens_categories_beside_media(sandbox):
    _, image = sandbox
    temporary = media_store.cache_generated(record())
    permanent = media_store.cache_generated(record("video"))
    media_store.save(permanent)
    root = media_store.MEDIA_DIR.parent
    # Rebuild the nested 临时/已保存 × 图片/视频 layout old versions left on disk.
    for manifest in list((media_store.MEDIA_DIR / ".index").glob("*.json")):
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        category = "已保存" if meta["saved"] else "临时"
        kind = "视频" if meta["kind"] == "video" else "图片"
        old_relative = str(Path(category) / kind / Path(meta["path"]).name)
        nested = media_store.MEDIA_DIR / old_relative
        nested.parent.mkdir(parents=True, exist_ok=True)
        (root / meta["path"]).replace(nested)
        manifest.write_text(json.dumps({**meta, "path": old_relative}, ensure_ascii=False), encoding="utf-8")
    media_store.migrate_media_layout()
    image_path = media_store.local_path(temporary)
    video_path = media_store.local_path(permanent)
    # Images and videos share the same hidden folder; only lifetime splits them.
    assert image_path.parent == root / ".临时" and image_path.is_file()
    assert video_path.parent == root / ".保存" and video_path.is_file()
    assert media_store.read_bytes(temporary) == image
    assert media_store.read_bytes(permanent) == b"video-with-audio"
    assert not media_store.info(temporary)["saved"]
    assert media_store.info(permanent)["saved"]
    assert not (media_store.MEDIA_DIR / "临时").exists()
    assert not (media_store.MEDIA_DIR / "已保存").exists()
    media_store.migrate_media_layout()  # Idempotent after a restart.
    assert media_store.read_bytes(permanent) == b"video-with-audio"


def test_layout_migration_hides_visible_media_folders(sandbox):
    _, image = sandbox
    temporary = media_store.cache_generated(record())
    permanent = media_store.cache_generated(record("video"))
    media_store.save(permanent)
    root = media_store.MEDIA_DIR.parent
    # Rebuild the visible flat 临时/保存 folders the previous version used.
    for manifest in list((media_store.MEDIA_DIR / ".index").glob("*.json")):
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        visible = str(Path("保存" if meta["saved"] else "临时") / Path(meta["path"]).name)
        (root / visible).parent.mkdir(parents=True, exist_ok=True)
        (root / meta["path"]).replace(root / visible)
        manifest.write_text(json.dumps({**meta, "path": visible}, ensure_ascii=False), encoding="utf-8")
    media_store.migrate_media_layout()
    assert media_store.local_path(temporary).parent == root / ".临时"
    assert media_store.local_path(permanent).parent == root / ".保存"
    assert media_store.read_bytes(temporary) == image
    assert media_store.read_bytes(permanent) == b"video-with-audio"
    # The visible folders are gone once everything moved into the hidden ones.
    assert not (root / "临时").exists()
    assert not (root / "保存").exists()
    media_store.migrate_media_layout()  # Idempotent after a restart.
    assert media_store.read_bytes(permanent) == b"video-with-audio"


def test_saved_image_migrates_from_root_to_saved_folder(sandbox):
    _, image = sandbox
    item = media_store.cache_generated(record())
    media_store.save(item)
    root = media_store.MEDIA_DIR.parent
    current = media_store.local_path(item)
    former = root / current.name
    current.replace(former)
    manifest = media_store._manifest_path(item.id)
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**meta, "path": str(former.relative_to(root))}, ensure_ascii=False), encoding="utf-8")
    media_store.migrate_media_layout()
    assert media_store.local_path(item) == current
    assert current.read_bytes() == image
    assert not former.exists()
    if hasattr(stat, "UF_HIDDEN"):
        assert current.stat().st_flags & stat.UF_HIDDEN
    media_store.migrate_media_layout()
    assert media_store.local_path(item) == current


def test_images_are_hidden_without_moving(sandbox):
    _, image = sandbox
    root = media_store.MEDIA_DIR.parent
    saved = root / ".保存"
    saved.mkdir()
    for path in (root / "manually-added.PNG", saved / "another.JPG"):
        path.write_bytes(image)
    media_store.hide_project_images()
    for path in (root / "manually-added.PNG", saved / "another.JPG"):
        assert path.is_file()
        if hasattr(stat, "UF_HIDDEN"):
            assert path.stat().st_flags & stat.UF_HIDDEN
