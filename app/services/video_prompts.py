"""视频 prompt 的内置指令及图片来源解析，不按当前轮数猜测历史来源。"""

from app.models import GeneratedImageRecord, Page2ConversationTurn


VIDEO_PROMPT_SYSTEM_PROMPT = """#任务：我会给你当前我有图片prompt和本轮的故事内容，我需要你做的是：帮我根据这幅图片作为参考图生成生视频所需的prompt
#指导：根据“图片prompt”来确定当前图片中的角色形象和角色站位.根据“本轮故事内容”写出生视频所需prompt让图中角色进行“本轮故事内容”的操作.
#细节要求：
1.不要出现任何角色的名字，仅用外貌特征代指他们
2.输出的prompt不超过200字
3.输出的prompt不要带有任何解释性词语或者标题和特殊格式，仅为prompt
4.如果有人物，则要具体的用肌肉动作（而不是抽象的表情描述词）描述角色的表情，动作，反应，和角色间的互动。
5.不要描述角色的情绪仅仅以第三人称视角描述可察觉画面"""


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
