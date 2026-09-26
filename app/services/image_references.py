"""Resolve stable local media IDs into short-lived HTTP request inputs."""
from __future__ import annotations

import base64
from datetime import datetime
import io
import time
from urllib.parse import urlsplit

from PIL import Image, ImageOps
import requests

from app.config import debug_log
from app.image_inputs import decode_data_url, image_mime, validate_image_reference
from app.models import GeneratedImageRecord
from app.services import media_store, temporary_uploads

PREFIX = "media://"
# Route-specific limits from the providers' public documentation. Replicate's
# 100 MB figure applies to its SDK/Files API, while this module sends Data URLs;
# Replicate recommends Data URLs only for files smaller than 1 MB.
REPLICATE_INLINE_BYTES = 1_000_000 - 1
XAI_IMAGE_BYTES = 20 * 1024 * 1024
# BytePlus documents Seedance input images as strictly smaller than 30 MB.
SEEDANCE_IMAGE_BYTES = 30_000_000 - 1
FRESH_URL_SECONDS = 40 * 60


class TemporaryUploadConsentRequired(ValueError):
    """Raised before any Cloudinary write when an image exceeds the inline budget."""

    def __init__(self, *, size_bytes: int, budget_bytes: int):
        self.size_bytes = int(size_bytes)
        self.budget_bytes = int(budget_bytes)
        super().__init__(
            f"图片处理后大小为 {self.size_bytes / 1024:.0f} KiB，超过当前路线的 "
            f"{self.budget_bytes / 1024:.0f} KiB 直传预算。需要你确认后才能临时上传到 Cloudinary。"
        )


def media_reference(record):
    return PREFIX + record.id


def _can_reuse_url(record):
    url = record.image_url_string or ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    # Delivery URLs can expire while a downstream model is queued. Leave a
    # generous margin on fresh generated URLs; durable Cloudinary URLs may be older.
    if parsed.hostname != "res.cloudinary.com":
        try:
            age = time.time() - datetime.fromisoformat(record.created_at).timestamp()
        except (ValueError, TypeError):
            return False
        if age < 0 or age >= FRESH_URL_SECONDS:
            return False
    probe_start = time.monotonic()
    try:
        response = requests.get(url, stream=True, timeout=(5, 10), allow_redirects=True)
        try:
            ok = response.status_code == 200 and response.headers.get("Content-Type", "").lower().startswith("image/")
            debug_log(
                f"[ImageReference] URL 探测 {record.id} 耗时 "
                f"{time.monotonic() - probe_start:.2f}s，结果：{ok}",
            )
            return ok
        finally:
            response.close()
    except requests.RequestException:
        debug_log(
            f"[ImageReference] URL 探测 {record.id} 耗时 "
            f"{time.monotonic() - probe_start:.2f}s，结果：网络异常，不删除本地副本",
        )
        return False  # A failed probe never deletes the local copy.


def _video_copy(data: bytes, budget: int):
    compress_start = time.monotonic()
    original_size = len(data)
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original)
        if len(data) <= budget and max(image.size) <= 1680:
            debug_log(
                f"[ImageReference] 图片压缩跳过（{original_size} 字节在预算内），"
                f"耗时 {time.monotonic() - compress_start:.2f}s",
            )
            return data, image_mime(data)
        image.thumbnail((1680, 1680), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", image.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            image = rgb
        else:
            image = image.convert("RGB")
        # Keep useful detail. Oversized copies go to temporary hosting instead
        # of repeatedly shrinking until their quality is unusable.
        for quality in (90, 82):
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            candidate = output.getvalue()
            if len(candidate) <= budget:
                debug_log(
                    f"[ImageReference] 图片压缩 {original_size} -> {len(candidate)} 字节"
                    f"（quality={quality}），耗时 {time.monotonic() - compress_start:.2f}s",
                )
                return candidate, "image/jpeg"
        debug_log(
            f"[ImageReference] 图片压缩 {original_size} -> {len(candidate)} 字节"
            f"（仍超预算，将转托管），耗时 {time.monotonic() - compress_start:.2f}s",
        )
        return candidate, "image/jpeg"


def _inline_budget(*, provider, purpose):
    if purpose == "video":
        return REPLICATE_INLINE_BYTES if provider == "wan22Fast" else SEEDANCE_IMAGE_BYTES
    if provider in {"grok", "grokPro", "grokpro2k"}:
        return XAI_IMAGE_BYTES
    return REPLICATE_INLINE_BYTES


def _prepare_input(record, *, provider, purpose="image", image_bytes=None):
    if not media_store.available(record):
        raise ValueError("所选图片已过期、被删除或本地文件已丢失，请重新选择或导入。")
    if _can_reuse_url(record):
        debug_log("[ImageReference] original URL", record.id)
        return record.image_url_string
    if image_bytes is None:
        read_start = time.monotonic()
        source = "caller"
        if media_store.local_path(record):
            image_bytes = media_store.read_bytes(record)
            source = "local_file"
        elif record.image_data_base64:
            raw = record.image_data_base64
            image_bytes = decode_data_url(raw) if raw.startswith("data:") else base64.b64decode(raw, validate=True)
            source = "base64"
        elif record.image_url_string:
            from app.api.media_clients import download_image_bytes
            image_bytes = download_image_bytes(record.image_url_string)
            source = "remote_url"
        else:
            raise ValueError("没有找到所选图片的本地副本，请重新导入图片。")
        debug_log(
            f"[ImageReference] 本地读取 {record.id}（来源：{source}）"
            f"耗时 {time.monotonic() - read_start:.2f}s，{len(image_bytes)} 字节",
        )
    mime = image_mime(image_bytes)
    budget = _inline_budget(provider=provider, purpose=purpose)
    if purpose == "video":
        image_bytes, mime = _video_copy(image_bytes, budget)
    return image_bytes, mime, budget


def temporary_upload_requirement(record, *, provider, purpose="image", image_bytes=None):
    """Return an oversized-input description without uploading anything."""
    prepared = _prepare_input(
        record, provider=provider, purpose=purpose, image_bytes=image_bytes,
    )
    if isinstance(prepared, str):
        return None
    prepared_bytes, _mime, budget = prepared
    if len(prepared_bytes) <= budget:
        return None
    return {
        "record_id": record.id,
        "size_bytes": len(prepared_bytes),
        "budget_bytes": budget,
    }


def resolve_record(
    record, *, provider, purpose="image", image_bytes=None,
    allow_temporary_upload=False,
):
    prepared = _prepare_input(
        record, provider=provider, purpose=purpose, image_bytes=image_bytes,
    )
    if isinstance(prepared, str):
        return prepared
    image_bytes, mime, budget = prepared
    if len(image_bytes) > budget:
        if not allow_temporary_upload:
            raise TemporaryUploadConsentRequired(
                size_bytes=len(image_bytes), budget_bytes=budget,
            )
        return temporary_uploads.upload(image_bytes, mime)
    debug_log("[ImageReference] inline", record.id, mime, len(image_bytes))
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


def _record_for_reference(reference):
    record = GeneratedImageRecord(id=reference[len(PREFIX):], provider="local", prompt="")
    meta = media_store.info(record)
    record.image_url_string = meta.get("source_url")
    record.created_at = meta.get("source_created_at", "")
    return record


def temporary_upload_requirements(references, *, provider):
    """Preflight local references so the UI can ask before a Cloudinary upload."""
    requirements = []
    for reference in references:
        if not reference.startswith(PREFIX):
            continue
        requirement = temporary_upload_requirement(
            _record_for_reference(reference), provider=provider,
        )
        if requirement is not None:
            requirements.append(requirement)
    return requirements


def resolve_inputs(references, *, provider, allow_temporary_upload=False):
    """Return transport inputs and small durable references separately."""
    transport, durable = [], []
    for reference in references:
        if reference.startswith(PREFIX):
            record = _record_for_reference(reference)
            transport.append(resolve_record(
                record, provider=provider,
                allow_temporary_upload=allow_temporary_upload,
            ))
            durable.append(reference)
        elif reference.startswith("data:"):
            record = media_store.import_image(decode_data_url(reference), "参考图")
            transport.append(resolve_record(
                record, provider=provider,
                allow_temporary_upload=allow_temporary_upload,
            ))
            durable.append(media_reference(record))
        else:
            transport.append(validate_image_reference(reference))
            durable.append(reference)
    return transport, durable
