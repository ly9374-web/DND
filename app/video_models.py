"""Video capabilities verified against provider APIs on 2026-09-27."""

from dataclasses import dataclass


WAN_MODEL = "wan22Fast"
WAN3_MODEL = "alibaba/wan-3.0"
WAN_FPS = 16
WAN_DURATION_FRAMES = {5: 81, 6: 97, 7: 113, 7.5: 121}
WAN3_ASPECT_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16")
WAN3_RESOLUTIONS = ("480p", "720p", "1080p")


@dataclass(frozen=True)
class VideoModel:
    label: str
    durations: tuple[int | float, ...]
    audio: bool = False
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ("720p",)
    text_to_video: bool = False


SEEDANCE_ASPECT_RATIOS = ("1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21")
VIDEO_MODELS = {
    WAN_MODEL: VideoModel("Wan 2.2 I2V Fast", tuple(WAN_DURATION_FRAMES)),
    WAN3_MODEL: VideoModel(
        "Wan 3.0", tuple(range(2, 31)), True, WAN3_ASPECT_RATIOS,
        WAN3_RESOLUTIONS, True,
    ),
    "bytedance/seedance-2.0-mini": VideoModel(
        "Seedance 2.0 Mini", tuple(range(4, 16)), True, SEEDANCE_ASPECT_RATIOS,
    ),
    "bytedance/seedance-1-5-pro": VideoModel(
        "Seedance 1.5 Pro", tuple(range(4, 13)), True, SEEDANCE_ASPECT_RATIOS,
    ),
}


def normalize_video_model(value) -> str:
    model = str(value or "").strip()
    return model if model in VIDEO_MODELS else WAN_MODEL


def validate_video_duration(model: str, seconds: int | float) -> None:
    if model not in VIDEO_MODELS:
        raise ValueError("不支持的视频模型，请重新选择。")
    if isinstance(seconds, bool) or seconds not in VIDEO_MODELS[model].durations:
        supported = "、".join(str(item) for item in VIDEO_MODELS[model].durations)
        raise ValueError(f"{VIDEO_MODELS[model].label} 的时长只支持：{supported} 秒。")


def validate_video_resolution(model: str, resolution: str) -> None:
    if model not in VIDEO_MODELS or resolution not in VIDEO_MODELS[model].resolutions:
        raise ValueError("当前视频模型不支持此分辨率。")


def video_duration_label(model: str, seconds: int | float) -> str:
    return f"约 {seconds:g} 秒" if model == WAN_MODEL else f"{seconds:g} 秒"
