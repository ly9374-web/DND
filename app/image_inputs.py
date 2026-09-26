"""Validation shared by HTTP clients; data URLs are request-only values."""
import base64
import binascii
import io
from urllib.parse import urlsplit

from PIL import Image

MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}


def image_mime(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        mime = MIME_TYPES.get(image.format)
        image.verify()
    if mime is None:
        raise ValueError("图片仅支持 PNG、JPEG、WebP 或 GIF。")
    return mime


def decode_data_url(value: str) -> bytes:
    header, separator, encoded = value.partition(",")
    if not separator or header not in {f"data:{mime};base64" for mime in MIME_TYPES.values()}:
        raise ValueError("图片 Data URL 格式无效。")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("图片 Base64 数据无效。") from exc
    if not data or image_mime(data) != header[5:-7]:
        raise ValueError("图片内容与声明的格式不一致。")
    return data


def validate_image_reference(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("data:"):
        decode_data_url(value)
    else:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("图片需要 HTTP/HTTPS URL 或有效的图片 Data URL。")
    return value
