import base64
from datetime import datetime, timedelta
import io
import json
from unittest.mock import Mock

from PIL import Image
import pytest
import requests

from app.api import media_clients
from app.image_inputs import decode_data_url
from app.models import GeneratedImageRecord
from app.services import image_references as refs, media_store, page2_service, temporary_uploads


def png(size=(40, 30), color="blue"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(temporary_uploads, "DIRECTORY", tmp_path / "uploads")
    monkeypatch.setattr(media_clients.requests, "get", Mock(side_effect=AssertionError("Unexpected HTTP GET")))
    monkeypatch.setattr(media_clients.requests, "post", Mock(side_effect=AssertionError("Unexpected HTTP POST")))
    return media_store.import_image(png(), "blue.png")


def test_expired_url_uses_exact_local_id_before_and_after_save(local):
    other = media_store.import_image(png(color="red"), "other.png")
    local.image_url_string = "https://example.com/expired.png"
    local.created_at = (datetime.now() - timedelta(hours=2)).isoformat()
    before = refs.resolve_record(local, provider="bytedance/seedance-2.0-mini", purpose="video")
    old_path = media_store.local_path(local)
    media_store.save(local)
    assert old_path != media_store.local_path(local)
    after = refs.resolve_record(local, provider="wan22Fast", purpose="video")
    assert decode_data_url(before) == decode_data_url(after) == png()
    assert decode_data_url(after) != media_store.read_bytes(other)


def test_fresh_url_is_reused_without_upload_or_encoding(local, monkeypatch):
    local.image_url_string = "https://replicate.delivery/output.png"
    response = Mock(status_code=200, headers={"Content-Type": "image/png"})
    monkeypatch.setattr(refs.requests, "get", Mock(return_value=response))
    assert refs.resolve_record(local, provider="wan22Fast", purpose="video") == local.image_url_string
    response.close.assert_called_once()
    media_clients.requests.post.assert_not_called()


@pytest.mark.parametrize("failure", [404, 403, 503, "timeout", "html"])
def test_failed_url_probe_does_not_delete_local_image(local, monkeypatch, failure):
    local.image_url_string = "https://example.com/image.png"
    response = Mock(status_code=failure if isinstance(failure, int) else 200,
                    headers={"Content-Type": "text/html" if failure == "html" else "image/png"})
    monkeypatch.setattr(refs.requests, "get", Mock(side_effect=requests.Timeout() if failure == "timeout" else None,
                                                 return_value=response))
    result = refs.resolve_record(local, provider="wan22Fast", purpose="video")
    assert decode_data_url(result) == png()
    assert media_store.available(local)


def test_import_is_local_and_never_persists_base64(local):
    assert local.image_data_base64 is None
    assert media_store.read_bytes(local) == png()
    assert "data:image" not in json.dumps(local.to_dict())
    assert not media_store.info(local)["saved"]
    media_clients.requests.post.assert_not_called()


def test_library_lists_all_local_images_without_duplicates_or_expired_files(local, monkeypatch):
    saved = media_store.import_image(png(color="red"), "permanent.png", permanent=True)
    expired = media_store.import_image(png(color="green"), "expired.png")
    original_path = media_store.local_path(local)
    expiration = media_store.info(expired)
    media_store._write({**expiration, "expires_at": 0})
    loose = original_path.parent / "directly-placed.png"
    loose.write_bytes(png(color="yellow"))
    (original_path.parent / "ignore.mp4").write_bytes(b"not an image")
    first = {item.id: item for item in media_store.list_images()}
    second = {item.id: item for item in media_store.list_images()}
    assert set(first) == set(second)
    assert len(first) == 3
    assert expired.id not in first
    assert first[local.id].prompt == "blue"
    assert media_store.info(first[saved.id])["saved"]
    loose_record = next(item for item in first.values() if item.prompt == "directly-placed")
    assert media_store.local_path(loose_record) == loose
    assert media_store.local_path(local) == original_path
    assert all(item.image_data_base64 is None for item in first.values())
    media_store.delete(loose_record)
    assert loose_record.id not in {item.id for item in media_store.list_images()}
    media_clients.requests.get.assert_not_called()


def test_library_remembers_video_draft_without_extending_lifetime(local):
    before = media_store.info(local)
    local.video_prompt = "转身"
    local.video_story_text = "她听到背后的声音。"
    media_store.remember_image(local)
    restored = next(item for item in media_store.list_images() if item.id == local.id)
    assert restored.video_prompt == local.video_prompt
    assert restored.video_story_text == local.video_story_text
    assert media_store.info(restored)["expires_at"] == before["expires_at"]
    assert "base64" not in json.dumps(media_store.info(restored))


def test_refuses_deleted_media_instead_of_using_another_picture(local):
    media_store.import_image(png(color="red"), "other.png")
    reference = refs.media_reference(local)
    media_store.delete(local)
    with pytest.raises(ValueError, match="删除"):
        refs.resolve_inputs([reference], provider="seedream-4.5")


def test_large_video_copy_preserves_original_and_has_matching_mime(local):
    large = media_store.import_image(png((4096, 3072)), "large.png", permanent=True)
    original = media_store.read_bytes(large)
    result = refs.resolve_record(large, provider="wan22Fast", purpose="video")
    assert result.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(decode_data_url(result))) as image:
        assert max(image.size) <= 1680
        assert image.width / image.height == 4 / 3
    assert media_store.read_bytes(large) == original


def test_image_generation_keeps_media_id_not_base64(local, monkeypatch):
    client = Mock(return_value=media_clients.GeneratedImageResult(image_url="https://example.com/new.png"))
    monkeypatch.setattr(media_clients.ReplicateImageAPIClient, "generate_image", client)
    reference = refs.media_reference(local)
    result = page2_service.generate_image("seedream-4.5", "draw", image_urls=[reference])
    assert result.image_input_urls == [reference]
    assert decode_data_url(client.call_args.kwargs["image_urls"][0]) == png()
    assert "base64" not in json.dumps(result.image_input_urls)


@pytest.mark.parametrize(
    ("provider", "purpose", "expected"),
    [
        ("seedream-4.5", "image", refs.REPLICATE_INLINE_BYTES),
        ("wan22Fast", "video", refs.REPLICATE_INLINE_BYTES),
        ("grok", "image", refs.XAI_IMAGE_BYTES),
        ("grokPro", "image", refs.XAI_IMAGE_BYTES),
        ("bytedance/seedance-2.0-mini", "video", refs.SEEDANCE_IMAGE_BYTES),
        ("bytedance/seedance-1-5-pro", "video", refs.SEEDANCE_IMAGE_BYTES),
    ],
)
def test_inline_budget_uses_each_official_route_limit(provider, purpose, expected):
    assert refs._inline_budget(provider=provider, purpose=purpose) == expected


def test_video_records_source_id_and_no_embedded_copy(local, monkeypatch):
    client = Mock(return_value="https://example.com/video.mp4")
    monkeypatch.setattr(media_clients.ReplicateVideoAPIClient, "generate_wan_2_2_i2v_fast", client)
    result = page2_service.generate_video_from_image(ctx=page2_service.default_context(), source_record=local,
                                                     prompt="move", seconds=5)
    assert decode_data_url(client.call_args.args[0]) == png()
    restored = GeneratedImageRecord.from_dict(result.to_dict())
    assert restored.source_image_id == local.id
    assert restored.source_image_data_base64 is None


def test_large_image_requires_consent_then_uploads_without_downscaling(local, monkeypatch):
    monkeypatch.setattr(refs, "REPLICATE_INLINE_BYTES", 1)
    upload = Mock(return_value="https://res.cloudinary.com/example/image.png")
    monkeypatch.setattr(temporary_uploads, "upload", upload)
    requirement = refs.temporary_upload_requirement(local, provider="seedream-4.5")
    assert requirement == {
        "record_id": local.id,
        "size_bytes": len(png()),
        "budget_bytes": 1,
    }
    with pytest.raises(refs.TemporaryUploadConsentRequired, match="确认"):
        refs.resolve_record(local, provider="seedream-4.5")
    upload.assert_not_called()
    assert refs.resolve_record(
        local, provider="seedream-4.5", allow_temporary_upload=True,
    ) == upload.return_value
    assert upload.call_args.args == (png(), "image/png")


def test_available_original_url_never_needs_temporary_upload(local, monkeypatch):
    upload = Mock(return_value="https://example.com/fallback.png")
    monkeypatch.setattr(temporary_uploads, "upload", upload)
    local.image_url_string = "https://example.com/fresh.png"
    local.created_at = datetime.now().isoformat()
    response = Mock(status_code=200, headers={"Content-Type": "image/png"})
    monkeypatch.setattr(refs.requests, "get", Mock(return_value=response))
    assert refs.temporary_upload_requirement(
        local, provider="wan22Fast", purpose="video",
    ) is None
    assert refs.resolve_record(local, provider="wan22Fast", purpose="video") == local.image_url_string
    upload.assert_not_called()


def test_temporary_upload_reused_and_cleaned_only_after_last_lease(local, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(temporary_uploads.time, "time", lambda: clock[0])
    monkeypatch.setattr(temporary_uploads.CloudinaryConfig, "cloud_name", lambda: "test")
    calls = []

    def request(action, parameters, **kwargs):
        calls.append((action, parameters))
        if action == "upload":
            return {"public_id": parameters["public_id"], "asset_id": "test-asset", "secure_url": "https://example.com/ref.png"}
        return {"result": "ok"}

    monkeypatch.setattr(temporary_uploads, "_request", request)
    first = temporary_uploads.upload(png(), "image/png")
    clock[0] += 3600
    assert temporary_uploads.upload(png(), "image/png") == first
    assert len(calls) == 1
    favorite = temporary_uploads.DIRECTORY / "favorite.json"
    favorite.write_text(json.dumps({"cloud": "test", "public_id": "favorites/my-image", "expires_at": 0}))
    clock[0] += temporary_uploads.RETENTION_SECONDS - 1
    temporary_uploads.cleanup()
    assert len(calls) == 1
    clock[0] += 1
    temporary_uploads.cleanup()
    assert [call[0] for call in calls] == ["upload", "destroy"]
    assert calls[0][1]["public_id"] == calls[1][1]["public_id"]
    assert favorite.exists()


def test_ambiguous_upload_is_tracked_for_later_cleanup(local, monkeypatch):
    monkeypatch.setattr(temporary_uploads.CloudinaryConfig, "cloud_name", lambda: "test")
    monkeypatch.setattr(temporary_uploads.CloudinaryConfig, "api_key", lambda: "test-key")
    monkeypatch.setattr(temporary_uploads.CloudinaryConfig, "api_secret", lambda: "test-secret")
    post = Mock(side_effect=requests.ConnectionError(
        "Connection aborted.", TimeoutError("The write operation timed out")))
    monkeypatch.setattr(temporary_uploads.requests, "post", post)
    with pytest.raises(requests.ConnectionError):
        temporary_uploads.upload(png(), "image/png")
    post.assert_called_once()
    assert post.call_args.kwargs["timeout"] == media_clients.IMAGE_SUBMISSION_TIMEOUT
    manifests = [json.loads(p.read_text()) for p in temporary_uploads.DIRECTORY.glob("*.json") if p.name != "namespace.json"]
    assert len(manifests) == 1
    assert manifests[0]["public_id"].startswith("dnd-temp/")


def test_openrouter_accepts_valid_data_url_without_resubmitting_on_timeout(local, monkeypatch):
    monkeypatch.setattr(media_clients.OpenRouterConfig, "api_key", lambda: "test")
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(media_clients.requests, "post", post)
    with pytest.raises(RuntimeError, match="无法确认任务"):
        media_clients.OpenRouterVideoAPIClient.generate_video(
            model="bytedance/seedance-2.0-mini", image_url="data:image/png;base64," + base64.b64encode(png()).decode(),
            prompt="move", seconds=5, aspect_ratio="4:3")
    post.assert_called_once()
