"""Deduplicated Cloudinary fallback, isolated from permanent URL favorites.

Only assets recorded by this module in its own installation namespace are deleted.
The 48-hour lease is renewed whenever an image is reused; ambiguous upload results
remain in the manifest so maintenance can remove them too.
"""
import hashlib
import json
import threading
import time
import uuid

import requests

from app.config import CloudinaryConfig, DATA_DIR, debug_log
from app.api.media_clients import IMAGE_SUBMISSION_TIMEOUT, validate_http_response

DIRECTORY = DATA_DIR / "temporary_uploads"
RETENTION_SECONDS = 48 * 60 * 60
_lock = threading.RLock()


def _write(path, meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _namespace():
    path = DIRECTORY / "namespace.json"
    if not path.exists():
        _write(path, {"id": uuid.uuid4().hex})
    value = json.loads(path.read_text(encoding="utf-8"))["id"]
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("临时上传索引无效。")
    return "dnd-temp/" + value + "/"


def _request(action, parameters, *, files=None):
    key, secret = CloudinaryConfig.api_key(), CloudinaryConfig.api_secret()
    if not key or not secret:
        raise ValueError("当前传图需要 Cloudinary 临时上传，但缺少 Key 或 Secret。请配置后重试，或关闭兼容上传并使用较小图片。")
    parameters = {**parameters, "timestamp": str(int(time.time()))}
    signed = "&".join(f"{k}={parameters[k]}" for k in sorted(parameters)) + secret
    parameters.update(api_key=key, signature=hashlib.sha256(signed.encode()).hexdigest())
    request_start = time.monotonic()
    response = requests.post(
        f"https://api.cloudinary.com/v1_1/{CloudinaryConfig.cloud_name()}/image/{action}",
        data=parameters, files=files,
        timeout=IMAGE_SUBMISSION_TIMEOUT if action == "upload" else (15, 120),
    )
    try:
        validate_http_response(response)
        data = response.json()
        debug_log(
            f"[ImageReference] 临时上传 请求发送+接口响应耗时 "
            f"{time.monotonic() - request_start:.2f}s",
        )
        return data
    finally:
        response.close()


def upload(data: bytes, mime: str) -> str:
    cloud = CloudinaryConfig.cloud_name()
    digest = hashlib.sha256(data).hexdigest()
    cache_key = hashlib.sha256((cloud + ":" + digest).encode()).hexdigest()
    with _lock:
        path = DIRECTORY / (cache_key + ".json")
        meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        now = time.time()
        if meta.get("url") and meta.get("expires_at", 0) > now:
            _write(path, {**meta, "expires_at": now + RETENTION_SECONDS})
            return meta["url"]
        public_id = _namespace() + digest
        meta = {"cloud": cloud, "public_id": public_id, "expires_at": now + RETENTION_SECONDS}
        _write(path, meta)  # Track even an upload whose response times out.
        result = _request("upload", {"public_id": public_id, "overwrite": "false", "tags": "dnd-temporary-input"},
                          files={"file": ("reference." + mime.split("/")[1], data, mime)})
        url = result.get("secure_url")
        if not url or result.get("public_id") != public_id:
            raise RuntimeError("临时上传未返回匹配的图片资源。")
        _write(path, {**meta, "url": url, "asset_id": result.get("asset_id")})
        debug_log("[ImageReference] temporary upload", digest[:12], len(data))
        return url


def cleanup():
    with _lock:
        if not DIRECTORY.exists():
            return
        prefix = _namespace()
        cloud = CloudinaryConfig.cloud_name()
        for path in DIRECTORY.glob("*.json"):
            if path.name == "namespace.json" or path.is_symlink():
                continue
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
                public_id = meta.get("public_id", "")
                suffix = public_id.removeprefix(prefix)
                if (meta.get("cloud") != cloud or not public_id.startswith(prefix)
                        or len(suffix) != 64 or any(c not in "0123456789abcdef" for c in suffix)
                        or meta.get("expires_at", float("inf")) > time.time()):
                    continue
                result = _request("destroy", {"public_id": public_id, "invalidate": "true"})
                if result.get("result") in {"ok", "not found"}:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                debug_log("[TemporaryUpload] cleanup will retry", type(exc).__name__)
