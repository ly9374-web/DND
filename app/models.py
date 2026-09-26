from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4

from app.default_prompts.first_inputs import (
    FIRST_INPUT_DEFAULTS_VERSION,
    OLD_PLAYER_FIRST_INPUT,
    OLD_PLAYER_FIRST_INPUT_TITLE,
)

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def new_id():
    return str(uuid4())


def optional_positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class Page2ChatModel(str, Enum):
    GROK1 = "grok1"
    GROK2 = "grok2"
    DEEPSEEK_PRO = "deepseekPro"
    DEEPSEEK_FLASH = "deepseekFlash"
    MISTRAL_LARGE_2512 = "mistralLarge2512"


CHAT_MODEL_OPTIONS = [model.value for model in Page2ChatModel]
CHAT_MODEL_LABELS = {
    Page2ChatModel.GROK1.value: "Grok 4.3",
    Page2ChatModel.GROK2.value: "Grok 4.6",
    Page2ChatModel.DEEPSEEK_PRO.value: "DeepSeek Pro",
    Page2ChatModel.DEEPSEEK_FLASH.value: "DeepSeek Flash",
    Page2ChatModel.MISTRAL_LARGE_2512.value: "Mistral Large 3 2512",
}
GROK_CHAT_MODEL_IDS = {
    Page2ChatModel.GROK1.value: "grok-4.3",
    Page2ChatModel.GROK2.value: "grok-4.6",
}
DEEPSEEK_CHAT_MODEL_IDS = {
    Page2ChatModel.DEEPSEEK_PRO.value: "deepseek-v4-pro",
    Page2ChatModel.DEEPSEEK_FLASH.value: "deepseek-v4-flash",
}
OPENROUTER_CHAT_MODEL_IDS = {
    Page2ChatModel.MISTRAL_LARGE_2512.value: "mistralai/mistral-large-2512",
}


def normalize_chat_model(value, default: str = Page2ChatModel.GROK1.value) -> str:
    model = str(value or "").strip()
    if model == "deepseek":
        return Page2ChatModel.DEEPSEEK_FLASH.value
    if model in CHAT_MODEL_OPTIONS:
        return model
    return default


def deepseek_api_model_for_chat_model(value) -> str | None:
    return DEEPSEEK_CHAT_MODEL_IDS.get(normalize_chat_model(value))


def grok_api_model_for_chat_model(value) -> str | None:
    return GROK_CHAT_MODEL_IDS.get(normalize_chat_model(value))


def openrouter_api_model_for_chat_model(value) -> str | None:
    return OPENROUTER_CHAT_MODEL_IDS.get(normalize_chat_model(value))


def chat_model_supports_reasoning(value) -> bool:
    return openrouter_api_model_for_chat_model(value) is None


class GeneratedMediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


@dataclass
class FirstInputRecord:
    title: str
    content: str
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @staticmethod
    def from_dict(data):
        return FirstInputRecord(
            id=data.get("id") or new_id(),
            title=data.get("title") or data.get("name") or "",
            content=data.get("content") or data.get("text") or "",
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SystemPromptRecord:
    title: str
    prompt: str
    # Legacy compatibility mirror of the currently selected first-input content.
    first_input: str = ""
    first_inputs: list[FirstInputRecord] = field(default_factory=list)
    selected_first_input_id: str = ""
    long_term_memory: str = ""
    final_template_id: str = ""
    final_world_book_id: str = ""
    final_long_term_memory_id: str = ""
    final_user_character_id: str = ""
    final_other_character_ids: list[str] = field(default_factory=list)
    final_user_character_name: str = ""
    final_other_character_names: list[str] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    first_inputs_need_migration: bool = field(default=False, repr=False, compare=False)
    first_input_defaults_version: int = 0

    def __post_init__(self):
        normalized_inputs: list[FirstInputRecord] = []
        for item in list(self.first_inputs or []):
            if isinstance(item, FirstInputRecord):
                normalized_inputs.append(item)
            elif isinstance(item, dict):
                normalized_inputs.append(FirstInputRecord.from_dict(item))
        if not normalized_inputs:
            normalized_inputs = [
                FirstInputRecord(
                    title="默认首轮输入",
                    content=str(self.first_input or ""),
                )
            ]
        self.first_inputs = normalized_inputs
        # Seed each record once. Preserve the selected input and any customized
        # preset; subsequent edits/deletions must not resurrect the default.
        if self.first_input_defaults_version < FIRST_INPUT_DEFAULTS_VERSION:
            if not any(
                item.title.strip() == OLD_PLAYER_FIRST_INPUT_TITLE
                or item.content.strip() == OLD_PLAYER_FIRST_INPUT
                for item in self.first_inputs
            ):
                self.first_inputs.append(FirstInputRecord(
                    title=OLD_PLAYER_FIRST_INPUT_TITLE,
                    content=OLD_PLAYER_FIRST_INPUT,
                ))
            self.first_input_defaults_version = FIRST_INPUT_DEFAULTS_VERSION
            self.first_inputs_need_migration = True
        if not any(item.id == self.selected_first_input_id for item in self.first_inputs):
            self.selected_first_input_id = self.first_inputs[0].id
        selected = next(
            item for item in self.first_inputs if item.id == self.selected_first_input_id
        )
        self.first_input = str(selected.content or "")

    @staticmethod
    def from_dict(data):
        legacy_first_input = (
            data.get("first_input")
            or data.get("firstInput")
            or data.get("first_reply")
            or data.get("firstReply")
            or ""
        )
        record = SystemPromptRecord(
            id=data.get("id") or new_id(),
            title=data.get("title", "未命名记录"),
            prompt=data.get("prompt", ""),
            first_input=legacy_first_input,
            first_inputs=[
                FirstInputRecord.from_dict(item)
                for item in (data.get("first_inputs") or data.get("firstInputs") or [])
                if isinstance(item, dict)
            ],
            selected_first_input_id=(
                data.get("selected_first_input_id")
                or data.get("selectedFirstInputID")
                or ""
            ),
            long_term_memory=(
                data.get("long_term_memory")
                or data.get("longTermMemory")
                or ""
            ),
            final_template_id=data.get("final_template_id") or data.get("finalTemplateID") or "",
            final_world_book_id=data.get("final_world_book_id") or data.get("finalWorldBookID") or "",
            final_long_term_memory_id=(
                data.get("final_long_term_memory_id")
                or data.get("finalLongTermMemoryID")
                or ""
            ),
            final_user_character_id=(
                data.get("final_user_character_id")
                or data.get("finalUserCharacterID")
                or ""
            ),
            final_other_character_ids=list(
                data.get("final_other_character_ids")
                or data.get("finalOtherCharacterIDs")
                or []
            ),
            final_user_character_name=(
                data.get("final_user_character_name")
                or data.get("finalUserCharacterName")
                or ""
            ),
            final_other_character_names=list(
                data.get("final_other_character_names")
                or data.get("finalOtherCharacterNames")
                or []
            ),
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
            first_inputs_need_migration=not bool(
                data.get("first_inputs") or data.get("firstInputs")
            ),
            first_input_defaults_version=(
                optional_positive_int(data.get("first_input_defaults_version")) or 0
            ),
        )
        return record

    def to_dict(self):
        self.__post_init__()
        self.first_inputs_need_migration = False
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "first_input": self.first_input,
            "first_inputs": [item.to_dict() for item in self.first_inputs],
            "selected_first_input_id": self.selected_first_input_id,
            "first_input_defaults_version": self.first_input_defaults_version,
            "long_term_memory": self.long_term_memory,
            "final_template_id": self.final_template_id,
            "final_world_book_id": self.final_world_book_id,
            "final_long_term_memory_id": self.final_long_term_memory_id,
            "final_user_character_id": self.final_user_character_id,
            "final_other_character_ids": list(self.final_other_character_ids),
            "final_user_character_name": self.final_user_character_name,
            "final_other_character_names": list(self.final_other_character_names),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PromptTextRecord:
    title: str
    content: str
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @staticmethod
    def from_dict(data):
        return PromptTextRecord(
            id=data.get("id") or new_id(),
            title=data.get("title", ""),
            content=data.get("content", ""),
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class FixedTemplateRecord:
    title: str
    background_intro: str
    dm_core_tasks: str
    response_format: str
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @staticmethod
    def from_dict(data):
        return FixedTemplateRecord(
            id=data.get("id") or new_id(),
            title=data.get("title", ""),
            background_intro=data.get("background_intro") or data.get("backgroundIntro") or "",
            dm_core_tasks=data.get("dm_core_tasks") or data.get("dmCoreTasks") or "",
            response_format=data.get("response_format") or data.get("responseFormat") or "",
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "background_intro": self.background_intro,
            "dm_core_tasks": self.dm_core_tasks,
            "response_format": self.response_format,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Page2ConversationTurn:
    user_message: str = ""
    assistant_message: str = None
    is_loading: bool = False
    is_user_message_hidden: bool = False
    is_context_excluded: bool = False
    assistant_audio_filename: str = ""
    id: str = field(default_factory=new_id)
    date: str = field(default_factory=now_iso)

    @staticmethod
    def from_dict(data):
        return Page2ConversationTurn(
            id=data.get("id") or new_id(),
            user_message=data.get("user_message") or data.get("userMessage") or "",
            assistant_message=data.get("assistant_message") or data.get("assistantMessage"),
            is_loading=bool(data.get("is_loading") if "is_loading" in data else data.get("isLoading", False)),
            is_user_message_hidden=bool(
                data.get("is_user_message_hidden")
                if "is_user_message_hidden" in data
                else data.get("isUserMessageHidden", False)
            ),
            is_context_excluded=bool(
                data.get("is_context_excluded")
                if "is_context_excluded" in data
                else data.get("isContextExcluded", False)
            ),
            assistant_audio_filename=str(
                data.get("assistant_audio_filename")
                if "assistant_audio_filename" in data
                else data.get("assistantAudioFilename", "") or ""
            ),
            date=data.get("date") or now_iso(),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "user_message": self.user_message,
            "assistant_message": self.assistant_message,
            "is_loading": self.is_loading,
            "is_user_message_hidden": self.is_user_message_hidden,
            "is_context_excluded": self.is_context_excluded,
            "assistant_audio_filename": str(self.assistant_audio_filename or ""),
            "date": self.date,
        }


@dataclass
class GeneratedImageRecord:
    provider: str
    prompt: str
    media_kind: GeneratedMediaKind = GeneratedMediaKind.IMAGE
    image_url_string: str = None
    image_data_base64: str = None
    image_input_urls: list = field(default_factory=list)
    video_url_string: str = None
    source_image_url_string: str = None
    source_image_data_base64: str = None
    source_image_id: str = ""
    duration_seconds: float = None
    video_generation_provider: str = None
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    source_turn_id: str = ""
    previous_source_turn_id: str = ""
    video_prompt: str = ""
    video_story_text: str = ""
    # Distinguish new temporary base64 outputs from legacy explicitly saved images.
    media_managed: bool = False

    @staticmethod
    def from_dict(data):
        media_kind = data.get("media_kind") or data.get("mediaKind") or "image"
        try:
            media_kind = GeneratedMediaKind(media_kind)
        except Exception:
            media_kind = GeneratedMediaKind.IMAGE

        return GeneratedImageRecord(
            id=data.get("id") or new_id(),
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            provider=data.get("provider", ""),
            prompt=data.get("prompt", ""),
            media_kind=media_kind,
            image_url_string=data.get("image_url_string") or data.get("imageURLString"),
            image_data_base64=data.get("image_data_base64") or data.get("imageDataBase64"),
            image_input_urls=data.get("image_input_urls") or data.get("imageInputURLs") or [],
            video_url_string=data.get("video_url_string") or data.get("videoURLString"),
            source_image_url_string=data.get("source_image_url_string") or data.get("sourceImageURLString"),
            source_image_data_base64=data.get("source_image_data_base64") or data.get("sourceImageDataBase64"),
            source_image_id=str(data.get("source_image_id") or ""),
            duration_seconds=data.get("duration_seconds") or data.get("durationSeconds"),
            video_generation_provider=data.get("video_generation_provider") or data.get("videoGenerationProvider"),
            source_turn_id=str(data.get("source_turn_id") or ""),
            previous_source_turn_id=str(data.get("previous_source_turn_id") or ""),
            video_prompt=str(data.get("video_prompt") or ""),
            video_story_text=str(data.get("video_story_text") or ""),
            media_managed=bool(data.get("media_managed", False)),
        )

    def to_dict(self):
        media_kind = self.media_kind.value if hasattr(self.media_kind, "value") else self.media_kind
        return {
            "id": self.id,
            "created_at": self.created_at,
            "provider": self.provider,
            "prompt": self.prompt,
            "media_kind": media_kind,
            "image_url_string": self.image_url_string,
            "image_data_base64": self.image_data_base64,
            "image_input_urls": self.image_input_urls,
            "video_url_string": self.video_url_string,
            "source_image_url_string": self.source_image_url_string,
            "source_image_data_base64": self.source_image_data_base64,
            "source_image_id": self.source_image_id,
            "duration_seconds": self.duration_seconds,
            "video_generation_provider": self.video_generation_provider,
            "source_turn_id": self.source_turn_id,
            "previous_source_turn_id": self.previous_source_turn_id,
            "video_prompt": self.video_prompt,
            "video_story_text": self.video_story_text,
            "media_managed": self.media_managed,
        }


@dataclass
class StoredImageURLRecord:
    title: str
    url: str
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @staticmethod
    def from_dict(data):
        return StoredImageURLRecord(
            id=data.get("id") or new_id(),
            title=data.get("title", "url"),
            url=data.get("url", ""),
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ChatRecord:
    title: str
    turns: list
    system_prompt: str
    generated_images: list = field(default_factory=list)
    # Compatibility archive only. The independent ordinary edition never reads,
    # displays, updates, or sends this value to a model, but it round-trips the
    # original JSON so opening and saving an old record cannot erase its data.
    story_brain: object = field(default_factory=dict)
    story_brain_short: str = ""
    story_brain_next_update_turn: int | None = None
    story_brain_last_update_turn_id: str = ""
    # At most two versions: current and previous, never a per-turn memory archive.
    story_brain_checkpoints: list = field(default_factory=list)
    final_setting_id: str = ""
    # Records created from the same conversation snapshot share a branch group.
    # The number is kept separately from the title so later manual renames do not
    # break the next-number calculation.
    branch_group_id: str = ""
    branch_number: int | None = None
    # 记录是否属于隐藏账号（在隐藏账号中创建的聊天记录对普通账号不可见）。
    hidden: bool = False
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @staticmethod
    def from_dict(data):
        turns = [
            Page2ConversationTurn.from_dict(item)
            for item in data.get("turns", [])
            if isinstance(item, dict)
        ]

        generated_images = [
            GeneratedImageRecord.from_dict(item)
            for item in (data.get("generated_images") or data.get("generatedImages") or [])
            if isinstance(item, dict)
        ]

        return ChatRecord(
            id=data.get("id") or new_id(),
            title=data.get("title", "聊天记录"),
            turns=turns,
            system_prompt=data.get("system_prompt") or data.get("systemPrompt") or "",
            generated_images=generated_images,
            story_brain=deepcopy(
                data.get("story_brain")
                if "story_brain" in data
                else data.get("storyBrain", {})
            ),
            story_brain_short=data.get("story_brain_short") or data.get("storyBrainShort") or "",
            story_brain_next_update_turn=optional_positive_int(
                data.get("story_brain_next_update_turn")
                if "story_brain_next_update_turn" in data
                else data.get("storyBrainNextUpdateTurn")
            ),
            story_brain_last_update_turn_id=str(
                data.get("story_brain_last_update_turn_id")
                or data.get("storyBrainLastUpdateTurnID")
                or ""
            ),
            final_setting_id=(
                data.get("final_setting_id")
                or data.get("finalSettingID")
                or ""
            ),
            branch_group_id=str(
                data.get("branch_group_id") or data.get("branchGroupID") or ""
            ),
            branch_number=optional_positive_int(
                data.get("branch_number")
                if "branch_number" in data
                else data.get("branchNumber")
            ),
            story_brain_checkpoints=deepcopy([
                item for item in (data.get("story_brain_checkpoints") or [])
                if isinstance(item, dict) and isinstance(item.get("state"), dict)
            ][-2:]),
            hidden=bool(data.get("hidden", False)),
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "turns": [
                turn.to_dict() if hasattr(turn, "to_dict") else turn
                for turn in self.turns
            ],
            "system_prompt": self.system_prompt,
            "generated_images": [
                image.to_dict() if hasattr(image, "to_dict") else image
                for image in self.generated_images
            ],
            "story_brain": deepcopy(self.story_brain),
            "story_brain_short": str(self.story_brain_short or "").strip(),
            "story_brain_next_update_turn": optional_positive_int(
                self.story_brain_next_update_turn
            ),
            "story_brain_last_update_turn_id": str(
                self.story_brain_last_update_turn_id or ""
            ),
            "final_setting_id": str(self.final_setting_id or ""),
            "branch_group_id": str(self.branch_group_id or ""),
            "branch_number": optional_positive_int(self.branch_number),
            "story_brain_checkpoints": deepcopy(self.story_brain_checkpoints[-2:]),
            "hidden": bool(self.hidden),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ChatRecordIndexItem:
    id: str
    title: str
    created_at: str
    updated_at: str
    file_name: str
    hidden: bool = False
    branch_group_id: str = ""
    branch_number: int | None = None

    @staticmethod
    def from_dict(data):
        return ChatRecordIndexItem(
            id=data.get("id", ""),
            title=data.get("title", "未命名聊天"),
            created_at=data.get("created_at") or data.get("createdAt") or now_iso(),
            updated_at=data.get("updated_at") or data.get("updatedAt") or now_iso(),
            file_name=data.get("file_name") or data.get("fileName") or "",
            hidden=bool(data.get("hidden", False)),
            branch_group_id=str(
                data.get("branch_group_id") or data.get("branchGroupID") or ""
            ),
            branch_number=optional_positive_int(
                data.get("branch_number")
                if "branch_number" in data
                else data.get("branchNumber")
            ),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "file_name": self.file_name,
            "hidden": bool(self.hidden),
            "branch_group_id": str(self.branch_group_id or ""),
            "branch_number": optional_positive_int(self.branch_number),
        }
