"""Video capabilities verified against provider APIs on 2026-09-10."""

from dataclasses import dataclass


WAN_MODEL = "wan22Fast"
WAN_FPS = 16
WAN_DURATION_FRAMES = {5: 81, 6: 97, 7: 113, 7.5: 121}


@dataclass(frozen=True)
class VideoModel:
    label: str
    durations: tuple[int | float, ...]
    audio: bool = False
    aspect_ratios: tuple[str, ...] = ()


SEEDANCE_ASPECT_RATIOS = ("1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21")
VIDEO_MODELS = {
    WAN_MODEL: VideoModel("Wan 2.2 I2V Fast", tuple(WAN_DURATION_FRAMES)),
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


def video_duration_label(model: str, seconds: int | float) -> str:
    return f"约 {seconds:g} 秒" if model == WAN_MODEL else f"{seconds:g} 秒"
