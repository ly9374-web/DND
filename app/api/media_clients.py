import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from app.config import (
    CloudinaryConfig,
    DomoAIConfig,
    ReplicateConfig,
    XAIConfig,
    ZhipuConfig,
    debug_log,
    mask_authorization_header,
)


IMAGE_ASPECT_RATIO = "3:4"


@dataclass
class GeneratedImageResult:
    image_url: Optional[str] = None
    image_data_base64: Optional[str] = None


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


def download_url_as_base64(url):
    response = requests.get(url, timeout=120)
    validate_http_response(response)
    return base64.b64encode(response.content).decode("ascii")


def try_download_url_as_base64(url):
    try:
        return download_url_as_base64(url)
    except Exception as exc:
        debug_log("Failed to cache image URL as base64:", str(exc))
        return None


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
                "aspect_ratio": IMAGE_ASPECT_RATIO,
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
                    "aspect_ratio": IMAGE_ASPECT_RATIO,
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
                    "aspect_ratio": IMAGE_ASPECT_RATIO,
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

        validate_http_response(response)

        return parse_image_response(response.json())


class ReplicateImageAPIClient:
    MODELS = {
        "flux": "black-forest-labs/flux-2-pro",
        "nanoPro": "google/nano-banana-pro",
        "nano": "google/nano-banana",
    }

    @classmethod
    def generate_image(cls, provider, prompt, image_urls=None):
        image_urls = image_urls or []
        image_urls = [url.strip() for url in image_urls if str(url).strip()]

        if provider not in cls.MODELS:
            raise ValueError("未知 Replicate provider: " + str(provider))

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
                "aspect_ratio": IMAGE_ASPECT_RATIO,
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
                "aspect_ratio": IMAGE_ASPECT_RATIO,
                "output_format": "png",
                "safety_filter_level": "block_only_high",
                "allow_fallback_model": False,
            }
        else:
            input_body = {
                "prompt": prompt,
                "image_input": image_urls,
                "aspect_ratio": IMAGE_ASPECT_RATIO,
                "output_format": "jpg",
            }

        body = {
            "input": input_body,
        }

        debug_log("====== Replicate Image Request ======")
        debug_log("URL:", url)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", body)

        response = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=3600,
        )

        debug_log("====== Replicate Image HTTP Status ======")
        debug_log(response.status_code)
        debug_log("====== Replicate Image Raw Response ======")
        debug_log(response.text)

        validate_http_response(response)

        return parse_image_response(response.json())


# 兼容旧名字：如果其他文件还 import ReplicateImageClient，也不会报错
ReplicateImageClient = ReplicateImageAPIClient


class DomoAIClient:
    CREATE_IMAGE_TO_VIDEO_URL = "https://api.domoai.com/v1/video/image2video"

    @staticmethod
    def _extract_video_url(data):
        if not isinstance(data, dict):
            return None

        direct = (
            data.get("video_url")
            or data.get("output_url")
            or data.get("url")
            or data.get("result")
        )

        if isinstance(direct, list) and direct:
            direct = direct[0]

        if isinstance(direct, str) and direct.startswith("http"):
            return direct

        output_videos = data.get("output_videos") or data.get("outputVideos")
        if isinstance(output_videos, list) and output_videos:
            first = output_videos[0]
            if isinstance(first, dict):
                nested_url = first.get("url") or first.get("video_url")
                if isinstance(nested_url, str) and nested_url.startswith("http"):
                    return nested_url
            if isinstance(first, str) and first.startswith("http"):
                return first

        return None

    @classmethod
    def create_image_to_video_task_with_base64(cls, image_base64, prompt, seconds):
        image_base64 = str(image_base64 or "").strip()
        prompt = str(prompt or "").strip()

        if not image_base64:
            raise ValueError("imageBase64 不能为空")

        try:
            seconds = int(seconds)
        except Exception:
            raise ValueError("seconds 必须是整数")

        if seconds < 1 or seconds > 10:
            raise ValueError("视频时长必须在 1 到 10 秒之间")

        effective_domoai_api_key = DomoAIConfig.api_key()
        if not effective_domoai_api_key:
            raise RuntimeError(
                "缺少 DomoAI API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 DOMOAI_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_domoai_api_key,
        }

        body = {
            "prompt": prompt,
            "model": "animate-2.4-faster",
            "image": {
                "bytes_base64_encoded": image_base64,
            },
            "seconds": seconds,
        }

        debug_log("====== DomoAI Image2Video Request ======")
        debug_log("URL:", cls.CREATE_IMAGE_TO_VIDEO_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))

        response = requests.post(
            cls.CREATE_IMAGE_TO_VIDEO_URL,
            headers=headers,
            json=body,
            timeout=3600,
        )

        debug_log("====== DomoAI Image2Video HTTP Status ======")
        debug_log(response.status_code)
        debug_log("====== DomoAI Image2Video Raw Response ======")
        debug_log(response.text)

        validate_http_response(response)

        data = response.json()

        code = data.get("code")
        if code not in (None, 0):
            raise RuntimeError("DomoAI error: " + str(data))

        data_object = data.get("data")

        if isinstance(data_object, dict):
            task_id = data_object.get("task_id") or data_object.get("id")
        else:
            task_id = data.get("task_id") or data.get("id")

        if not task_id:
            raise RuntimeError("Missing task_id: " + str(data))

        return str(task_id)

    @classmethod
    def poll_task_until_video_url(cls, task_id):
        task_id = str(task_id or "").strip()

        if not task_id:
            raise ValueError("taskID 不能为空")

        url = "https://api.domoai.com/v1/tasks/" + task_id

        effective_domoai_api_key = DomoAIConfig.api_key()
        if not effective_domoai_api_key:
            raise RuntimeError(
                "缺少 DomoAI API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 DOMOAI_API_KEY。"
            )

        headers = {
            "Authorization": "Bearer " + effective_domoai_api_key,
        }

        for _ in range(80):
            response = requests.get(
                url,
                headers=headers,
                timeout=3600,
            )

            debug_log("====== DomoAI Poll HTTP Status ======")
            debug_log(response.status_code)
            debug_log("====== DomoAI Poll Raw Response ======")
            debug_log(response.text)

            validate_http_response(response)

            data = response.json()

            data_object = data.get("data")

            if isinstance(data_object, dict):
                status = str(data_object.get("status") or "").lower()
                video_url = cls._extract_video_url(data_object)
            else:
                status = str(data.get("status") or "").lower()
                video_url = cls._extract_video_url(data)

            if isinstance(video_url, str) and video_url.startswith("http"):
                return video_url

            if status in ["failed", "error", "canceled", "cancelled"]:
                raise RuntimeError("DomoAI task failed: " + str(data))

            time.sleep(3)

        raise TimeoutError("DomoAI 视频任务超时。")


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
    def generate_wan_2_2_i2v_fast(cls, image_url, prompt):
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
                "num_frames": 81,
                "resolution": "720p",
                "sample_shift": 12,
                "frames_per_second": 16,
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


class ZhipuVideoClient:
    CREATE_VIDEO_URL = "https://open.bigmodel.cn/api/paas/v4/videos/generations"
    POLL_URL_PREFIX = "https://open.bigmodel.cn/api/paas/v4/async-result/"

    @staticmethod
    def _extract_video_url(data):
        if not isinstance(data, dict):
            return None

        direct = (
            data.get("video_url")
            or data.get("output")
            or data.get("result")
        )

        if isinstance(direct, list) and direct:
            direct = direct[0]

        if isinstance(direct, str) and direct.startswith("http"):
            return direct

        video_result = data.get("video_result") or data.get("videoResult")
        if isinstance(video_result, list) and video_result:
            first = video_result[0]
            if isinstance(first, dict):
                nested_url = first.get("url") or first.get("video_url")
                if isinstance(nested_url, str) and nested_url.startswith("http"):
                    return nested_url
            if isinstance(first, str) and first.startswith("http"):
                return first

        return None

    @classmethod
    def create_image_to_video_task(cls, image_url, prompt, seconds):
        image_url = str(image_url or "").strip()
        prompt = str(prompt or "").strip()

        if not image_url:
            raise ValueError("智谱图生视频需要图片 URL")

        try:
            seconds = int(seconds)
        except Exception:
            raise ValueError("seconds 必须是整数")

        if seconds not in (5, 10):
            raise ValueError("智谱视频时长只支持 5 秒或 10 秒。")

        effective_zhipu_api_key = ZhipuConfig.api_key()
        if not effective_zhipu_api_key:
            raise RuntimeError(
                "缺少智谱 API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 ZHIPU_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_zhipu_api_key,
        }

        body = {
            "model": "cogvideox-2",
            "image_url": image_url,
            "prompt": prompt,
            "duration": seconds,
            "quality": "speed",
            "with_audio": False,
            "fps": 30,
        }

        debug_log("====== Zhipu Image2Video Request ======")
        debug_log("URL:", cls.CREATE_VIDEO_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", body)

        response = requests.post(
            cls.CREATE_VIDEO_URL,
            headers=headers,
            json=body,
            timeout=3600,
        )

        debug_log("====== Zhipu Image2Video HTTP Status ======")
        debug_log(response.status_code)
        debug_log("====== Zhipu Image2Video Raw Response ======")
        debug_log(response.text)

        validate_http_response(response)

        data = response.json()
        task_id = data.get("id") or data.get("task_id")

        if not task_id:
            raise RuntimeError("Missing Zhipu task id: " + str(data))

        return str(task_id)

    @classmethod
    def poll_video_url(cls, task_id):
        task_id = str(task_id or "").strip()

        if not task_id:
            raise ValueError("taskID 不能为空")

        url = cls.POLL_URL_PREFIX + task_id

        effective_zhipu_api_key = ZhipuConfig.api_key()
        if not effective_zhipu_api_key:
            raise RuntimeError(
                "缺少智谱 API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 ZHIPU_API_KEY。"
            )

        headers = {
            "Authorization": "Bearer " + effective_zhipu_api_key,
        }

        for _ in range(120):
            response = requests.get(
                url,
                headers=headers,
                timeout=3600,
            )

            debug_log("====== Zhipu Poll HTTP Status ======")
            debug_log(response.status_code)
            debug_log("====== Zhipu Poll Raw Response ======")
            debug_log(response.text)

            validate_http_response(response)

            data = response.json()

            video_url = cls._extract_video_url(data)

            if isinstance(video_url, str) and video_url.startswith("http"):
                return video_url

            task_status = str(
                data.get("task_status") or data.get("status") or ""
            ).lower()

            if task_status in ["failed", "error", "canceled", "cancelled"]:
                raise RuntimeError("智谱视频任务失败：" + str(data))

            time.sleep(3)

        raise TimeoutError("智谱视频任务超时。")


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
