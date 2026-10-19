"""视频 prompt 的内置指令及图片来源解析，不按当前轮数猜测历史来源。"""

from app.default_prompts.internal_catalog import prompt
from app.models import GeneratedImageRecord, Page2ConversationTurn


VIDEO_PROMPT_SYSTEM_PROMPT = prompt("video", "system")


class MissingVideoStorySource(ValueError):
    """旧图片没有轮次关联，可由用户为这张图片补录故事正文。"""


def latest_image_prompt_source(turns: list[Page2ConversationTurn]) -> dict[str, str]:
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if str(turn.assistant_message or "").strip() and not turn.is_loading:
            return {"source_turn_id": turn.id}
    return {}


def resolve_video_story(source: GeneratedImageRecord, turns: list[Page2ConversationTurn]) -> str:
    if not source.source_turn_id:
        manual = str(source.video_story_text or "").strip()
        if manual:
            return manual
        raise MissingVideoStorySource("这张图片缺少来源轮次，请粘贴对应那一轮的助手故事正文。")

    by_id = {turn.id: turn for turn in turns}
    turn = by_id.get(source.source_turn_id)
    if turn is None:
        raise ValueError("图片对应的原轮次已撤回，无法生成视频 prompt。")
    text = str(turn.assistant_message or "").strip()
    if turn.is_loading or not text:
        raise ValueError("图片对应的故事轮次没有可用的助手回复，无法生成视频 prompt。")
    return text
