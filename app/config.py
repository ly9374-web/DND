import json
import os
import time
from pathlib import Path

import streamlit as st

from app.video_models import normalize_video_model


# =========================
# App 基础配置
# =========================

APP_TITLE = "DND"

# 数据保存目录
# Python 版没有 Swift 的 UserDefaults，所以我们用 json 文件模拟本地存储
DATA_DIR = Path(
    os.environ.get("VS_IMAGE_NOVEL_DATA_DIR")
    or (Path(__file__).resolve().parent.parent / ".python_app_data")
).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

USER_DEFAULTS_PATH = DATA_DIR / "user_defaults.json"
FINAL_SETTING_SYNC_BACKUP_PATH = DATA_DIR / "SyncBackup" / "user_defaults.before_last_sync.json"
CHAT_RECORDS_DIR = DATA_DIR / "ChatRecords"
CHAT_RECORDS_DIR.mkdir(parents=True, exist_ok=True)
CHAT_RECORD_DIR = CHAT_RECORDS_DIR


# =========================
# 对应 Swift: AppStorageKeys
# =========================

class AppStorageKeys:
    SYSTEM_PROMPT = "systemPrompt"
    PAGE2_CONTEXT_TURN_COUNT = "page2ContextTurnCount"
    PAGE2_SELECTED_CHAT_MODEL = "page2SelectedChatModel"
    PAGE2_CHAT_REASONING_ENABLED = "page2ChatReasoningEnabled"
    PAGE2_STORY_BRAIN_UPDATE_MODEL = "page2StoryBrainUpdateModel"
    PAGE2_STORY_BRAIN_REASONING_ENABLED = "page2StoryBrainReasoningEnabled"
    PAGE2_STORY_BRAIN_TURNS = "page2StoryBrainTurns"
    PAGE2_UNEXPECTED_EVENT_ENABLED = "page2UnexpectedEventEnabled"
    PAGE2_UNEXPECTED_EVENT_THRESHOLD = "page2UnexpectedEventThreshold"
    PAGE2_AUTO_IMAGE_ENABLED = "page2AutoImageEnabled"
    PAGE2_AUTO_IMAGE_INTERVAL = "page2AutoImageInterval"
    PAGE2_TEMPERATURE = "page2Temperature"
    PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER = "page2SelectedVideoGenerationProvider"
    PAGE2_SELECTED_TTS_VOICE = "page2SelectedTtsVoice"
    PAGE2_IMAGE_ASPECT_RATIOS = "page2ImageAspectRatios"
    PAGE2_LAST_IMAGE_PROVIDER = "page2LastImageProvider"
    PAGE2_IMAGE2_QUALITY = "page2Image2Quality"

    OPENROUTER_API_KEY = "openrouterApiKey"
    DASHSCOPE_API_KEY = "dashscopeApiKey"
    DEEPSEEK_API_KEY = "deepseekApiKey"
    KEYLINK_DEEPSEEK_V4_PRO_API_KEY = "keylinkDeepseekV4ProApiKey"
    KEYLINK_DEEPSEEK_V4_PRO_ENABLED = "keylinkDeepseekV4ProEnabled"

    SYSTEM_PROMPT_RECORDS = "systemPromptRecords"
    SELECTED_SYSTEM_PROMPT_RECORD_ID = "selectedSystemPromptRecordID"
    SYSTEM_PROMPT_RECORD_NEXT_INDEX = "systemPromptRecordNextIndex"

    HIDDEN_SYSTEM_PROMPT_RECORDS = "hiddenSystemPromptRecords"
    HIDDEN_SYSTEM_PROMPT_RECORD_NEXT_INDEX = "hiddenSystemPromptRecordNextIndex"

    WORLD_BOOK_RECORDS = "worldBookRecords"
    CHARACTER_CARD_RECORDS = "characterCardRecords"
    LONG_TERM_MEMORY_RECORDS = "longTermMemoryRecords"
    QUICK_INPUT_RECORDS = "quickInputRecords"
    HIDDEN_QUICK_INPUT_RECORDS = "hiddenQuickInputRecords"
    QUICK_INPUT_DEFAULT_SEEDED = "quickInputDefaultSeeded"
    QUICK_INPUT_IMAGE_REMOVAL_MIGRATED = "quickInputImageRemovalMigrated"
    LONG_TERM_MEMORY_DEFAULT_SEEDED = "longTermMemoryDefaultSeeded"
    DEFAULT_LONG_TERM_MEMORY_RECORD_ID = "defaultLongTermMemoryRecordID"
    HIDDEN_DEFAULT_LONG_TERM_MEMORY_RECORD_ID = "hiddenDefaultLongTermMemoryRecordID"
    FIXED_TEMPLATE_RECORDS = "fixedTemplateRecords"
    SELECTED_FIXED_TEMPLATE_RECORD_ID = "selectedFixedTemplateRecordID"
    FIXED_TEMPLATE_DEFAULT_SEEDED = "fixedTemplateDefaultSeeded"
    PUBLIC_PROMPT_ASSET_SEED_VERSION = "publicPromptAssetSeedVersion"

    HIDDEN_WORLD_BOOK_RECORDS = "hiddenWorldBookRecords"
    HIDDEN_CHARACTER_CARD_RECORDS = "hiddenCharacterCardRecords"
    HIDDEN_LONG_TERM_MEMORY_RECORDS = "hiddenLongTermMemoryRecords"
    HIDDEN_FIXED_TEMPLATE_RECORDS = "hiddenFixedTemplateRecords"

    CHAT_RECORDS = "chatRecords"
    CHAT_RECORD_NEXT_INDEX = "chatRecordNextIndex"
    CHAT_RECORD_INDEX = "chatRecordIndex"
    GUEST_CHAT_RECORD_INDEX = "guestChatRecordIndex"

    STORED_IMAGE_URL_RECORDS = "storedImageURLRecords"
    STORED_IMAGE_URL_RECORD_NEXT_INDEX = "storedImageURLRecordNextIndex"

    HIDDEN_URL_RECORDS = "hiddenURLRecords"
    HIDDEN_URL_RECORD_NEXT_INDEX = "hiddenURLRecordNextIndex"

    XAI_API_KEY = "xaiApiKey"
    XAI_CHAT_API_KEY = "xaiChatApiKey"
    XAI_IMAGE_API_KEY = "xaiImageApiKey"

    REPLICATE_API_TOKEN = "replicateApiToken"
    CLOUDINARY_API_KEY = "cloudinaryApiKey"
    CLOUDINARY_API_SECRET = "cloudinaryApiSecret"

    DEBUG_LOG_ENABLED = "debugLogEnabled"


# =========================
# 对应 Swift: Layout
# =========================

class Layout:
    PAGE_PADDING = 24

    BUTTON_WIDTH = 220
    BUTTON_HEIGHT = 52
    BUTTON_CORNER_RADIUS = 12
    HOME_BUTTON_SPACING = 18

    TITLE_TOP_PADDING = 18
    TITLE_BOTTOM_PADDING = 12

    SYSTEM_PROMPT_INPUT_HEIGHT = 440
    SYSTEM_PROMPT_INPUT_TOP_PADDING = 8
    SYSTEM_PROMPT_INPUT_CORNER_RADIUS = 12
    SYSTEM_PROMPT_INPUT_BORDER_WIDTH = 1


# =========================
# 对应 Swift @AppStorage / UserDefaults
# =========================

class SettingsStore:
    """
    用 json 文件模拟 Swift 的 UserDefaults / @AppStorage。
    Swift 里是：
        @AppStorage(AppStorageKeys.systemPrompt) private var systemPrompt: String = ""

    Python 里用：
        settings.get(AppStorageKeys.SYSTEM_PROMPT, "")
        settings.set(AppStorageKeys.SYSTEM_PROMPT, value)
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.values = {}
        self.load()
        self._migrate_video_settings()

    def _migrate_video_settings(self):
        # Only retire credentials and the active selection; media history stays intact.
        changed = False
        for key in ("domoaiApiKey", "zhipuApiKey"):
            if key in self.values:
                del self.values[key]
                changed = True
        key = AppStorageKeys.PAGE2_SELECTED_VIDEO_GENERATION_PROVIDER
        if key in self.values:
            model = normalize_video_model(self.values[key])
            if model != self.values[key]:
                self.values[key] = model
                changed = True
        if changed:
            self.save()

    def load(self):
        if not self.path.exists():
            self.values = {}
            return

        try:
            text = self.path.read_text(encoding="utf-8")
            self.values = json.loads(text)
        except Exception as exc:
            # 文件损坏（如写入中途被打断导致 JSON 截断）时，绝不静默置空——
            # 否则下一次 save() 会把空状态写回磁盘，造成全部数据丢失。
            # 先把损坏文件转移保存，再尝试从最近备份恢复。
            corrupt_path = self.path.with_name(
                f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            try:
                os.replace(self.path, corrupt_path)
                debug_log(
                    f"[SettingsStore] 主文件损坏，已移至 {corrupt_path.name}: {exc}"
                )
            except Exception:
                corrupt_path = None
                debug_log(f"[SettingsStore] 主文件损坏且无法转移: {exc}")

            self.values = {}
            if corrupt_path is not None:
                backup = self.path.with_name(
                    self.path.name.replace(".json", "") + ".last-good.json"
                )
                if backup.exists():
                    try:
                        self.values = json.loads(
                            backup.read_text(encoding="utf-8")
                        )
                        debug_log(
                            f"[SettingsStore] 已从备份 {backup.name} 恢复 {len(self.values)} 个键"
                        )
                    except Exception as backup_exc:
                        debug_log(
                            f"[SettingsStore] 备份 {backup.name} 也无法读取: {backup_exc}"
                        )

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.values, ensure_ascii=False, indent=2)

        # 原子写入：先写临时文件再 os.replace，防止写入中途被打断
        # （进程被杀/断电）导致主文件截断损坏。
        data_tmp = self.path.with_name(f".{self.path.name}.tmp")
        try:
            data_tmp.write_text(payload, encoding="utf-8")

            # 每次成功写入前，保留一份最近完好的备份用于损坏自恢复。
            if self.path.exists():
                backup = self.path.with_name(
                    self.path.name.replace(".json", "") + ".last-good.json"
                )
                try:
                    backup.write_text(
                        self.path.read_text(encoding="utf-8"), encoding="utf-8"
                    )
                except Exception as backup_exc:
                    debug_log(f"[SettingsStore] 写滚动备份失败(不影响主流程): {backup_exc}")

            os.replace(data_tmp, self.path)
        except Exception:
            if data_tmp.exists():
                data_tmp.unlink()
            raise

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.save()

    def set_many_atomic(self, updates, *, backup_path=None):
        """Persist several setting values in one replace, with an optional rolling backup."""
        next_values = dict(self.values)
        next_values.update(dict(updates or {}))
        payload = json.dumps(next_values, ensure_ascii=False, indent=2)
        data_tmp = self.path.with_name(f".{self.path.name}.sync.tmp")
        backup_tmp = None

        try:
            if backup_path is not None and self.path.exists():
                backup_path = Path(backup_path)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_tmp = backup_path.with_name(f".{backup_path.name}.tmp")
                backup_tmp.write_bytes(self.path.read_bytes())
                os.replace(backup_tmp, backup_path)
                backup_tmp = None

            self.path.parent.mkdir(parents=True, exist_ok=True)
            data_tmp.write_text(payload, encoding="utf-8")
            os.replace(data_tmp, self.path)
        except Exception:
            if data_tmp.exists():
                data_tmp.unlink()
            if backup_tmp is not None and backup_tmp.exists():
                backup_tmp.unlink()
            raise

        self.values = next_values

    def bool(self, key, default=False):
        value = self.values.get(key, default)

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            return value.lower() in ["1", "true", "yes", "on"]

        return bool(value)

    def int(self, key, default=0):
        try:
            return int(self.values.get(key, default))
        except Exception:
            return default

    def float(self, key, default=0.0):
        try:
            return float(self.values.get(key, default))
        except Exception:
            return default


settings = SettingsStore(USER_DEFAULTS_PATH)


# =========================
# 工具函数
# =========================

def first_non_empty(*values):
    """
    返回第一个非空字符串。
    用于在多个本地持久化字段中保留旧 key 的兼容读取。
    """
    for value in values:
        if value is None:
            continue

        text = str(value).strip()
        if text:
            return text

    return ""


def get_effective_api_key(user_value: str | None, secret_name: str) -> str:
    """
    API Key 优先级：
    1. 网页内部用户填写并本地保存的值
    2. Streamlit Secrets 中的默认值
    3. 空字符串
    """
    user_text = str(user_value or "").strip()
    if user_text:
        return _require_latin1_api_value(user_text, secret_name)

    try:
        secret_value = st.secrets.get(secret_name, "")
    except Exception:
        secret_value = ""

    secret_text = str(secret_value or "").strip()
    if secret_text:
        return _require_latin1_api_value(secret_text, secret_name)

    return ""


def _require_latin1_api_value(value: str, secret_name: str) -> str:
    if not is_latin1_api_value(value):
        raise ValueError(
            secret_name
            + " 包含中文或其他无法用于 HTTP 认证的字符。"
            + "请在“模型”页面删除该项后重新填写正确的 Key，"
            + "或检查 Streamlit Secrets 中的同名配置。"
        )

    return value


def is_latin1_api_value(value: str | None) -> bool:
    try:
        str(value or "").encode("latin-1")
    except UnicodeEncodeError:
        return False

    return True


def has_streamlit_secret(secret_name: str) -> bool:
    try:
        secret_value = st.secrets.get(secret_name, "")
    except Exception:
        return False

    return bool(str(secret_value or "").strip())


def has_invalid_streamlit_secret(secret_name: str) -> bool:
    try:
        secret_value = st.secrets.get(secret_name, "")
    except Exception:
        return False

    secret_text = str(secret_value or "").strip()
    return bool(secret_text) and not is_latin1_api_value(secret_text)


def user_facing_error_message(exc: Exception) -> str:
    message = str(exc)
    if isinstance(exc, UnicodeEncodeError) or "'latin-1' codec can't encode" in message:
        return (
            "某个 API Key、Token、Secret 或 HTTP 认证字段里包含中文/特殊字符，"
            "无法放进请求头。请到“模型”页面查看是否有“字符异常”的项目，"
            "删除后重新填写正确值；如果显示默认 Key 字符异常，请检查 Streamlit Secrets。"
        )

    return message


def debug_log(*items):
    """Opt-in redacted debug output is printed and retained locally for 48 hours."""
    settings.load()
    if settings.bool(AppStorageKeys.DEBUG_LOG_ENABLED, False):
        from app import log_store
        message = log_store.format_message(*items)
        print(message, flush=True)
        log_store.start_maintenance(DATA_DIR / "logs")
        log_store.write(DATA_DIR / "logs", message=message)


def mask_authorization_header(value):
    """
    调试日志里隐藏 API Key，避免完整 key 出现在终端。
    """
    text = str(value or "").strip()
    prefix = "Bearer "

    if not text.startswith(prefix):
        return "***"

    token = text[len(prefix):].strip()

    if not token:
        return "Bearer ***"

    if len(token) <= 16:
        return "Bearer " + token[:4] + "***" + token[-2:]

    return "Bearer " + token[:10] + "***" + token[-6:]


# 兼容另一个可能用到的旧名字
masked_authorization_header = mask_authorization_header


# =========================
# 对应 Swift: XAIConfig
# =========================

class XAIConfig:
    @staticmethod
    def chat_api_key():
        grok_chat_api_key = first_non_empty(
            settings.get(AppStorageKeys.XAI_CHAT_API_KEY, ""),
            settings.get(AppStorageKeys.XAI_API_KEY, ""),
        )
        effective_grok_chat_api_key = get_effective_api_key(
            grok_chat_api_key,
            "GROK_CHAT_API_KEY",
        )
        return effective_grok_chat_api_key

    @staticmethod
    def image_api_key():
        grok_image_api_key = first_non_empty(
            settings.get(AppStorageKeys.XAI_IMAGE_API_KEY, ""),
            settings.get(AppStorageKeys.XAI_API_KEY, ""),
        )
        effective_grok_image_api_key = get_effective_api_key(
            grok_image_api_key,
            "GROK_IMAGE_API_KEY",
        )
        return effective_grok_image_api_key


# =========================
# 对应 Swift: ReplicateConfig
# =========================

class ReplicateConfig:
    @staticmethod
    def api_token():
        replicate_api_token = settings.get(AppStorageKeys.REPLICATE_API_TOKEN, "")
        effective_replicate_api_token = get_effective_api_key(
            replicate_api_token,
            "REPLICATE_API_TOKEN",
        )
        return effective_replicate_api_token


# =========================
# 对应 Swift: DeepSeekConfig
# =========================

class DeepSeekConfig:
    @staticmethod
    def api_key():
        deepseek_api_key = settings.get(AppStorageKeys.DEEPSEEK_API_KEY, "")
        effective_deepseek_api_key = get_effective_api_key(
            deepseek_api_key,
            "DEEPSEEK_API_KEY",
        )
        return effective_deepseek_api_key


# =========================
# KeyLink DeepSeek V4 Pro 配置
# =========================

class KeyLinkDeepSeekV4ProConfig:
    @staticmethod
    def api_key():
        api_key = str(
            settings.get(AppStorageKeys.KEYLINK_DEEPSEEK_V4_PRO_API_KEY, "") or ""
        ).strip()
        if not api_key:
            return ""
        return _require_latin1_api_value(api_key, "keylink-deepseek-v4-pro")

    @staticmethod
    def enabled():
        return settings.bool(AppStorageKeys.KEYLINK_DEEPSEEK_V4_PRO_ENABLED, False)


# =========================
# OpenRouter 视频配置
# =========================

class OpenRouterConfig:
    @staticmethod
    def api_key():
        return get_effective_api_key(
            settings.get(AppStorageKeys.OPENROUTER_API_KEY, ""),
            "OPENROUTER_API_KEY",
        )


# =========================
# Cloudinary 上传配置
# =========================

class CloudinaryConfig:
    DEFAULT_CLOUD_NAME = "dxi0op4os"

    @staticmethod
    def cloud_name():
        return CloudinaryConfig.DEFAULT_CLOUD_NAME

    @staticmethod
    def api_key():
        cloudinary_api_key = settings.get(AppStorageKeys.CLOUDINARY_API_KEY, "")
        effective_cloudinary_api_key = get_effective_api_key(
            cloudinary_api_key,
            "CLOUDINARY_API_KEY",
        )
        return effective_cloudinary_api_key

    @staticmethod
    def api_secret():
        cloudinary_api_secret = settings.get(AppStorageKeys.CLOUDINARY_API_SECRET, "")
        effective_cloudinary_api_secret = get_effective_api_key(
            cloudinary_api_secret,
            "CLOUDINARY_API_SECRET",
        )
        return effective_cloudinary_api_secret
