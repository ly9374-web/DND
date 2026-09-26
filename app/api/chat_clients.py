import json
import time
from dataclasses import dataclass
from typing import Optional, List
from urllib.parse import urlparse

import requests

from app.config import (
    XAIConfig,
    DeepSeekConfig,
    KeyLinkDeepSeekV4ProConfig,
    OpenRouterConfig,
    debug_log,
    mask_authorization_header,
)


# =========================
# 网络瞬断自动重试（代理重启/切换节点时秒级不可用）
# =========================

TRANSIENT_NETWORK_ERRORS = (
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

NETWORK_RETRY_DELAYS = (2, 5)


def post_with_network_retry(url, *, headers, json_body, timeout, label, bypass_env_proxy=False, stream=False):
    """POST 请求；遇到代理断连/连接被重置等瞬时网络错误时自动重试。

    仅重试连接层异常（请求未送达服务器），HTTP 错误码不重试。
    bypass_env_proxy=True 时对该 URL 的主机忽略环境变量代理直连
    （用于国内可直连的服务，如 api.deepseek.com）。
    stream=True 时返回未消费的流式响应，调用方自行迭代。
    """
    extra_kwargs = {}
    if bypass_env_proxy:
        # requests 官方支持的用法：proxies 中的 no_proxy 键使该主机绕过
        # 环境变量代理，其余请求不受影响。
        extra_kwargs["proxies"] = {"no_proxy": urlparse(url).hostname or ""}
    last_exc = None
    for attempt, delay in enumerate([0] + list(NETWORK_RETRY_DELAYS)):
        if delay:
            debug_log(f"{label} 网络瞬断，{delay}s 后第 {attempt + 1} 次重试：{last_exc}")
            time.sleep(delay)
        try:
            request_start = time.monotonic()
            response = requests.post(
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
                stream=stream,
                **extra_kwargs,
            )
            elapsed = time.monotonic() - request_start
            debug_log(f"[{label}] 请求发送+接口响应耗时 {elapsed:.2f}s")
            return response
        except TRANSIENT_NETWORK_ERRORS as exc:
            last_exc = exc
    raise last_exc


# =========================
# 对应 Swift: GrokResponseResult
# =========================

@dataclass
class GrokResponseResult:
    id: Optional[str]
    text: str


# =========================
# 对应 Swift: GrokInputMessage
# =========================

@dataclass
class GrokInputMessage:
    role: str
    content: str

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
        }


# =========================
# 对应 Swift: GrokAPIClient
# =========================

class GrokAPIClient:
    """
    对应 Swift 里的 final class GrokAPIClient。

    功能：
    1. 普通聊天 send_message()
    2. 解析 Grok Responses API 返回
    """

    RESPONSES_URL = "https://api.x.ai/v1/responses"

    @classmethod
    def send_message(
        cls,
        system_prompt,
        context_messages,
        user_message,
        model="grok-4.3",
        temperature=0.8,
        thinking_enabled=None,
        reasoning_effort=None,
    ):
        """
        对应 Swift:
        sendMessage(systemPrompt:contextMessages:userMessage:model:temperature:)
        """

        input_messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        for message in context_messages:
            if isinstance(message, GrokInputMessage):
                input_messages.append(message.to_dict())
            elif isinstance(message, dict):
                input_messages.append(message)

        input_messages.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        body = {
            "model": model,
            "input": input_messages,
            "temperature": temperature,
            "max_output_tokens": 20000,
        }
        if thinking_enabled is not None:
            effective_reasoning_effort = (
                str(reasoning_effort or "high") if thinking_enabled else "none"
            )
            body["reasoning"] = {"effort": effective_reasoning_effort}

        data = cls._post_responses(body, label="Grok Request")
        return cls.parse_response(data).text

    @staticmethod
    def _consume_responses_stream(response, label):
        """消费 /v1/responses 的 SSE 流，返回与同步接口同构的 data dict。

        优先使用 response.completed 事件携带的完整 response；
        若流直接以连接关闭结束（无 completed/[DONE]，实测 xAI 行为），
        则从 output_text.delta 拼接文本并构造 {"id": ..., "output_text": ...}。
        无任何文本 delta 时返回 None，由调用方回退非流式。
        """
        text_parts = []
        response_id = None
        # SSE 流为 UTF-8；响应头未声明 charset 时 requests 默认用
        # ISO-8859-1 解码会导致中文乱码，这里显式指定。
        if (response.encoding or "").lower().replace("-", "") != "utf8":
            response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type == "response.completed":
                inner = event.get("response")
                if isinstance(inner, dict):
                    return inner
            elif event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif event_type == "response.created" and response_id is None:
                created = event.get("response")
                if isinstance(created, dict):
                    response_id = created.get("id")

        if not text_parts:
            return None
        debug_log("====== " + label + " Stream Output ======")
        text = "".join(text_parts)
        debug_log(text)
        return {"id": response_id, "output_text": text}

    @classmethod
    def _post_responses(cls, body, label="Grok Request"):
        effective_grok_chat_api_key = XAIConfig.chat_api_key()
        if not effective_grok_chat_api_key:
            raise RuntimeError(
                "缺少 Grok 聊天 API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 GROK_CHAT_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_grok_chat_api_key,
        }

        debug_log("====== " + label + " ======")
        debug_log("URL:", cls.RESPONSES_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", json.dumps(body, ensure_ascii=False))

        # 流式优先：代理通常对空闲连接有约 60s 超时，长思考请求（如 grok-4.6
        # high reasoning）在非流式下会因等待期间无数据流动被代理掐断；
        # 流式的 delta 事件持续到达可保持连接活跃。
        try:
            response = post_with_network_retry(
                cls.RESPONSES_URL,
                headers=headers,
                json_body={**body, "stream": True},
                timeout=(15, 600),
                label=label,
                stream=True,
            )
            if response.status_code == 200:
                data = cls._consume_responses_stream(response, label)
                if data is not None:
                    return data
                debug_log(f"{label} 流式未返回内容，回退非流式。")
            else:
                # 错误响应是普通 JSON，直接解析返回，与原非流式错误路径一致。
                debug_log("====== " + label + " HTTP Status ======")
                debug_log(response.status_code)
                try:
                    return response.json()
                except Exception:
                    return {
                        "error": {
                            "message": "Grok 返回内容不是 JSON：" + response.text
                        }
                    }
        except TRANSIENT_NETWORK_ERRORS as exc:
            debug_log(f"{label} 流式请求网络失败，回退非流式：{exc}")

        response = post_with_network_retry(
            cls.RESPONSES_URL,
            headers=headers,
            json_body=body,
            timeout=150,
            label=label,
        )

        debug_log("====== " + label + " HTTP Status ======")
        debug_log(response.status_code)

        debug_log("====== " + label + " Raw Response ======")
        debug_log(response.text)

        try:
            data = response.json()
        except Exception:
            return {
                "error": {
                    "message": "Grok 返回内容不是 JSON：" + response.text
                }
            }

        return data

    @staticmethod
    def parse_response(data):
        """
        对应 Swift:
        private static func parseResponse(from data: Data, rawResponse: String? = nil)
        """

        if not isinstance(data, dict):
            return GrokResponseResult(
                id=None,
                text="未解析到 Grok 回复内容。",
            )

        response_id = data.get("id")

        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or "unknown"
            return GrokResponseResult(
                id=response_id,
                text="Grok API 错误：" + str(message),
            )

        if isinstance(error, str) and error.strip():
            return GrokResponseResult(
                id=response_id,
                text="Grok API 错误：" + error,
            )

        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return GrokResponseResult(
                id=response_id,
                text=output_text,
            )

        pieces = []

        output = data.get("output")
        if isinstance(output, list):
            extracted_from_message_items = False

            # 优先只取 type == message 的 output_text
            for item in output:
                if not isinstance(item, dict):
                    continue

                if item.get("type") != "message":
                    continue

                extracted_from_message_items = True

                content_array = item.get("content")
                if not isinstance(content_array, list):
                    continue

                for block in content_array:
                    if not isinstance(block, dict):
                        continue

                    if block.get("type") == "output_text":
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            pieces.append(text)

            # 兼容其他返回结构
            if not extracted_from_message_items:
                for item in output:
                    if not isinstance(item, dict):
                        continue

                    content_array = item.get("content")
                    if isinstance(content_array, list):
                        for block in content_array:
                            if not isinstance(block, dict):
                                continue

                            text = block.get("text") or block.get("content")
                            if isinstance(text, str) and text.strip():
                                pieces.append(text)

                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        pieces.append(text)

                    message = item.get("message")
                    if isinstance(message, dict):
                        message_content = message.get("content")

                        if isinstance(message_content, str) and message_content.strip():
                            pieces.append(message_content)

                        if isinstance(message_content, list):
                            for block in message_content:
                                if not isinstance(block, dict):
                                    continue

                                text = block.get("text") or block.get("content")
                                if isinstance(text, str) and text.strip():
                                    pieces.append(text)

        # 去重，避免上下文污染
        seen = set()
        unique_pieces = []

        for piece in pieces:
            text = str(piece).strip()
            if not text:
                continue

            if text not in seen:
                seen.add(text)
                unique_pieces.append(text)

        combined = "\n".join(unique_pieces).strip()

        if not combined:
            return GrokResponseResult(
                id=response_id,
                text="未解析到 Grok 回复内容。",
            )

        return GrokResponseResult(
            id=response_id,
            text=combined,
        )


# =========================
# 对应 Swift: DeepSeekAPIClient
# =========================

class DeepSeekAPIClient:
    """
    对应 Swift 里的 final class DeepSeekAPIClient。
    """

    CHAT_URL = "https://api.deepseek.com/v1/chat/completions"
    KEYLINK_V4_PRO_CHAT_URL = "https://keylinkclub.com/v1/chat/completions"
    DEFAULT_MODEL = "deepseek-v4-flash"
    SUPPORTED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}

    @classmethod
    def send_message(
        cls,
        system_prompt,
        context_messages,
        user_message,
        temperature=0.8,
        model=DEFAULT_MODEL,
        thinking_enabled=True,
        reasoning_effort="high",
        max_tokens=50000,
    ):
        """
        对应 Swift:
        sendMessage(systemPrompt:contextMessages:userMessage:temperature:)
        """

        model = str(model or cls.DEFAULT_MODEL).strip()
        if model not in cls.SUPPORTED_MODELS:
            raise ValueError("未知 DeepSeek 模型：" + model)

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        for message in context_messages:
            if isinstance(message, GrokInputMessage):
                messages.append(message.to_dict())
            elif isinstance(message, dict):
                messages.append(message)

        messages.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        request_url = cls.CHAT_URL
        provider_label = "DeepSeek"
        use_keylink = False
        effective_api_key = ""

        if model == "deepseek-v4-pro" and KeyLinkDeepSeekV4ProConfig.enabled():
            effective_api_key = KeyLinkDeepSeekV4ProConfig.api_key()
            if effective_api_key:
                request_url = cls.KEYLINK_V4_PRO_CHAT_URL
                provider_label = "KeyLink DeepSeek"
                use_keylink = True

        if not effective_api_key:
            effective_api_key = DeepSeekConfig.api_key()

        if not effective_api_key:
            raise RuntimeError(
                "缺少 DeepSeek API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 DEEPSEEK_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_api_key,
        }

        if use_keylink:
            body = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
        else:
            body = {
                "messages": messages,
                "model": model,
                "thinking": {
                    "type": "enabled" if thinking_enabled else "disabled",
                },
                "response_format": {
                    "type": "text",
                },
                "stream": False,
                "temperature": temperature,
                "top_p": 1,
                "tool_choice": "none",
                "logprobs": False,
            }
            if thinking_enabled and reasoning_effort:
                body["reasoning_effort"] = str(reasoning_effort)
            if max_tokens is not None:
                body["max_tokens"] = int(max_tokens)

        debug_log("====== DeepSeek Chat Request ======")
        debug_log("URL:", request_url)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", json.dumps(body, ensure_ascii=False))

        response = post_with_network_retry(
            request_url,
            headers=headers,
            json_body=body,
            timeout=3600,
            label="DeepSeek Chat",
            # api.deepseek.com 国内可直连，且部分代理节点会阻断该域名；
            # KeyLink 中转域名仍按环境变量代理设置访问。
            bypass_env_proxy=not use_keylink,
        )

        debug_log("====== DeepSeek Chat HTTP Status ======")
        debug_log(response.status_code)

        debug_log("====== DeepSeek Chat Raw Response ======")
        debug_log(response.text)

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"{provider_label} 返回了无法解析的响应（HTTP {response.status_code}）。"
            ) from exc

        if data.get("error"):
            debug_log("====== DeepSeek Chat Error ======")
            debug_log(data.get("error"))
            raise RuntimeError(provider_label + " API error: " + str(data.get("error")))

        if int(response.status_code) >= 400:
            raise RuntimeError(f"{provider_label} API HTTP 错误：{response.status_code}")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("DeepSeek 响应缺少 choices，没有可用的模型输出。")

        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("DeepSeek 响应中的 choice 格式异常。")

        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("DeepSeek 响应缺少 message。")

        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or item.get("content") or "")
                for item in content
                if isinstance(item, dict)
            )

        finish_reason = str(first.get("finish_reason") or "unknown")
        content = str(content or "").strip()
        if finish_reason == "length" and thinking_enabled and not use_keylink:
            debug_log(
                "DeepSeek thinking output reached max_tokens; "
                "retrying the same request with thinking disabled."
            )
            return cls.send_message(
                system_prompt=system_prompt,
                context_messages=context_messages,
                user_message=user_message,
                temperature=temperature,
                model=model,
                thinking_enabled=False,
                reasoning_effort=None,
                max_tokens=max_tokens,
            )
        if content:
            return content

        if finish_reason == "length":
            if use_keylink:
                raise RuntimeError(
                    "KeyLink DeepSeek 未返回最终内容（finish_reason=length）。"
                )
            raise RuntimeError(
                "DeepSeek 未返回最终内容（finish_reason=length），"
                "关闭思考自动重试后仍用完了本次输出 token。"
            )
        raise RuntimeError(
            "DeepSeek 未返回可用的最终内容"
            f"（finish_reason={finish_reason}）。"
        )


# =========================
# OpenRouter Chat Completions
# =========================

class OpenRouterChatAPIClient:
    CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
    DEFAULT_MODEL = "mistralai/mistral-large-2512"
    SUPPORTED_MODELS = {DEFAULT_MODEL}

    @classmethod
    def send_message(
        cls,
        system_prompt,
        context_messages,
        user_message,
        temperature=0.8,
        model=DEFAULT_MODEL,
        max_tokens=20000,
    ):
        model = str(model or cls.DEFAULT_MODEL).strip()
        if model not in cls.SUPPORTED_MODELS:
            raise ValueError("未知 OpenRouter 聊天模型：" + model)

        messages = [{"role": "system", "content": system_prompt}]
        for message in context_messages:
            if isinstance(message, GrokInputMessage):
                messages.append(message.to_dict())
            elif isinstance(message, dict):
                messages.append(message)
        messages.append({"role": "user", "content": user_message})

        api_key = OpenRouterConfig.api_key()
        if not api_key:
            raise RuntimeError(
                "缺少 OpenRouter API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 OPENROUTER_API_KEY。"
            )

        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)

        debug_log("====== OpenRouter Chat Request ======")
        debug_log("URL:", cls.CHAT_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", json.dumps(body, ensure_ascii=False))

        response = post_with_network_retry(
            cls.CHAT_URL,
            headers=headers,
            json_body=body,
            timeout=3600,
            label="OpenRouter Chat",
        )

        debug_log("====== OpenRouter Chat HTTP Status ======")
        debug_log(response.status_code)
        debug_log("====== OpenRouter Chat Raw Response ======")
        debug_log(response.text)

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"OpenRouter 返回了无法解析的响应（HTTP {response.status_code}）。"
            ) from exc

        if data.get("error"):
            debug_log("====== OpenRouter Chat Error ======")
            debug_log(data.get("error"))
            raise RuntimeError("OpenRouter API error: " + str(data.get("error")))
        if int(response.status_code) >= 400:
            raise RuntimeError(f"OpenRouter API HTTP 错误：{response.status_code}")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter 响应缺少 choices，没有可用的模型输出。")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("OpenRouter 响应中的 choice 格式异常。")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("OpenRouter 响应缺少 message。")

        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or item.get("content") or "")
                for item in content
                if isinstance(item, dict)
            )
        content = str(content or "").strip()
        if content:
            return content

        finish_reason = str(first.get("finish_reason") or "unknown")
        raise RuntimeError(
            "OpenRouter 未返回可用的最终内容"
            f"（finish_reason={finish_reason}）。"
        )
