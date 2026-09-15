from __future__ import annotations

import base64
import binascii
import io
import math
import secrets
import uuid
from dataclasses import dataclass, replace
from typing import Iterable, List, Optional

from PIL import Image, ImageOps

from app.api.chat_clients import DeepSeekAPIClient, GrokAPIClient, OpenRouterChatAPIClient
from app.api.media_clients import (
    ANILLUSTRIOUS_PROVIDER,
    WAI_ILLUSTRIOUS_PROVIDER,
    IMAGE_ASPECT_RATIO,
    IMAGE_ASPECT_RATIOS,
    CloudinaryUploader,
    OpenRouterVideoAPIClient,
    GrokImageAPIClient,
    ReplicateImageAPIClient,
    ReplicateVideoAPIClient,
    download_url_as_base64,
)
from app.config import AppStorageKeys, settings
from app.models import (
    CHAT_MODEL_LABELS,
    CHAT_MODEL_OPTIONS,
    ChatRecord,
    GeneratedImageRecord,
    GeneratedMediaKind,
    Page2ConversationTurn,
    deepseek_api_model_for_chat_model,
    grok_api_model_for_chat_model,
    normalize_chat_model,
    now_iso,
    openrouter_api_model_for_chat_model,
)
from app.services.story_brain_prompts import DEFAULT_STORY_BRAIN_GENERATOR_PROMPT
from app.services.image_prompts import build_image_prompt_system_prompt
from app.services.video_prompts import VIDEO_PROMPT_SYSTEM_PROMPT
from app.storage import ChatRecordStore
from app.video_models import (
    VIDEO_MODELS, WAN_MODEL, WAN_FPS, WAN_DURATION_FRAMES,
    normalize_video_model, validate_video_duration,
)
DEFAULT_SYSTEM_PROMPT = ""
STORY_BRAIN_SHORT = "story_brain_short"
STORY_BRAIN_UPDATE_MODELS = list(CHAT_MODEL_OPTIONS)
STORY_BRAIN_UPDATE_MODEL_LABELS = dict(CHAT_MODEL_LABELS)
INVALID_STORY_BRAIN_TEXTS = {"未解析到 DeepSeek 回复内容。"}

def normalize_story_brain_update_model(value) -> str:
    model = str(value or "").strip()
    if model == "grok":
        return "grok1"
    if model == "deepseek":
        return "deepseekFlash"
    if model in STORY_BRAIN_UPDATE_MODELS:
        return model
    return "deepseekFlash"

UNEXPECTED_EVENT_PROMPT = """
本轮根据上下文，根据剧情走向和场景中角色人设发生一件自然的意外事件（不要直接告诉用户是意外事件），意外情况可以是场景中的角色主动了什么话，或做了什么行为，也可能是什么突发事件。
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
        selected_video_generation_provider=normalize_video_model(
            settings.get(AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER, WAN_MODEL)
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
        selected_video_generation_provider=WAN_MODEL,
    )


def reset_context_settings():
    # 全部设置（含 prompt 选择）保留上次值，不重置
    return


def save_context_to_settings(ctx: Page2Context) -> bool:
    """Atomically persist a changed context and skip no-op page reruns."""
    updates = {
        AppStorageKeys.SYSTEM_PROMPT: str(ctx.system_prompt or "").strip(),
        AppStorageKeys.PAGE2_CONTEXT_TURN_COUNT: int(max(0, ctx.context_turn_count)),
        AppStorageKeys.PAGE2_SELECTED_CHAT_MODEL: normalize_chat_model(
            ctx.selected_chat_model
        ),
        AppStorageKeys.PAGE2_CHAT_REASONING_ENABLED: bool(
            ctx.chat_reasoning_enabled
        ),
        AppStorageKeys.PAGE2_STORY_BRAIN_UPDATE_MODEL: (
            normalize_story_brain_update_model(ctx.story_brain_update_model)
        ),
        AppStorageKeys.PAGE2_STORY_BRAIN_REASONING_ENABLED: bool(
            ctx.story_brain_reasoning_enabled
        ),
        AppStorageKeys.PAGE2_STORY_BRAIN_TURNS: max(1, int(ctx.story_brain_turns)),
        AppStorageKeys.PAGE2_UNEXPECTED_EVENT_ENABLED: bool(
            ctx.unexpected_event_enabled
        ),
        AppStorageKeys.PAGE2_UNEXPECTED_EVENT_THRESHOLD: min(
            100,
            max(0, int(ctx.unexpected_event_threshold)),
        ),
        AppStorageKeys.PAGE2_TEMPERATURE: float(ctx.temperature),
        AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER: normalize_video_model(
            ctx.selected_video_generation_provider
        ),
    }
    if all(settings.get(key) == value for key, value in updates.items()):
        return False

    settings.set_many_atomic(updates)
    return True


def build_context_messages(
    turns: Iterable[Page2ConversationTurn],
    context_turn_count: int,
) -> list[dict]:
    turns_list = [
        turn
        for turn in turns
        if not bool(getattr(turn, "is_context_excluded", False))
    ]
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


def _send_with_current_chat_model(
    *,
    ctx: Page2Context,
    context_messages: list[dict],
    user_message: str,
    system_prompt: str | None = None,
) -> str:
    effective_system_prompt = ctx.system_prompt if system_prompt is None else system_prompt
    deepseek_model = deepseek_api_model_for_chat_model(ctx.selected_chat_model)
    if deepseek_model is not None:
        return DeepSeekAPIClient.send_message(
            system_prompt=effective_system_prompt,
            context_messages=context_messages,
            user_message=user_message,
            temperature=ctx.temperature,
            model=deepseek_model,
            thinking_enabled=ctx.chat_reasoning_enabled,
            reasoning_effort="high" if ctx.chat_reasoning_enabled else None,
        )

    openrouter_model = openrouter_api_model_for_chat_model(ctx.selected_chat_model)
    if openrouter_model is not None:
        return OpenRouterChatAPIClient.send_message(
            system_prompt=effective_system_prompt,
            context_messages=context_messages,
            user_message=user_message,
            temperature=ctx.temperature,
            model=openrouter_model,
        )

    grok_model = grok_api_model_for_chat_model(ctx.selected_chat_model) or "grok-4.3"
    grok_requires_reasoning = grok_model == "grok-4.6"
    return GrokAPIClient.send_message(
        system_prompt=effective_system_prompt,
        context_messages=context_messages,
        user_message=user_message,
        model=grok_model,
        temperature=ctx.temperature,
        thinking_enabled=True if grok_requires_reasoning else ctx.chat_reasoning_enabled,
        reasoning_effort=(
            "high"
            if ctx.chat_reasoning_enabled
            else "low" if grok_requires_reasoning else None
        ),
    )


def send_message(
    *,
    ctx: Page2Context,
    turns: list[Page2ConversationTurn],
    user_message: str,
    story_brain_short: str = "",
    story_brain_enabled: bool = True,
) -> str:
    context_messages = build_context_messages(turns, ctx.context_turn_count)
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

    return _send_with_current_chat_model(
        ctx=ctx,
        context_messages=context_messages,
        user_message=user_message_for_model,
    )


def generate_short_story_brain(
    *,
    ctx: Page2Context,
    turns: list[Page2ConversationTurn],
    story_brain_short: str,
    long_term_memory_prompt: str = "",
    history_turn_count: int | None = None,
) -> str:
    history_turns = max(
        1,
        int(
            ctx.story_brain_turns
            if history_turn_count is None
            else history_turn_count
        ),
    )
    existing_story_brain = str(story_brain_short or "").strip()
    if existing_story_brain in INVALID_STORY_BRAIN_TEXTS:
        existing_story_brain = ""
    generator_user_message = f"""
过去的记录：
{_turn_history_text(turns, history_turns) or "暂无"}

现有 Story Brain：
{existing_story_brain or "暂无"}
""".strip()

    story_brain_system_prompt = (
        str(long_term_memory_prompt or "").strip()
        or DEFAULT_STORY_BRAIN_GENERATOR_PROMPT
    )
    story_brain_ctx = replace(
        ctx,
        selected_chat_model=normalize_story_brain_update_model(
            ctx.story_brain_update_model
        ),
        chat_reasoning_enabled=ctx.story_brain_reasoning_enabled,
    )
    raw_text = _send_with_current_chat_model(
        ctx=story_brain_ctx,
        system_prompt=story_brain_system_prompt,
        context_messages=[],
        user_message=generator_user_message,
    )

    updated_story_brain = _strip_plain_text_output(raw_text)
    if updated_story_brain in INVALID_STORY_BRAIN_TEXTS:
        raise ValueError("Story Brain 更新模型没有返回可用内容。")
    return updated_story_brain or existing_story_brain


def generate_image_prompt(
    *,
    ctx: Page2Context,
    turns: list[Page2ConversationTurn],
    story_brain_short: str = "",
    story_brain_enabled: bool = True,
    mode: str = "normal",
    subject: str = "",
) -> str:
    if not build_recent_image_prompt_conversation(turns):
        raise ValueError("暂无助手回复内容。")

    user_message = build_image_prompt_system_prompt(mode, subject)
    if story_brain_enabled:
        user_message = _inject_short_story_brain_into_user_prompt(
            user_message,
            story_brain_short,
        )
    reply = _strip_plain_text_output(_send_with_current_chat_model(
        ctx=ctx,
        context_messages=build_context_messages(turns, ctx.context_turn_count),
        user_message=user_message,
    ))
    if not reply or reply in INVALID_STORY_BRAIN_TEXTS:
        raise ValueError("模型没有返回可用的图片 prompt，请重试。")
    return reply


def generate_video_prompt(*, ctx: Page2Context, image_prompt: str, story_text: str) -> str:
    image_prompt = str(image_prompt or "").strip()
    story_text = str(story_text or "").strip()
    if not image_prompt:
        raise ValueError("这张图片缺少生成时的图片 prompt，无法自动生成视频 prompt。")
    if not story_text:
        raise ValueError("缺少本轮故事内容，无法生成视频 prompt。")
    reply = _strip_plain_text_output(_send_with_current_chat_model(
        ctx=ctx,
        system_prompt=VIDEO_PROMPT_SYSTEM_PROMPT,
        context_messages=[],
        user_message=f"图片prompt：\n{image_prompt}\n\n本轮故事内容：\n{story_text}",
    ))
    if not reply or reply in INVALID_STORY_BRAIN_TEXTS:
        raise ValueError("模型没有返回可用的视频 prompt，请重试。")
    return reply


def generate_image(
    provider: str,
    prompt: str,
    image_urls: Optional[list[str]] = None,
    aspect_ratio: str = IMAGE_ASPECT_RATIO,
    refiner_prompt: str = "",
) -> GeneratedImageRecord:
    provider = str(provider or "").strip()
    if provider == "grokQuality":
        provider = "grokpro2k"
    prompt = str(prompt or "").strip()
    refiner_prompt = str(refiner_prompt or "").strip()
    image_urls = image_urls or []
    image_urls = [str(url).strip() for url in image_urls if str(url).strip()]

    if not prompt:
        raise ValueError("你得先点“生成图片prompt”生成prompt才能点这个生图")

    if aspect_ratio not in IMAGE_ASPECT_RATIOS:
        raise ValueError("图片尺寸请选择 3:4、4:3 或 1:1。")

    if provider in ["grok", "grokpro2k", "grokPro"]:
        model = {
            "grok": "grok-imagine-image",
            "grokpro2k": "grok-imagine-image-2.0",
            "grokPro": "grok-imagine-image-2.0",
        }.get(provider, "grok-imagine-image")

        result = GrokImageAPIClient.generate_image(
            prompt=prompt,
            image_urls=image_urls,
            model=model,
            resolution="1k" if provider == "grokPro" else "2k",
            quality={"grokPro": "low", "grokpro2k": "medium"}.get(provider),
            aspect_ratio=aspect_ratio,
        )
    else:
        result = ReplicateImageAPIClient.generate_image(
            provider=provider,
            prompt=prompt,
            image_urls=image_urls,
            aspect_ratio=aspect_ratio,
            refiner_prompt=refiner_prompt,
        )

    image_url = getattr(result, "image_url", None)
    image_base64 = getattr(result, "image_data_base64", None)

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


def _video_input_image_bytes(source_record: GeneratedImageRecord) -> bytes:
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
    return image_bytes


def _video_aspect_ratio(image_bytes: bytes, supported_ratios: tuple[str, ...]) -> str:
    with Image.open(io.BytesIO(image_bytes)) as original:
        image = ImageOps.exif_transpose(original)
        width, height = image.size
    ratio = width / height
    return min(
        supported_ratios,
        key=lambda item: abs(math.log(ratio / (int(item.split(":")[0]) / int(item.split(":")[1])))),
    )


def _prepare_video_cloudinary_image_url(
    source_record: GeneratedImageRecord, *, image_bytes: bytes | None = None,
) -> str:
    if image_bytes is None:
        image_bytes = _video_input_image_bytes(source_record)

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
    seconds: int | float,
    generate_audio: bool = True,
) -> GeneratedImageRecord:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("视频 prompt 不能为空，请先生成或填写视频 prompt。")

    provider = str(ctx.selected_video_generation_provider or WAN_MODEL).strip()
    validate_video_duration(provider, seconds)
    image_bytes = _video_input_image_bytes(source_record)
    image_reference = _prepare_video_cloudinary_image_url(source_record, image_bytes=image_bytes)

    if provider == WAN_MODEL:
        video_url = ReplicateVideoAPIClient.generate_wan_2_2_i2v_fast(
            image_reference, prompt, seconds=seconds,
        )
        effective_seconds = WAN_DURATION_FRAMES[seconds] / WAN_FPS
    else:
        aspect_ratio = _video_aspect_ratio(image_bytes, VIDEO_MODELS[provider].aspect_ratios)
        video_url = OpenRouterVideoAPIClient.generate_video(
            model=provider,
            image_url=image_reference,
            prompt=prompt,
            seconds=seconds,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
        )
        effective_seconds = int(seconds)

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
    story_brain_next_update_turn: int | None = None,
    story_brain_last_update_turn_id: str = "",
    story_brain_checkpoints: list | None = None,
    final_setting_id: str = "",
    scope: str | None = None,
    hidden: bool = False,
) -> str:
    has_short_story_brain = bool(str(story_brain_short or "").strip())
    if not record_id and not turns and not generated_media and not has_short_story_brain:
        return str(record_id or "").strip()

    now = now_iso()
    record_id = ensure_record_id(record_id)

    existing_title = None
    existing_created_at = None
    existing_hidden = None
    existing_branch_group_id = ""
    existing_branch_number = None
    for item in ChatRecordStore.load_index(scope=scope):
        if item.id == record_id:
            existing_title = item.title
            existing_created_at = item.created_at
            existing_hidden = bool(getattr(item, "hidden", False))
            existing_branch_group_id = str(
                getattr(item, "branch_group_id", "") or ""
            )
            existing_branch_number = getattr(item, "branch_number", None)
            break

    record = ChatRecord(
        id=record_id,
        title=existing_title or make_record_title(turns),
        turns=turns,
        system_prompt=system_prompt,
        generated_images=generated_media,
        story_brain=story_brain,
        story_brain_short=str(story_brain_short or "").strip(),
        story_brain_next_update_turn=(
            max(1, int(story_brain_next_update_turn))
            if story_brain_next_update_turn is not None
            else None
        ),
        story_brain_last_update_turn_id=str(
            story_brain_last_update_turn_id or ""
        ),
        final_setting_id=str(final_setting_id or ""),
        story_brain_checkpoints=list(story_brain_checkpoints or [])[-2:],
        branch_group_id=existing_branch_group_id,
        branch_number=existing_branch_number,
        # 已有记录沿用创建时的账号归属；新记录按当前账号标记。
        hidden=existing_hidden if existing_hidden is not None else bool(hidden),
        created_at=existing_created_at or now,
        updated_at=now,
    )
    ChatRecordStore.save_or_update_record(record, scope=scope)
    return record_id
