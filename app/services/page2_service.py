from __future__ import annotations

import base64
import binascii
import secrets
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional

from app.api.chat_clients import GrokAPIClient, DeepSeekAPIClient
from app.api.media_clients import (
    CloudinaryUploader,
    DomoAIClient,
    GrokImageAPIClient,
    ReplicateImageAPIClient,
    ReplicateVideoAPIClient,
    ZhipuVideoClient,
    download_url_as_base64,
    try_download_url_as_base64,
)
from app.config import AppStorageKeys, settings
from app.models import (
    ChatRecord,
    GeneratedImageRecord,
    GeneratedMediaKind,
    Page2ConversationTurn,
    deepseek_api_model_for_chat_model,
    grok_api_model_for_chat_model,
    normalize_chat_model,
    now_iso,
)
from app.services.story_brain_prompts import DEFAULT_STORY_BRAIN_GENERATOR_PROMPT
from app.storage import ChatRecordStore
DEFAULT_SYSTEM_PROMPT = ""
STORY_BRAIN_SHORT = "story_brain_short"
STORY_BRAIN_UPDATE_MODELS = ["deepseekPro", "deepseekFlash", "grok"]
STORY_BRAIN_UPDATE_MODEL_LABELS = {
    "deepseekPro": "DeepSeek Pro",
    "deepseekFlash": "DeepSeek Flash",
    "grok": "Grok",
}
INVALID_STORY_BRAIN_TEXTS = {"未解析到 DeepSeek 回复内容。"}

def normalize_story_brain_update_model(value) -> str:
    model = str(value or "").strip()
    if model == "deepseek":
        return "deepseekFlash"
    if model in STORY_BRAIN_UPDATE_MODELS:
        return model
    return "deepseekFlash"

UNEXPECTED_EVENT_PROMPT = """
本轮根据上下文，根据剧情走向和人设发生一件自然的意外事件（不要直接告诉用户是意外事件），意外情况需要推动剧情的发展，需要用户对其进行反应。意外情况可以是场景中的角色说的话，做的行为，也可以是新角色的入场或者场景中什么事情的发生
""".strip()


def _strip_plain_text_output(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _turn_history_text(turns: Iterable[Page2ConversationTurn], limit: int) -> str:
    limit = max(0, int(limit))
    if limit == 0:
        return ""

    completed_turns = [
        turn
        for turn in list(turns or [])
        if str(getattr(turn, "user_message", "") or "").strip()
        or str(getattr(turn, "assistant_message", "") or "").strip()
    ]
    recent = completed_turns[-limit:]
    lines = []
    for turn in recent:
        user_text = str(getattr(turn, "user_message", "") or "").strip()
        assistant_text = str(getattr(turn, "assistant_message", "") or "").strip()
        if user_text:
            lines.append("用户：" + user_text)
        if assistant_text:
            lines.append("助手：" + assistant_text)
    return "\n".join(lines)


def build_recent_image_prompt_conversation(
    turns: Iterable[Page2ConversationTurn],
) -> str:
    """只取最新一轮的 Assistant 回复，作为生成图片 prompt 的输入。"""
    for turn in reversed(list(turns or [])):
        assistant_text = str(getattr(turn, "assistant_message", "") or "").strip()
        if assistant_text:
            return assistant_text
    return ""


def _inject_short_story_brain_into_user_prompt(user_message: str, story_brain_short: str) -> str:
    current_text = str(user_message or "").strip()
    story_brain_text = str(story_brain_short or "").strip()
    if story_brain_text in INVALID_STORY_BRAIN_TEXTS:
        story_brain_text = ""
    story_brain_text = story_brain_text or "暂无"
    story_brain_section = f"<当前故事背景>{story_brain_text}</当前故事背景>"
    return f"{story_brain_section}\n\n{current_text}".strip()


def roll_point() -> int:
    return secrets.randbelow(25)


def should_auto_roll_dice(assistant_message: str) -> bool:
    """Return whether a freshly generated reply explicitly requests a dice roll."""
    return str(assistant_message or "").rstrip().endswith("可以开始掷骰子。")


def roll_unexpected_event() -> int:
    return secrets.randbelow(101)


def _append_unexpected_event_prompt(user_message: str) -> str:
    current_text = str(user_message or "").strip()
    return f"{current_text}\n\n{UNEXPECTED_EVENT_PROMPT}".strip()


@dataclass
class Page2Context:
    system_prompt: str
    context_turn_count: int
    selected_chat_model: str
    chat_reasoning_enabled: bool
    story_brain_update_model: str
    story_brain_reasoning_enabled: bool
    story_brain_turns: int
    unexpected_event_enabled: bool
    unexpected_event_threshold: int
    temperature: float
    selected_video_generation_provider: str


def load_context_from_settings() -> Page2Context:
    return Page2Context(
        system_prompt=str(settings.get(AppStorageKeys.SYSTEM_PROMPT, "") or "").strip()
        or DEFAULT_SYSTEM_PROMPT,
        context_turn_count=max(0, settings.int(AppStorageKeys.PAGE2_CONTEXT_TURN_COUNT, 8)),
        selected_chat_model=normalize_chat_model(
            settings.get(AppStorageKeys.PAGE2_SELECTED_CHAT_MODEL, "grok1")
        ),
        chat_reasoning_enabled=settings.bool(
            AppStorageKeys.PAGE2_CHAT_REASONING_ENABLED,
            True,
        ),
        story_brain_update_model=normalize_story_brain_update_model(
            settings.get(AppStorageKeys.PAGE2_STORY_BRAIN_UPDATE_MODEL, "deepseekFlash")
        ),
        story_brain_reasoning_enabled=settings.bool(
            AppStorageKeys.PAGE2_STORY_BRAIN_REASONING_ENABLED,
            False,
        ),
        story_brain_turns=max(1, settings.int(AppStorageKeys.PAGE2_STORY_BRAIN_TURNS, 6)),
        unexpected_event_enabled=settings.bool(
            AppStorageKeys.PAGE2_UNEXPECTED_EVENT_ENABLED,
            False,
        ),
        unexpected_event_threshold=min(
            100,
            max(0, settings.int(AppStorageKeys.PAGE2_UNEXPECTED_EVENT_THRESHOLD, 98)),
        ),
        temperature=float(settings.float(AppStorageKeys.PAGE2_TEMPERATURE, 0.8)),
        selected_video_generation_provider=str(
            settings.get(AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER, "domoai")
            or "domoai"
        ),
    )


def default_context() -> Page2Context:
    return Page2Context(
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        context_turn_count=8,
        selected_chat_model="grok1",
        chat_reasoning_enabled=True,
        story_brain_update_model="deepseekFlash",
        story_brain_reasoning_enabled=False,
        story_brain_turns=6,
        unexpected_event_enabled=False,
        unexpected_event_threshold=98,
        temperature=0.8,
        selected_video_generation_provider="domoai",
    )


def reset_context_settings():
    # 全部设置（含 prompt 选择）保留上次值，不重置
    return


def save_context_to_settings(ctx: Page2Context):
    settings.set(AppStorageKeys.SYSTEM_PROMPT, str(ctx.system_prompt or "").strip())
    settings.set(AppStorageKeys.PAGE2_CONTEXT_TURN_COUNT, int(max(0, ctx.context_turn_count)))
    settings.set(
        AppStorageKeys.PAGE2_SELECTED_CHAT_MODEL,
        normalize_chat_model(ctx.selected_chat_model),
    )
    settings.set(
        AppStorageKeys.PAGE2_CHAT_REASONING_ENABLED,
        bool(ctx.chat_reasoning_enabled),
    )
    settings.set(
        AppStorageKeys.PAGE2_STORY_BRAIN_UPDATE_MODEL,
        normalize_story_brain_update_model(ctx.story_brain_update_model),
    )
    settings.set(
        AppStorageKeys.PAGE2_STORY_BRAIN_REASONING_ENABLED,
        bool(ctx.story_brain_reasoning_enabled),
    )
    settings.set(AppStorageKeys.PAGE2_STORY_BRAIN_TURNS, max(1, int(ctx.story_brain_turns)))
    settings.set(
        AppStorageKeys.PAGE2_UNEXPECTED_EVENT_ENABLED,
        bool(ctx.unexpected_event_enabled),
    )
    settings.set(
        AppStorageKeys.PAGE2_UNEXPECTED_EVENT_THRESHOLD,
        min(100, max(0, int(ctx.unexpected_event_threshold))),
    )
    settings.set(AppStorageKeys.PAGE2_TEMPERATURE, float(ctx.temperature))
    settings.set(
        AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER,
        str(ctx.selected_video_generation_provider or "domoai"),
    )


def build_context_messages(
    turns: Iterable[Page2ConversationTurn],
    context_turn_count: int,
) -> list[dict]:
    turns_list = list(turns)
    turn_count = max(0, int(context_turn_count))
    if turn_count == 0:
        return []

    slice_turns = turns_list[-turn_count:]
    messages: list[dict] = []

    for turn in slice_turns:
        if turn.user_message:
            messages.append({"role": "user", "content": turn.user_message})
        if turn.assistant_message:
            messages.append({"role": "assistant", "content": turn.assistant_message})

    return messages


def send_message(
    *,
    ctx: Page2Context,
    turns: list[Page2ConversationTurn],
    user_message: str,
    story_brain_short: str = "",
    story_brain_enabled: bool = True,
) -> str:
    context_messages = build_context_messages(turns, ctx.context_turn_count)
    system_prompt = ctx.system_prompt
    user_message_for_model = user_message

    if story_brain_enabled:
        user_message_for_model = _inject_short_story_brain_into_user_prompt(
            user_message,
            story_brain_short,
        )

    if ctx.unexpected_event_enabled:
        threshold = min(100, max(0, int(ctx.unexpected_event_threshold)))
        if roll_unexpected_event() >= threshold:
            user_message_for_model = _append_unexpected_event_prompt(user_message_for_model)

    deepseek_model = deepseek_api_model_for_chat_model(ctx.selected_chat_model)
    if deepseek_model is not None:
        return DeepSeekAPIClient.send_message(
            system_prompt=system_prompt,
            context_messages=context_messages,
            user_message=user_message_for_model,
            temperature=ctx.temperature,
            model=deepseek_model,
            thinking_enabled=ctx.chat_reasoning_enabled,
            reasoning_effort="high" if ctx.chat_reasoning_enabled else None,
        )

    grok_model = grok_api_model_for_chat_model(ctx.selected_chat_model) or "grok-4.3"
    grok_requires_reasoning = grok_model == "grok-4.6"
    return GrokAPIClient.send_message(
        system_prompt=system_prompt,
        context_messages=context_messages,
        user_message=user_message_for_model,
        model=grok_model,
        temperature=ctx.temperature,
        thinking_enabled=True if grok_requires_reasoning else ctx.chat_reasoning_enabled,
        reasoning_effort=(
            "high"
            if ctx.chat_reasoning_enabled
            else "low" if grok_requires_reasoning else None
        ),
    )


def generate_short_story_brain(
    *,
    ctx: Page2Context,
    turns: list[Page2ConversationTurn],
    story_brain_short: str,
) -> str:
    history_turns = max(1, int(ctx.story_brain_turns))
    existing_story_brain = str(story_brain_short or "").strip()
    if existing_story_brain in INVALID_STORY_BRAIN_TEXTS:
        existing_story_brain = ""
    generator_user_message = f"""
过去的记录：
{_turn_history_text(turns, history_turns) or "暂无"}

现有 Story Brain：
{existing_story_brain or "暂无"}
""".strip()

    update_model = normalize_story_brain_update_model(ctx.story_brain_update_model)
    deepseek_model = deepseek_api_model_for_chat_model(update_model)
    if deepseek_model is not None:
        raw_text = DeepSeekAPIClient.send_message(
            system_prompt=DEFAULT_STORY_BRAIN_GENERATOR_PROMPT,
            context_messages=[],
            user_message=generator_user_message,
            temperature=ctx.temperature,
            model=deepseek_model,
            thinking_enabled=ctx.story_brain_reasoning_enabled,
            reasoning_effort="high" if ctx.story_brain_reasoning_enabled else None,
            max_tokens=50000,
        )
    else:
        raw_text = GrokAPIClient.send_message(
            system_prompt=DEFAULT_STORY_BRAIN_GENERATOR_PROMPT,
            context_messages=[],
            user_message=generator_user_message,
            model="grok-4.3",
            temperature=ctx.temperature,
            thinking_enabled=ctx.story_brain_reasoning_enabled,
            reasoning_effort="high" if ctx.story_brain_reasoning_enabled else None,
        )

    updated_story_brain = _strip_plain_text_output(raw_text)
    if updated_story_brain in INVALID_STORY_BRAIN_TEXTS:
        raise ValueError("Story Brain 更新模型没有返回可用内容。")
    return updated_story_brain or existing_story_brain


def generate_image_prompt(conversation_text: str, mode: str = "normal", subject: str = "") -> str:
    conversation_text = str(conversation_text or "").strip()
    if not conversation_text:
        raise ValueError("暂无助手回复内容。")

    mode = str(mode or "normal").strip()
    subject = str(subject or "").strip()

    if mode == "first_person":
        if not subject:
            raise ValueError("主体不能为空。")
        return GrokAPIClient.generate_first_person_image_prompt(conversation_text, subject)

    if mode == "closeup":
        if not subject:
            raise ValueError("主体不能为空。")
        return GrokAPIClient.generate_character_closeup_image_prompt(conversation_text, subject)

    return GrokAPIClient.generate_image_prompt(conversation_text)


def generate_image(provider: str, prompt: str, image_urls: Optional[list[str]] = None) -> GeneratedImageRecord:
    provider = str(provider or "").strip()
    prompt = str(prompt or "").strip()
    image_urls = image_urls or []
    image_urls = [str(url).strip() for url in image_urls if str(url).strip()]

    if not prompt:
        raise ValueError("你得先点“生成图片prompt”生成prompt才能点这个生图")

    if provider in ["grok", "grokQuality", "grokPro"]:
        model = {
            "grok": "grok-imagine-image",
            "grokQuality": "grok-imagine-image-quality",
            "grokPro": "grok-imagine-image-2.0",
        }.get(provider, "grok-imagine-image")

        result = GrokImageAPIClient.generate_image(
            prompt=prompt,
            image_urls=image_urls,
            model=model,
            resolution="1k" if provider == "grokPro" else "2k",
            quality="low" if provider == "grokPro" else None,
        )
    else:
        result = ReplicateImageAPIClient.generate_image(
            provider=provider,
            prompt=prompt,
            image_urls=image_urls,
        )

    image_url = getattr(result, "image_url", None)
    image_base64 = getattr(result, "image_data_base64", None)

    if image_url and not image_base64:
        image_base64 = try_download_url_as_base64(image_url)

    return GeneratedImageRecord(
        provider=provider,
        prompt=prompt,
        media_kind=GeneratedMediaKind.IMAGE,
        image_url_string=image_url,
        image_data_base64=image_base64,
        image_input_urls=image_urls,
        video_url_string=None,
        source_image_url_string=None,
        source_image_data_base64=None,
        duration_seconds=None,
        video_generation_provider=None,
    )


def _decode_image_base64(image_base64: str) -> bytes:
    text = str(image_base64 or "").strip()

    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1].strip()

    try:
        image_bytes = base64.b64decode(text, validate=True)
    except binascii.Error:
        image_bytes = base64.b64decode(text)

    if not image_bytes:
        raise ValueError("图片 base64 数据为空。")

    return image_bytes


def _prepare_video_cloudinary_image_url(source_record: GeneratedImageRecord) -> str:
    if source_record.image_data_base64:
        try:
            image_bytes = _decode_image_base64(source_record.image_data_base64)
        except Exception as exc:
            raise RuntimeError("解析输入图片 base64 失败，无法上传到 Cloudinary。原始错误：" + str(exc))
    elif source_record.image_url_string:
        try:
            image_base64 = download_url_as_base64(source_record.image_url_string)
            image_bytes = _decode_image_base64(image_base64)
        except Exception as exc:
            raise RuntimeError(
                "输入图片 URL 已无法访问或无法下载，无法上传到 Cloudinary。"
                "请重新生成图片，或使用仍可访问的图片作为输入。原始错误："
                + str(exc)
            )
    else:
        raise RuntimeError("图生视频需要可用的图片 URL 或 base64 图片数据。")

    try:
        return CloudinaryUploader.upload_image_bytes(image_bytes)
    except Exception as exc:
        raise RuntimeError(
            "上传图片到 Cloudinary 失败，无法生成视频。"
            "请确认 Cloudinary API Key 已在 APIkey 页面或 Streamlit Secrets 配置，"
            "并确认 Cloudinary API Secret 已在 APIkey 页面或 Streamlit Secrets 配置。原始错误："
            + str(exc)
        )


def generate_video_from_image(
    *,
    ctx: Page2Context,
    source_record: GeneratedImageRecord,
    prompt: str,
    seconds: int,
) -> GeneratedImageRecord:
    prompt = str(prompt or "").strip()
    if not prompt:
        prompt = "动起来"

    provider = str(ctx.selected_video_generation_provider or "domoai").strip()
    effective_seconds = int(seconds)

    if provider == "wan22Fast":
        image_reference = _prepare_video_cloudinary_image_url(source_record)
        video_url = ReplicateVideoAPIClient.generate_wan_2_2_i2v_fast(
            image_reference,
            prompt,
        )
        effective_seconds = 5
    elif provider == "zhipu":
        image_reference = _prepare_video_cloudinary_image_url(source_record)
        task_id = ZhipuVideoClient.create_image_to_video_task(
            image_reference,
            prompt,
            seconds,
        )
        video_url = ZhipuVideoClient.poll_video_url(task_id)
    else:
        if source_record.image_data_base64:
            image_base64 = source_record.image_data_base64
        elif source_record.image_url_string:
            try:
                image_base64 = download_url_as_base64(source_record.image_url_string)
            except Exception as exc:
                raise RuntimeError(
                    "输入图片 URL 已无法访问，图生视频需要原图数据。"
                    "请重新生成图片，或使用仍可访问的图片作为输入。原始错误："
                    + str(exc)
                )
        else:
            raise RuntimeError("当前记录没有可用图片。")

        task_id = DomoAIClient.create_image_to_video_task_with_base64(
            image_base64,
            prompt,
            seconds,
        )
        video_url = DomoAIClient.poll_task_until_video_url(task_id)

    return GeneratedImageRecord(
        provider=source_record.provider,
        prompt=prompt,
        media_kind=GeneratedMediaKind.VIDEO,
        image_url_string=None,
        image_data_base64=None,
        image_input_urls=[],
        video_url_string=video_url,
        source_image_url_string=source_record.image_url_string,
        source_image_data_base64=source_record.image_data_base64,
        duration_seconds=effective_seconds,
        video_generation_provider=provider,
    )


def ensure_record_id(existing: Optional[str]) -> str:
    existing = str(existing or "").strip()
    return existing or str(uuid.uuid4())


def make_record_title(turns: list[Page2ConversationTurn]) -> str:
    for turn in turns:
        if bool(getattr(turn, "is_user_message_hidden", False)):
            continue
        text = str(turn.user_message or "").strip()
        if text:
            return text[:24]
    # 没有用户消息时（如仅含首轮输出开场消息的记录），用 assistant 消息做标题
    for turn in turns:
        text = str(turn.assistant_message or "").strip()
        if text:
            return text[:24]
    return "聊天记录"


def upsert_chat_record(
    *,
    record_id: Optional[str],
    turns: list[Page2ConversationTurn],
    generated_media: list[GeneratedImageRecord],
    system_prompt: str,
    story_brain: object,
    story_brain_short: str = "",
    scope: str | None = None,
) -> str:
    has_short_story_brain = bool(str(story_brain_short or "").strip())
    if not turns and not generated_media and not has_short_story_brain:
        return str(record_id or "").strip()

    now = now_iso()
    record_id = ensure_record_id(record_id)

    existing_title = None
    existing_created_at = None
    for item in ChatRecordStore.load_index(scope=scope):
        if item.id == record_id:
            existing_title = item.title
            existing_created_at = item.created_at
            break

    record = ChatRecord(
        id=record_id,
        title=existing_title or make_record_title(turns),
        turns=turns,
        system_prompt=system_prompt,
        generated_images=generated_media,
        story_brain=story_brain,
        story_brain_short=str(story_brain_short or "").strip(),
        created_at=existing_created_at or now,
        updated_at=now,
    )
    ChatRecordStore.save_or_update_record(record, scope=scope)
    return record_id
