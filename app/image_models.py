"""Shared image-model names, aspect ratios and reference-image limits."""

IMAGE_ASPECT_RATIO = "1:1"
IMAGE_ASPECT_RATIOS = ("3:4", "4:3", "1:1")
EXTENDED_IMAGE_ASPECT_RATIOS = IMAGE_ASPECT_RATIOS + ("16:9", "9:16", "match_input_image")
IMAGE2_PROVIDER = "image2"
IMAGE2_ASPECT_RATIOS = ("1:1", "3:2", "2:3")
IMAGE2_QUALITIES = ("low", "medium", "high")
IMAGE2_DEFAULT_QUALITY = "low"
DEFAULT_IMAGE_PROVIDER = "seedream-4.5"
ANILLUSTRIOUS_PROVIDER = "anillustrious-v4"
WAI_ILLUSTRIOUS_PROVIDER = "wai-nsfw-illustrious-v12"
IMAGE_PROVIDERS = (
    "grok", "grokpro2k", "grokPro", "flux2-dev", "seedream-4.5", "nanoPro", "nano",
    ANILLUSTRIOUS_PROVIDER, WAI_ILLUSTRIOUS_PROVIDER, IMAGE2_PROVIDER,
)


def normalize_image_provider(provider: str) -> str:
    value = str(provider or "").strip()
    return {"flux": "flux2-dev", "grokQuality": "grokpro2k"}.get(value, value)


def image_aspect_ratios(provider: str) -> tuple[str, ...]:
    if normalize_image_provider(provider) == IMAGE2_PROVIDER:
        return IMAGE2_ASPECT_RATIOS
    if normalize_image_provider(provider) in {"flux2-dev", "seedream-4.5"}:
        return EXTENDED_IMAGE_ASPECT_RATIOS
    return IMAGE_ASPECT_RATIOS


def image_aspect_ratio_label(ratio: str) -> str:
    return "跟随参考图" if ratio == "match_input_image" else ratio


def validate_image_options(
    provider: str, aspect_ratio: str, image_urls: list[str],
    quality: str = IMAGE2_DEFAULT_QUALITY,
) -> None:
    provider = normalize_image_provider(provider)
    if provider not in IMAGE_PROVIDERS:
        raise ValueError("未知图片模型：" + provider)
    if provider == IMAGE2_PROVIDER and quality not in IMAGE2_QUALITIES:
        raise ValueError("image2 quality 请选择 low、medium 或 high。")
    if aspect_ratio not in image_aspect_ratios(provider):
        choices = "、".join(image_aspect_ratio_label(ratio) for ratio in image_aspect_ratios(provider))
        raise ValueError(f"{provider} 图片尺寸请选择 {choices}。")
    if aspect_ratio == "match_input_image" and not image_urls:
        raise ValueError("选择“跟随参考图”时，请至少填写一张参考图片 URL。")
    if provider in {ANILLUSTRIOUS_PROVIDER, WAI_ILLUSTRIOUS_PROVIDER} and image_urls:
        raise ValueError(f"{provider} 仅支持文生图，不能使用参考图片 URL。")
    limit = {"flux2-dev": 4, "seedream-4.5": 14}.get(provider)
    if limit is not None and len(image_urls) > limit:
        raise ValueError(f"{provider} 最多支持 {limit} 张参考图片。")
