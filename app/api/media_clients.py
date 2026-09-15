import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin, urlsplit

import requests

from app.config import (
    CloudinaryConfig,
    OpenRouterConfig,
    ReplicateConfig,
    XAIConfig,
    debug_log,
    mask_authorization_header,
)
from app.video_models import VIDEO_MODELS, WAN_DURATION_FRAMES, WAN_FPS, WAN_MODEL, validate_video_duration


IMAGE_ASPECT_RATIO = "1:1"
IMAGE_ASPECT_RATIOS = ("3:4", "4:3", "1:1")
ANILLUSTRIOUS_PROVIDER = "anillustrious-v4"
ANILLUSTRIOUS_VERSION = (
    "aisha-ai-official/anillustrious-v4:"
    "80441e2c32a55f2fcf9b77fa0a74c6c86ad7deac51eed722b9faedb253265cb4"
)
ANILLUSTRIOUS_DIMENSIONS = {
    "3:4": (768, 1024),
    "4:3": (1024, 768),
    "1:1": (1024, 1024),
}
WAI_ILLUSTRIOUS_PROVIDER = "wai-nsfw-illustrious-v12"
WAI_ILLUSTRIOUS_VERSION = (
    "aisha-ai-official/wai-nsfw-illustrious-v12:"
    "0fc0fa9885b284901a6f9c0b4d67701fd7647d157b88371427d63f8089ce140e"
)
IMAGE_GENERATION_MAX_ATTEMPTS = 3
IMAGE_GENERATION_RETRY_INTERVAL_SECONDS = 3


@dataclass
class GeneratedImageResult:
    image_url: Optional[str] = None
    image_data_base64: Optional[str] = None


class ReplicateTerminalPredictionError(RuntimeError):
    """A prediction reached a terminal state without a usable image."""


def validate_http_response(response):
    if 200 <= response.status_code < 300:
        return

    text = response.text or ""
    raise RuntimeError("HTTP " + str(response.status_code) + ": " + text)


def parse_image_response(data):
    if not isinstance(data, dict):
        raise RuntimeError("图片生成响应不是 JSON 对象。")

    if isinstance(data.get("data"), list) and data["data"]:
        first = data["data"][0]

        if isinstance(first, dict):
            if first.get("url"):
                return GeneratedImageResult(
                    image_url=first.get("url"),
                    image_data_base64=None,
                )

            if first.get("b64_json"):
                return GeneratedImageResult(
                    image_url=None,
                    image_data_base64=first.get("b64_json"),
                )

    output = data.get("output")

    if isinstance(output, list) and output:
        first_output = output[0]

        if isinstance(first_output, str):
            return GeneratedImageResult(
                image_url=first_output,
                image_data_base64=None,
            )

    if isinstance(output, str):
        return GeneratedImageResult(
            image_url=output,
            image_data_base64=None,
        )

    if isinstance(data.get("b64_json"), str):
        return GeneratedImageResult(
            image_url=None,
            image_data_base64=data.get("b64_json"),
        )

    raise RuntimeError("未识别图片生成响应：" + str(data))


def download_image_bytes(url):
    response = requests.get(url, timeout=120)
    validate_http_response(response)
    content = response.content
    if not content:
        raise RuntimeError("服务商返回的图片文件为空。")
    return content


def download_url_as_base64(url):
    return base64.b64encode(download_image_bytes(url)).decode("ascii")


def image_url_is_expired(url):
    """Return true only when the server explicitly rejects the image URL."""
    parsed = urlsplit(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return True
    try:
        response = requests.get(
            url, timeout=(5, 15), stream=True, allow_redirects=True,
        )
    except requests.RequestException:
        # A temporary network failure must not delete a user's media record.
        return False
    try:
        return response.status_code in (401, 403, 404, 410)
    finally:
        response.close()

class GrokImageAPIClient:
    IMAGE_GENERATION_URL = "https://api.x.ai/v1/images/generations"
    IMAGE_EDIT_URL = "https://api.x.ai/v1/images/edits"

    @classmethod
    def generate_image(
        cls,
        prompt,
        image_urls=None,
        model="grok-imagine-image",
        resolution="2k",
        quality=None,
        aspect_ratio=IMAGE_ASPECT_RATIO,
    ):
        image_urls = image_urls or []
        image_urls = [url.strip() for url in image_urls if str(url).strip()]
        image_urls = image_urls[:5]

        effective_grok_image_api_key = XAIConfig.image_api_key()
        if not effective_grok_image_api_key:
            raise RuntimeError(
                "缺少 Grok 生图 API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 GROK_IMAGE_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_grok_image_api_key,
        }

        if not image_urls:
            url = cls.IMAGE_GENERATION_URL
            body = {
                "model": model,
                "prompt": prompt,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
            }
            if quality:
                body["quality"] = quality
        else:
            url = cls.IMAGE_EDIT_URL

            if len(image_urls) == 1:
                body = {
                    "model": model,
                    "prompt": prompt,
                    "resolution": resolution,
                    "aspect_ratio": aspect_ratio,
                    "image": {
                        "url": image_urls[0],
                        "type": "image_url",
                    },
                }
                if quality:
                    body["quality"] = quality
            else:
                body = {
                    "model": model,
                    "prompt": prompt,
                    "resolution": resolution,
                    "aspect_ratio": aspect_ratio,
                    "images": [
                        {
                            "url": item,
                            "type": "image_url",
                        }
                        for item in image_urls
                    ],
                }
                if quality:
                    body["quality"] = quality

        debug_log("====== Grok Image Request ======")
        debug_log("URL:", url)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", body)

        for attempt in range(1, IMAGE_GENERATION_MAX_ATTEMPTS + 1):
            response = requests.post(
                url,
                headers=headers,
                json=body,
                timeout=3600,
            )

            debug_log("====== Grok Image HTTP Status ======")
            debug_log(response.status_code)
            debug_log("====== Grok Image Raw Response ======")
            debug_log(response.text)

            try:
                validate_http_response(response)
                return parse_image_response(response.json())
            except (RuntimeError, ValueError) as exc:
                if attempt >= IMAGE_GENERATION_MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Grok 连续 {IMAGE_GENERATION_MAX_ATTEMPTS} 次未返回图片。"
                        f"最后错误：{exc}"
                    ) from exc
                debug_log(
                    "[Grok Image] retry",
                    attempt + 1,
                    "of",
                    IMAGE_GENERATION_MAX_ATTEMPTS,
                    "after:",
                    str(exc),
                )
                time.sleep(IMAGE_GENERATION_RETRY_INTERVAL_SECONDS)

        raise RuntimeError("Grok 未返回图片。")


class ReplicateImageAPIClient:
    POLL_INTERVAL_SECONDS = 3
    POLL_TIMEOUT_SECONDS = 1200
    MAX_POLL_ERRORS = 5

    MODELS = {
        "flux": "black-forest-labs/flux-2-pro",
        "nanoPro": "google/nano-banana-pro",
        "nano": "google/nano-banana",
    }

    @classmethod
    def generate_image(
        cls,
        provider,
        prompt,
        image_urls=None,
        aspect_ratio=IMAGE_ASPECT_RATIO,
        refiner_prompt="",
    ):
        image_urls = image_urls or []
        image_urls = [url.strip() for url in image_urls if str(url).strip()]
        refiner_prompt = str(refiner_prompt or "").strip()

        versioned_providers = {
            ANILLUSTRIOUS_PROVIDER,
            WAI_ILLUSTRIOUS_PROVIDER,
        }
        if provider not in cls.MODELS and provider not in versioned_providers:
            raise ValueError("未知 Replicate provider: " + str(provider))
        if provider in versioned_providers:
            if image_urls:
                raise ValueError(f"{provider} 仅支持文生图，不能使用参考图片 URL。")
            if aspect_ratio not in ANILLUSTRIOUS_DIMENSIONS:
                raise ValueError(f"{provider} 图片尺寸请选择 3:4、4:3 或 1:1。")

        if provider in versioned_providers:
            url = "https://api.replicate.com/v1/predictions"
        else:
            model = cls.MODELS[provider]
            url = "https://api.replicate.com/v1/models/" + model + "/predictions"

        effective_replicate_api_token = ReplicateConfig.api_token()
        if not effective_replicate_api_token:
            raise RuntimeError(
                "缺少 Replicate API Token。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 REPLICATE_API_TOKEN。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_replicate_api_token,
            "Prefer": "wait",
        }

        if provider == "flux":
            input_body = {
                "prompt": prompt,
                "resolution": "1 MP",
                "aspect_ratio": aspect_ratio,
                "input_images": image_urls,
                "output_format": "jpg",
                "output_quality": 80,
                "safety_tolerance": 5,
            }
        elif provider == "nanoPro":
            input_body = {
                "prompt": prompt,
                "resolution": "2K",
                "image_input": image_urls,
                "aspect_ratio": aspect_ratio,
                "output_format": "png",
                "safety_filter_level": "block_only_high",
                "allow_fallback_model": False,
            }
        elif provider == ANILLUSTRIOUS_PROVIDER:
            width, height = ANILLUSTRIOUS_DIMENSIONS[aspect_ratio]
            input_body = {
                "steps": 40,
                "prompt": prompt,
                "width": width,
                "height": height,
                "scheduler": "DPM++ 2M SDE Karras",
                "refiner": bool(refiner_prompt),
                "refiner_strength": 0.4,
                "prompt_conjunction": True,
            }
            if refiner_prompt:
                input_body["refiner_prompt"] = refiner_prompt
        elif provider == WAI_ILLUSTRIOUS_PROVIDER:
            width, height = ANILLUSTRIOUS_DIMENSIONS[aspect_ratio]
            input_body = {
                "batch_size": 1,
                "cfg_scale": 5.86,
                "clip_skip": 2,
                "guidance_rescale": 1,
                "height": height,
                "model": "WAI-NSFW-Illustrious-SDXL-v12",
                "pag_scale": 0,
                "prepend_preprompt": True,
                "prompt": prompt,
                "scheduler": "DPM++ 2M SDE Karras",
                "seed": -1,
                "steps": 40,
                "vae": "WAI-NSFW-Illustrious-SDXL-v12",
                "width": width,
            }
        else:
            input_body = {
                "prompt": prompt,
                "image_input": image_urls,
                "aspect_ratio": aspect_ratio,
                "output_format": "jpg",
            }

        body = {"input": input_body}
        if provider == ANILLUSTRIOUS_PROVIDER:
            body["version"] = ANILLUSTRIOUS_VERSION
        elif provider == WAI_ILLUSTRIOUS_PROVIDER:
            body["version"] = WAI_ILLUSTRIOUS_VERSION

        debug_log("====== Replicate Image Request ======")
        debug_log("URL:", url)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", body)

        max_attempts = IMAGE_GENERATION_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=(15, 90),
                )
            except requests.RequestException as exc:
                raise RuntimeError(
                    "Replicate 生图请求连接中断，无法确认任务是否已创建。"
                    "请先到 Replicate 查看任务记录，避免重复提交。"
                ) from exc

            debug_log("====== Replicate Image HTTP Status ======")
            debug_log(response.status_code)
            debug_log("====== Replicate Image Raw Response ======")
            debug_log(response.text)

            try:
                validate_http_response(response)
            except RuntimeError as exc:
                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"{provider} 连续 {max_attempts} 次未返回图片。"
                        f"最后错误：{exc}"
                    ) from exc
                debug_log(
                    "[Replicate Image] retry",
                    attempt + 1,
                    "of",
                    max_attempts,
                    "after:",
                    str(exc),
                )
                time.sleep(cls.POLL_INTERVAL_SECONDS)
                continue

            try:
                return cls._wait_for_image(
                    response.json(), effective_replicate_api_token
                )
            except ReplicateTerminalPredictionError as exc:
                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"{provider} 连续 {max_attempts} 次未返回图片。"
                        f"最后错误：{exc}"
                    ) from exc
                debug_log(
                    "[Replicate Image] retry",
                    attempt + 1,
                    "of",
                    max_attempts,
                    "after:",
                    str(exc),
                )
                time.sleep(cls.POLL_INTERVAL_SECONDS)

        raise RuntimeError(f"{provider} 未返回图片。")

    @classmethod
    def _wait_for_image(cls, data, api_token):
        """Resolve both synchronous outputs and accepted asynchronous predictions."""
        if not isinstance(data, dict):
            raise RuntimeError("Replicate 生图响应不是 JSON 对象。")
        prediction_id = str(data.get("id") or "").strip()
        encoded_id = quote(prediction_id, safe="")
        task_hint = (
            f"任务 {prediction_id}：https://replicate.com/p/{encoded_id}"
            if prediction_id else "响应未提供任务 ID。"
        )
        poll_url = "https://api.replicate.com/v1/predictions/" + encoded_id
        headers = {"Authorization": "Bearer " + api_token}
        deadline = time.monotonic() + cls.POLL_TIMEOUT_SECONDS
        consecutive_errors = 0
        delay = cls.POLL_INTERVAL_SECONDS
        last_status = "unknown"

        while True:
            if not isinstance(data, dict):
                raise RuntimeError("Replicate 查询响应不是 JSON 对象。" + task_hint)
            status = str(data.get("status") or "").lower()
            last_status = status or last_status
            debug_log(
                "[Replicate Image]", prediction_id, "status:", last_status,
                "has_output:", bool(data.get("output")),
            )
            if status in {"failed", "error", "canceled", "cancelled", "aborted"}:
                reason = str(data.get("error") or status)
                raise ReplicateTerminalPredictionError(
                    f"Replicate 生图任务未成功（{status}）：{reason}。{task_hint}"
                )

            # Sync mode can return ready file outputs while status is processing.
            if data.get("output"):
                result = parse_image_response(data)
                if str(result.image_url or "").strip():
                    return result
            if status == "succeeded":
                raise ReplicateTerminalPredictionError(
                    "Replicate 生图任务已结束，但未返回可用图片。" + task_hint
                )
            if status not in {"starting", "processing"}:
                raise RuntimeError(f"Replicate 生图任务状态无法识别：{status or '空'}。{task_hint}")
            if not prediction_id:
                raise RuntimeError("Replicate 生图任务尚未完成，但缺少任务 ID，无法继续查询。")

            # Retry only GETs for this prediction, never the billable POST.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"等待 Replicate 生图超时，最后状态：{last_status}。"
                        "远端任务可能仍在执行，可打开任务链接查看结果。" + task_hint
                    )
                time.sleep(min(delay, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                try:
                    response = requests.get(
                        poll_url,
                        headers=headers,
                        timeout=(min(10, remaining), min(60, remaining)),
                        allow_redirects=False,
                    )
                except (requests.ConnectionError, requests.Timeout,
                        requests.exceptions.ChunkedEncodingError) as exc:
                    retry_reason = type(exc).__name__
                    response = None
                else:
                    debug_log("====== Replicate Image Poll HTTP Status ======")
                    debug_log(response.status_code)
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        retry_reason = f"HTTP {response.status_code}"
                    else:
                        try:
                            validate_http_response(response)
                            data = response.json()
                        except (RuntimeError, ValueError) as exc:
                            raise RuntimeError(f"Replicate 生图查询失败：{exc}。{task_hint}") from exc
                        consecutive_errors = 0
                        delay = cls.POLL_INTERVAL_SECONDS
                        break

                consecutive_errors += 1
                debug_log("[Replicate Image] 查询重试", prediction_id,
                          consecutive_errors, retry_reason)
                if consecutive_errors >= cls.MAX_POLL_ERRORS:
                    raise RuntimeError(
                        f"Replicate 生图连续查询失败：{retry_reason}。"
                        "远端任务可能仍在执行，可打开任务链接查看结果。" + task_hint
                    )
                delay = min(30, cls.POLL_INTERVAL_SECONDS * 2 ** consecutive_errors)
                if response is not None:
                    try:
                        delay = max(delay, min(60, float(response.headers.get("Retry-After", 0))))
                    except (TypeError, ValueError):
                        pass


# 兼容旧名字：如果其他文件还 import ReplicateImageClient，也不会报错
ReplicateImageClient = ReplicateImageAPIClient


class ReplicateVideoAPIClient:
    MODEL = "wan-video/wan-2.2-i2v-fast"
    CREATE_URL = "https://api.replicate.com/v1/models/" + MODEL + "/predictions"

    @staticmethod
    def _extract_video_url(data):
        if not isinstance(data, dict):
            return None

        output = data.get("output")
        if isinstance(output, str) and output.startswith("http"):
            return output
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
        return None

    @classmethod
    def generate_wan_2_2_i2v_fast(cls, image_url, prompt, seconds=5):
        validate_video_duration(WAN_MODEL, seconds)
        image_url = str(image_url or "").strip()
        prompt = str(prompt or "").strip()

        if not image_url:
            raise ValueError("Wan 2.2 I2V Fast 需要可公开访问的输入图片 URL。")

        effective_replicate_api_token = ReplicateConfig.api_token()
        if not effective_replicate_api_token:
            raise RuntimeError(
                "缺少 Replicate API Token。请在 APIkey 页面填写，"
                "或在 Streamlit Secrets 配置 REPLICATE_API_TOKEN。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_replicate_api_token,
        }
        body = {
            "input": {
                "image": image_url,
                "prompt": prompt,
                "go_fast": True,
                "num_frames": WAN_DURATION_FRAMES[seconds],
                "resolution": "720p",
                "sample_shift": 12,
                "frames_per_second": WAN_FPS,
                "interpolate_output": False,
                "lora_scale_transformer": 1,
                "lora_scale_transformer_2": 1,
            }
        }

        debug_log("====== Replicate Wan 2.2 I2V Fast Request ======")
        debug_log("URL:", cls.CREATE_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", body)

        try:
            response = requests.post(
                cls.CREATE_URL,
                headers=headers,
                json=body,
                timeout=(15, 60),
            )
        except requests.RequestException as exc:
            debug_log("====== Replicate Wan 2.2 I2V Fast Request Exception ======")
            debug_log(repr(exc))
            raise RuntimeError(
                "连接 Replicate API 失败：" + repr(exc)
            ) from exc

        debug_log("====== Replicate Wan 2.2 I2V Fast HTTP Status ======")
        debug_log(response.status_code)
        debug_log("====== Replicate Wan 2.2 I2V Fast Raw Response ======")
        debug_log(response.text)

        validate_http_response(response)
        data = response.json()

        video_url = cls._extract_video_url(data)
        if video_url:
            return video_url

        status = str(data.get("status") or "").lower()
        if status in ["failed", "error", "canceled", "cancelled"]:
            raise RuntimeError("Replicate Wan 视频任务失败：" + str(data))
        if status == "succeeded":
            raise RuntimeError("Replicate Wan 视频任务未返回视频 URL：" + str(data))

        urls = data.get("urls") if isinstance(data.get("urls"), dict) else {}
        poll_url = str(urls.get("get") or "").strip()
        prediction_id = str(data.get("id") or "").strip()
        if not poll_url and prediction_id:
            poll_url = "https://api.replicate.com/v1/predictions/" + prediction_id
        if not poll_url:
            raise RuntimeError("Replicate Wan 响应缺少 prediction URL：" + str(data))

        poll_headers = {
            "Authorization": "Bearer " + effective_replicate_api_token,
        }
        for _ in range(200):
            poll_response = requests.get(
                poll_url,
                headers=poll_headers,
                timeout=120,
            )

            debug_log("====== Replicate Wan 2.2 I2V Fast Poll HTTP Status ======")
            debug_log(poll_response.status_code)
            debug_log("====== Replicate Wan 2.2 I2V Fast Poll Raw Response ======")
            debug_log(poll_response.text)

            validate_http_response(poll_response)
            poll_data = poll_response.json()
            video_url = cls._extract_video_url(poll_data)
            if video_url:
                return video_url

            status = str(poll_data.get("status") or "").lower()
            if status in ["failed", "error", "canceled", "cancelled"]:
                raise RuntimeError("Replicate Wan 视频任务失败：" + str(poll_data))
            if status == "succeeded":
                raise RuntimeError("Replicate Wan 视频任务未返回视频 URL：" + str(poll_data))

            time.sleep(3)

        raise TimeoutError("Replicate Wan 2.2 I2V Fast 视频任务超时。")


class OpenRouterVideoAPIClient:
    CREATE_URL = "https://openrouter.ai/api/v1/videos"
    POLL_INTERVAL_SECONDS = 10
    POLL_TIMEOUT_SECONDS = 1800
    MAX_POLL_ERRORS = 5

    @classmethod
    def generate_video(cls, *, model, image_url, prompt, seconds, aspect_ratio, generate_audio=True):
        validate_video_duration(model, seconds)
        if model == WAN_MODEL:
            raise ValueError("Wan 视频请使用 Replicate API。")
        if aspect_ratio not in VIDEO_MODELS[model].aspect_ratios:
            raise ValueError("当前视频模型不支持此画面比例。")
        if urlsplit(str(image_url)).scheme not in ("http", "https"):
            raise ValueError("Seedance 图生视频需要可访问的图片 URL。")
        api_key = OpenRouterConfig.api_key()
        if not api_key:
            raise RuntimeError("缺少 OpenRouter API Key，请在 APIkey 页面填写或配置 OPENROUTER_API_KEY。")
        headers = {"Authorization": "Bearer " + api_key}
        body = {
            "model": model,
            "prompt": prompt,
            "duration": int(seconds),
            "resolution": "720p",
            "aspect_ratio": aspect_ratio,
            "generate_audio": bool(generate_audio),
            "frame_images": [{
                "type": "image_url",
                "image_url": {"url": image_url},
                "frame_type": "first_frame",
            }],
        }
        try:
            response = requests.post(cls.CREATE_URL, headers=headers, json=body, timeout=(15, 60))
        except requests.RequestException as exc:
            # A timed-out POST may already have created a paid job. Never resubmit it here.
            raise RuntimeError("OpenRouter 连接中断，无法确认任务是否已创建，请先查看 OpenRouter 任务记录。") from exc
        validate_http_response(response)
        data = response.json()
        task_id = str(data.get("id") or "").strip()
        if not task_id:
            raise RuntimeError("OpenRouter 未返回视频任务 ID。")
        # Construct authenticated URLs ourselves instead of trusting arbitrary response URLs.
        poll_url = cls.CREATE_URL + "/" + quote(task_id, safe="")
        deadline = time.monotonic() + cls.POLL_TIMEOUT_SECONDS
        errors = 0
        while True:
            status = str(data.get("status") or "").lower()
            if status == "completed":
                return poll_url + "/content?index=0"
            if status in ("failed", "cancelled", "canceled", "expired"):
                raise RuntimeError(f"OpenRouter 视频任务 {status}：{data.get('error') or '生成失败'}；任务：{task_id}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"OpenRouter 视频等待超时，请保留任务地址查询结果：{poll_url}")
            time.sleep(min(cls.POLL_INTERVAL_SECONDS, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            try:
                response = requests.get(poll_url, headers=headers, timeout=(15, min(60, remaining)))
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.ConnectionError(f"HTTP {response.status_code}")
            except requests.RequestException as exc:
                errors += 1
                if errors >= cls.MAX_POLL_ERRORS:
                    raise RuntimeError(f"OpenRouter 连续查询失败，任务可能仍在运行：{poll_url}") from exc
                continue
            validate_http_response(response)
            data = response.json()
            errors = 0


def is_openrouter_video_url(url):
    parsed = urlsplit(str(url or ""))
    return (
        parsed.scheme == "https"
        and parsed.netloc == "openrouter.ai"
        and parsed.path.startswith("/api/v1/videos/")
        and parsed.path.endswith("/content")
    )


def download_video_bytes(url, *, api_key=""):
    """Fetch only for an explicit preview/download, in memory; never persist a video."""
    headers = {}
    if is_openrouter_video_url(url):
        if not api_key:
            raise RuntimeError("播放或下载此视频需要 OpenRouter API Key，请在 APIkey 页面填写。")
        headers["Authorization"] = "Bearer " + api_key
    elif urlsplit(str(url)).hostname == "openrouter.ai":
        raise ValueError("不是有效的 OpenRouter 视频下载地址。")
    current_url = url
    for _ in range(6):
        if urlsplit(str(current_url)).scheme not in ("http", "https"):
            raise ValueError("视频下载地址必须是 HTTP 或 HTTPS URL。")
        response = requests.get(
            current_url, headers=headers, timeout=(15, 120), stream=True, allow_redirects=False,
        )
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("视频下载重定向缺少目标地址。")
                current_url = urljoin(current_url, location)
                # Redirects target signed delivery URLs; do not forward the API key.
                headers = {}
                continue
            if response.status_code in (404, 410):
                raise RuntimeError("视频链接已失效或视频已被服务商清理。")
            validate_http_response(response)
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/" in content_type or "json" in content_type:
                raise RuntimeError("服务商返回的不是视频文件。")
            chunks = bytearray()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                chunks.extend(chunk)
                if len(chunks) > 256 * 1024 * 1024:
                    raise RuntimeError("视频超过 256 MB，无法在当前播放器中加载。")
            if not chunks:
                raise RuntimeError("服务商返回的视频文件为空。")
            return bytes(chunks)
        finally:
            response.close()
    raise RuntimeError("视频下载重定向次数过多。")


class CloudinaryUploader:
    @staticmethod
    def _upload_files(files):
        cloud_name = CloudinaryConfig.cloud_name()
        effective_cloudinary_api_key = CloudinaryConfig.api_key()
        cloudinary_api_secret = CloudinaryConfig.api_secret()

        if not effective_cloudinary_api_key:
            raise RuntimeError(
                "缺少 Cloudinary API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 CLOUDINARY_API_KEY。"
            )

        if not cloudinary_api_secret:
            raise RuntimeError(
                "缺少 Cloudinary API Secret。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 CLOUDINARY_API_SECRET。"
            )

        url = "https://api.cloudinary.com/v1_1/" + cloud_name + "/image/upload"

        response = requests.post(
            url,
            files=files,
            auth=(effective_cloudinary_api_key, cloudinary_api_secret),
            timeout=3600,
        )

        debug_log("====== Cloudinary Upload HTTP Status ======")
        debug_log(response.status_code)
        debug_log("====== Cloudinary Upload Raw Response ======")
        debug_log(response.text)

        validate_http_response(response)

        data = response.json()
        secure_url = data.get("secure_url")

        if not secure_url:
            raise RuntimeError("Cloudinary 未返回 secure_url: " + str(data))

        return secure_url

    @classmethod
    def upload_image(cls, file_path):
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(str(file_path))

        with file_path.open("rb") as file:
            return cls._upload_files({"file": file})

    @classmethod
    def upload_image_bytes(cls, image_bytes, filename="image.png"):
        if not image_bytes:
            raise ValueError("图片数据不能为空")

        return cls._upload_files({"file": (filename, image_bytes)})
