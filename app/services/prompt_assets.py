from __future__ import annotations

import json
import re

from app.api.chat_clients import DeepSeekAPIClient, GrokAPIClient
from app.config import AppStorageKeys, FINAL_SETTING_SYNC_BACKUP_PATH, settings
from app.models import (
    FixedTemplateRecord,
    PromptTextRecord,
    deepseek_api_model_for_chat_model,
    grok_api_model_for_chat_model,
    normalize_chat_model,
    now_iso,
)


DEFAULT_FINAL_FIRST_INPUT = """*根据背景介绍，世界书和用户角色给用户一个初始场景，告诉用户现在他是谁（原样输出“system prompt中的用户角色信息”），他在干嘛，周围有什么，现在剧情是从什么时候什么地方开始的，并且在最近几轮让用户自然的遇到给他主线任务的角色并且给他布置详细的主线任务。主线任务必须包含1.任务完成标准 2.任务失败标准 3.任务奖励（任务奖励需要是物质奖励加上可以开启的精彩剧情，比如有意思的后续任务）。"""

WORLD_BOOK_SYSTEM_PROMPT = """我会给你一个对于这个世界背景的大致介绍和关于这个世界背景知识的基本问答。根据大致介绍和基本问答，你将会细化这个世界背景至最多800字，包括但不限于：这个世界的背景、常识、特殊之处。输出使用markdown格式"""

CHARACTER_CARD_SYSTEM_PROMPT = """我会给你一个大致的角色介绍，你将帮我把这个扩写为大概800字的详细角色卡。其中提到用户角色的名字时，使用“{用户角色名}”作为占位符。语言精练简洁。其中不用“他/她”指代角色本身，使用角色自己的名字为主语如当角色名为“xx”时，用“xx是一个某某的人”而不是用"她/他"指代。
角色卡包含5个部分：

1. 角色身份：包含角色的身材、样貌、穿搭、年龄等背景信息，以及角色的背景故事。
2. 角色间的关系：包含角色与用户角色如何认识、关系如何、相处风格如何、一起经历过什么，以及角色对用户角色的看法。
3. 角色性格：包含角色的价值观、最显著的几个性格标签及其在角色身上的体现、角色说话风格，以及角色性格的特殊之处。
4. 角色的参数：参数信息包括力量、敏捷、反应、技巧和综合战力，综合战力为前四项之和。
   - 这个世界未经过训练的普通人，力量、敏捷、反应、技巧的参数平均为2，10 HP，10体力值。
   - 普通雇佣兵的力量、敏捷、反应、技巧参数平均为5，50 HP，50体力值。
   - 顶级雇佣兵的力量、敏捷、反应、技巧参数平均为8，100 HP，100体力值。
   - 力量、敏捷、反应、技巧的参数最低为0、最高为10；HP和体力值最低为0、最高为130。
   - 给出角色的力量、敏捷、反应、技巧、综合战力、HP和体力值。
5. 角色的能力：如果角色为战斗人员，则结合角色的身份、战力等背景信息编写角色的技能、特殊能力和限制。案例：“- 白眼：开启后可以看到360度的目标，同时可以短距离透视（100m左右，越远越困难，并且开启后持续消耗体力值）”。

使用Markdown格式输出，直接输出可放入角色卡的正文，不要附加解释。"""

WORLD_BOOK_QUESTIONS_SYSTEM_PROMPT = """你是一个DND中世界书的细化助手，我将会给你一个对于这个世界背景的大致介绍，你需要根据这个世界背景的大致介绍询问我15个这个世界的相关问题，这15个问题和可能的ABC选项。需要足够关键，足够影响这个世界的运行逻辑。你的输出格式仅为问题，以“1....2....”来编号，问题后面输出三个可能的选项，不要输出任何问题和可能选项以外的内容。输出格式案例：1.{第一个问题}A.{可能的选项A}B.{可能的选项B}C.{可能的选项C}"""

CHARACTER_CARD_QUESTIONS_SYSTEM_PROMPT = """你是一个DND中的角色卡细化助手，我将会给你一个对于这个角色的大致介绍，你需要根据这个角色的大致介绍询问我15个这个世界的相关问题，这15个问题和可能的ABC三个选项。需要足够关键，足够影响这个角色的日常决策，和其他角色的互动，说话的口吻，其他角色对待她的方式和态度。你的输出格式仅为问题，以“1....2....”来编号，问题后面输出三个可能的选项，不要输出任何问题和可能选项以外的内容。输出格式案例：1.{第一个问题}A.{可能的选项A}B.{可能的选项B}C.{可能的选项C}"""

DEFAULT_TEMPLATE_BACKGROUND = """1. 背景介绍
   -你是一名负责主持沉浸式文字角色扮演游戏的 Dungeon Master（DM）。你不是配合用户续写故事的助手，而是一个持续模拟世界运行的主持者。
   -你控制环境、NPC、事件、冲突、隐藏信息、战斗反馈和剧情发展；用户只控制自己的角色。
   -最高优先级规则：绝对禁止替用户角色做出任何主动行为、选择、回答、心理决定或战术决定。
   -你可以描述用户角色看到、听到、感受到的事情，以及外部行为对其身体产生的客观影响，但不能替用户决定任何行为，即使是小行为。
   -当场景发展到需要用户角色进行主动回应或决定时，应在完整呈现当前瞬间已经发生的NPC行为、台词和环境变化后停止推进，把行动权交给用户。
   -用户的血量和体力决定了用户的虚弱程度，影响了用户行为的难度（也就是掷骰子中行动成功的区间大小，越虚弱成功概率越小）
   -受到伤害会根据伤害程度掉HP（消耗率大概为没有防范时被棍子用力击打掉8HP）,进行消耗体力的剧烈运动或行为会根据行动的激烈程度消耗体力值（消耗率大概为普通人跑1000米消耗8体力值）
   -HP主要代表身体伤势，低HP会影响力量、敏捷、反应以及需要身体完成的行动；体力值主要代表体力与能力负荷，低体力值会影响连续行动、爆发动作和消耗型能力。DM在制定骰点区间时必须考虑两者，但影响程度根据具体行为决定，不机械按照固定比例扣除成功率。
   -用户和其他角色会有血量和体力值。血量和体力值上限保持不变，除非特殊情况（如重伤）
   ##参数信息：
   -参数信息包括：力量、敏捷、反应、技巧和综合战力（综合战力为前四项之和）
   -综合战力只用于粗略比较角色的基础战斗素质，不直接决定胜负，特殊能力不计入参数
   -这个世界未经过训练的普通人力量、敏捷、反应、技巧的参数平均为2，10HP,10体力值
   -普通雇佣兵的力量、敏捷、反应、技巧参数平均为5,50HP,50体力值
   -顶级雇佣兵的力量、敏捷、反应、技巧参数平均为8,100HP,100体力值
   -力量、敏捷、反应、技巧的参数最低为0最高为10。Hp/体力值的参数最低为0，最高为130。"""

DEFAULT_TEMPLATE_DM_CORE = """##基础任务

-DM必须主动维持一个拥有因果关系和主观能动性的世界，而不是等待用户触发剧情。

-结尾告诉用户当前时间和地点

-NPC拥有独立的欲望、恐惧、利益、关系和目标。他们可以主动接触、调查、欺骗、帮助、威胁、逃跑、合作、毁灭证据、改变计划或在用户不知道的地方采取行动。幕后事件可以发生，但只有当用户通过合理方式获得信息时才能揭露。

-所有NPC必须参考“当前故事背景”的设定保持人物一致性。态度和性格可以改变，但必须由真实经历造成。NPC只能根据自己实际看见、听见、知道或合理推断出的信息行动，严格区分“DM知道什么”和“NPC知道什么”。

-用户做出行动后，先判断现场谁真正看到、听到或注意到了这件事，再按照人物关系和性格决定谁会反应。不要让所有NPC都对每一件事发表评论，也不要让无人目击的信息瞬间传播。

-剧情应主动出现合理的新事件、人物互动、调查机会、真假线索、冲突、谣言、监视、邀请、威胁或任务变化，但禁止依靠不合理巧合强推剧情。

-其他NPC第一次正式登场时，应自然描写其可观察外貌，例如种族、年龄感、体型、发型、眼睛、衣着、明显特征和第一印象

-当某个NPC主动攻击用户角色，或用户角色主动攻击该NPC时，应公开其当前能够被观察或合理判断出的战斗信息，包括姓名或称呼、身份、已显露的能力、最大生命值和当前生命值。并且给出能力参数信息

-当用户角色新进入一个场景时从用户角色视角详细描述这个场景。被遮蔽或不显眼的物品不用描述。

-你需要根据“行为判定原则”进行行为判定

##行为判定原则

-并非所有行动都需要骰点。掷骰子的点数判定比例、各个区间的结果要参考剧情中各个角色的能力、状态、和操作的难度决定，而不是通用的。

-不存在合理失败可能的行为直接完成，例如打开未上锁的门、捡起物品、正常走路、推开毫无防备且明显弱于自己的普通人。

-只要行为存在有意义的失败概率，就必须进行 0—24点判定，区间之间不得重叠或留下空缺，包括攻击有能力反抗的人、闪避攻击、潜入、追逐、偷窃、欺骗高度警觉的角色、制服反抗目标或实施对于用户操作角色的能力而言高难度行为。

###判定必须分为两轮

-第一轮：DM制定判定区间。

根据以下情景因素动态决定0—24每个区间会发生什么：

• 角色能力

• 角色当前状态

• 当前环境

• 当前特殊情况

没有固定成功线。

点数分割规则DM根据当前情景因素自定（可以0-19一个档，也可以0-1一个档）。可能结果也由DM根据情景因素自定（可以分5个档位，也可以分2个档位）档位数量只根据实际可能产生的不同结果决定

-用户描述想要的结果，不等于结果已经发生。

“我砍中他”“我直接把他打晕”“我一招解决他”等描述，都只能视为角色的行动意图。如果该结果存在合理失败概率，必须先判定，禁止因为用户使用肯定句就直接判定成功。DM公布完整区间后停止推进，并明确告诉用户：

“可以开始掷骰子。”

-当声明的目标不可能完成时，不为该目标掷骰。但如果该行动本身仍可能造成其他合理结果，DM可以直接说明无法达到声明目标，并根据实际行为判断是否存在其他需要判定的结果。

###第二轮：用户亲自提供0—24的骰点。

-DM绝不能代替用户掷骰，也不能私自生成结果。收到用户骰点后，必须严格按照第一轮已经公布的区间结算，不得因为剧情需要临时改变标准。

###敌人攻击用户时

-NPC可以主动发动攻击，只要用户角色在攻击生效前存在合理察觉机会，就必须把主动权交给用户。若攻击在世界逻辑上完全无法被用户角色察觉，则不提前泄露攻击本身；DM可以直接结算其客观影响。是否能够察觉，可依据感知条件、反应能力和攻击隐蔽程度决定，必要时进行判定。

-例如敌人挥刀攻击时，不能自动写成“你侧身躲开”，也不能直接判定“刀命中了你”而是询问用户打算怎么应对再根据应对写出不同骰子点数的判定，让用户掷骰子"""

DEFAULT_TEMPLATE_RESPONSE_FORMAT = """6. 回复格式要求
   -使用沉浸式小说式文字进行叙事，重点描写人物动作、神态、语气、环境变化以及用户当前能够察觉到的细节。
   -对话应自然，不要把NPC写成任务说明书。
   -正常情况下，每次回复推进一个明确的场景阶段。除非用户提到，不然不要一次跳过大量时间
   -一旦出现需要用户角色主动回答、选择、移动、战斗应对或作出决定的节点，完成当前瞬间必要的信息呈现后立刻停止。
   -不要输出：
   “你选择A、B还是C？”
   进行骰点判定时，回复顺序必须为：
   ##【行动判定】
   -简要说明为什么该行为需要判定。
   -随后列出已经根据当前情况制定好的0—24结果区间。
   -最后写：
   可以开始掷骰子。
   -此时必须停止，等待用户提供点数。
   -用户提供点数后，在下一轮首先显示：
   ##【骰点：X / 24】
   -然后严格根据之前公布的区间描写实际结果。
   -战斗进行期间，每轮回复结尾显示所有当前参战者：
   ##【当前生命值】
   -只要任意一人未满hp/体力值，每轮结尾显示：
   -用户角色：当前HP / 最大HP, 当前体力值/最大体力值
   -队友：当前HP / 最大HP, 当前体力值/最大体力值
   -NPC：当前HP / 最大HP, 当前体力值/最大体力值
   -在非战斗和剧烈活动状态时，用户角色和主要队友如果没有满血，则每经过游戏中正常剧情回复自动恢复 3 HP和6 体力值，不得超过最大值。
   ##【状态】
   HP/体力值两者全部恢复满后停止显示状态栏。
   -始终优先保证四件事：
   用户拥有自己角色的绝对行动权；NPC拥有真正的自主性；随机行为严格遵守已经公布的骰点规则；世界真相不会为了配合用户而改变。
   ##【固定结尾输出】
   -每轮结尾输出：当前游戏内时间和当前所处地点。并在前面加上特殊符号">"
   -当前时间：根据剧情发展推算时间的改变，不因为聊天轮数本身自动增加，并且环境与作息匹配时间（如19点天会黑，23点学校中人会少）。
   -当前地点：用户角色当前实际所在的最具体已知区域。
   -输出格式如：“>当前时间：7月20日 17:25， 当前地点：教学楼东面地下停车场的角落”"""

FINAL_SECTION_HEADINGS = (
    "1. 背景介绍",
    "2. 世界书",
    "3. 用户角色",
    "4. 其他角色",
    "5. DM 的核心任务",
    "6. 回复格式要求",
)


def _decode_text_records(raw) -> list[PromptTextRecord]:
    try:
        items = raw if isinstance(raw, list) else json.loads(raw or "[]")
    except Exception:
        items = []
    return [PromptTextRecord.from_dict(item) for item in items if isinstance(item, dict)]


def _decode_template_records(raw) -> list[FixedTemplateRecord]:
    try:
        items = raw if isinstance(raw, list) else json.loads(raw or "[]")
    except Exception:
        items = []
    return [FixedTemplateRecord.from_dict(item) for item in items if isinstance(item, dict)]


def _persist_text_records(key: str, records: list[PromptTextRecord]) -> None:
    settings.set(key, json.dumps([record.to_dict() for record in records], ensure_ascii=False))


def load_world_books() -> list[PromptTextRecord]:
    return _decode_text_records(settings.get(AppStorageKeys.WORLD_BOOK_RECORDS, ""))


def load_character_cards() -> list[PromptTextRecord]:
    return _decode_text_records(settings.get(AppStorageKeys.CHARACTER_CARD_RECORDS, ""))


def _save_text_record(key: str, record_id: str, title: str, content: str) -> PromptTextRecord:
    records = _decode_text_records(settings.get(key, ""))
    existing = next((record for record in records if record.id == str(record_id or "")), None)
    if existing is None:
        existing = PromptTextRecord(title=str(title or "").strip(), content=str(content or "").strip())
        records.append(existing)
    else:
        existing.title = str(title or "").strip()
        existing.content = str(content or "").strip()
        existing.updated_at = now_iso()
    _persist_text_records(key, records)
    return existing


def save_world_book(record_id: str, title: str, content: str) -> PromptTextRecord:
    return _save_text_record(AppStorageKeys.WORLD_BOOK_RECORDS, record_id, title, content)


def save_character_card(record_id: str, title: str, content: str) -> PromptTextRecord:
    return _save_text_record(AppStorageKeys.CHARACTER_CARD_RECORDS, record_id, title, content)


def _delete_text_record(key: str, record_id: str) -> None:
    records = [record for record in _decode_text_records(settings.get(key, "")) if record.id != record_id]
    _persist_text_records(key, records)


def delete_world_book(record_id: str) -> None:
    _delete_text_record(AppStorageKeys.WORLD_BOOK_RECORDS, record_id)


def delete_character_card(record_id: str) -> None:
    _delete_text_record(AppStorageKeys.CHARACTER_CARD_RECORDS, record_id)


def _persist_templates(records: list[FixedTemplateRecord]) -> None:
    settings.set(
        AppStorageKeys.FIXED_TEMPLATE_RECORDS,
        json.dumps([record.to_dict() for record in records], ensure_ascii=False),
    )


def load_fixed_templates() -> list[FixedTemplateRecord]:
    records = _decode_template_records(settings.get(AppStorageKeys.FIXED_TEMPLATE_RECORDS, ""))
    if not settings.bool(AppStorageKeys.FIXED_TEMPLATE_DEFAULT_SEEDED, False):
        record = FixedTemplateRecord(
            title="默认模板",
            background_intro=DEFAULT_TEMPLATE_BACKGROUND,
            dm_core_tasks=DEFAULT_TEMPLATE_DM_CORE,
            response_format=DEFAULT_TEMPLATE_RESPONSE_FORMAT,
        )
        records.append(record)
        _persist_templates(records)
        settings.set(AppStorageKeys.SELECTED_FIXED_TEMPLATE_RECORD_ID, record.id)
        settings.set(AppStorageKeys.FIXED_TEMPLATE_DEFAULT_SEEDED, True)
    return records


def selected_fixed_template(records: list[FixedTemplateRecord] | None = None) -> FixedTemplateRecord | None:
    records = records if records is not None else load_fixed_templates()
    selected_id = str(settings.get(AppStorageKeys.SELECTED_FIXED_TEMPLATE_RECORD_ID, "") or "")
    return next((record for record in records if record.id == selected_id), None)


def save_fixed_template(
    record_id: str,
    title: str,
    background_intro: str,
    dm_core_tasks: str,
    response_format: str,
    *,
    make_active: bool,
) -> FixedTemplateRecord:
    records = load_fixed_templates()
    existing = next((record for record in records if record.id == str(record_id or "")), None)
    if existing is None:
        existing = FixedTemplateRecord(
            title=str(title or "").strip(),
            background_intro=str(background_intro or "").strip(),
            dm_core_tasks=str(dm_core_tasks or "").strip(),
            response_format=str(response_format or "").strip(),
        )
        records.append(existing)
    else:
        existing.title = str(title or "").strip()
        existing.background_intro = str(background_intro or "").strip()
        existing.dm_core_tasks = str(dm_core_tasks or "").strip()
        existing.response_format = str(response_format or "").strip()
        existing.updated_at = now_iso()
    _persist_templates(records)
    if make_active:
        settings.set(AppStorageKeys.SELECTED_FIXED_TEMPLATE_RECORD_ID, existing.id)
    return existing


def create_prefilled_fixed_template() -> FixedTemplateRecord:
    return save_fixed_template(
        "",
        "",
        DEFAULT_TEMPLATE_BACKGROUND,
        DEFAULT_TEMPLATE_DM_CORE,
        DEFAULT_TEMPLATE_RESPONSE_FORMAT,
        make_active=False,
    )


def delete_fixed_template(record_id: str) -> None:
    records = [record for record in load_fixed_templates() if record.id != record_id]
    _persist_templates(records)
    selected_id = str(settings.get(AppStorageKeys.SELECTED_FIXED_TEMPLATE_RECORD_ID, "") or "")
    if selected_id == record_id:
        fallback = max(records, key=lambda record: record.updated_at, default=None)
        settings.set(
            AppStorageKeys.SELECTED_FIXED_TEMPLATE_RECORD_ID,
            fallback.id if fallback is not None else "",
        )


def _character_brief(identity: str, relationship: str, personality: str, other: str) -> str:
    values = [
        str(identity or "").strip(),
        str(relationship or "").strip(),
        str(personality or "").strip(),
        str(other or "").strip(),
    ]
    if not any(values):
        return "随机生成一个角色"
    return "\n".join(
        [
            f"角色身份：{values[0] or '未设定'}",
            f"角色之间的关系：{values[1] or '未设定'}",
            f"角色性格：{values[2] or '未设定'}",
            f"其他：{values[3] or '未设定'}",
        ]
    )


def _with_refinement_answers(base_prompt: str, questions: str, answers: str) -> str:
    questions = str(questions or "").strip()
    answers = str(answers or "").strip()
    if not questions and not answers:
        return base_prompt
    return "\n\n".join(
        [
            f"## 原始简介\n{base_prompt}",
            f"## 细化问题\n{questions or '无'}",
            f"## 用户回答与补充\n{answers or '用户未填写额外回答'}",
            "请将原始简介和用户回答统合成最终设定；如有冲突，以用户回答为准。",
        ]
    )


def generate_world_book_questions(
    model: str,
    description: str,
    *,
    reasoning_effort: str = "high",
) -> str:
    description = str(description or "").strip()
    if not description:
        raise ValueError("请先填写世界背景介绍；如果想从零填写，请点击取消创建空白记录。")
    return _send_generation(
        model,
        WORLD_BOOK_QUESTIONS_SYSTEM_PROMPT,
        description,
        reasoning_effort=reasoning_effort,
        max_tokens=50000,
    )


def generate_character_card_questions(
    model: str,
    identity: str,
    relationship: str,
    personality: str,
    other: str,
    *,
    reasoning_effort: str = "high",
) -> str:
    return _send_generation(
        model,
        CHARACTER_CARD_QUESTIONS_SYSTEM_PROMPT,
        _character_brief(identity, relationship, personality, other),
        reasoning_effort=reasoning_effort,
        max_tokens=50000,
    )


def generate_world_book(
    model: str,
    description: str,
    *,
    questions: str = "",
    answers: str = "",
    reasoning_effort: str = "high",
) -> str:
    description = str(description or "").strip()
    if not description:
        raise ValueError("请先填写世界背景介绍；如果想从零填写，请点击取消创建空白记录。")
    user_prompt = _with_refinement_answers(description, questions, answers)
    return _send_generation(
        model,
        WORLD_BOOK_SYSTEM_PROMPT,
        user_prompt,
        reasoning_effort=reasoning_effort,
    )


def generate_character_card(
    model: str,
    identity: str,
    relationship: str,
    personality: str,
    other: str,
    *,
    questions: str = "",
    answers: str = "",
    reasoning_effort: str = "high",
) -> str:
    user_prompt = _with_refinement_answers(
        _character_brief(identity, relationship, personality, other),
        questions,
        answers,
    )
    return _send_generation(
        model,
        CHARACTER_CARD_SYSTEM_PROMPT,
        user_prompt,
        reasoning_effort=reasoning_effort,
    )


def _send_generation(
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    reasoning_effort: str = "high",
    max_tokens: int = 50000,
) -> str:
    selected_model = normalize_chat_model(model)
    effort = str(reasoning_effort or "high").strip().lower()
    thinking_enabled = effort not in {"off", "none", "disabled", "false"}
    effective_effort = effort if thinking_enabled else None
    deepseek_model = deepseek_api_model_for_chat_model(selected_model)
    if deepseek_model is not None:
        output = DeepSeekAPIClient.send_message(
            system_prompt=system_prompt,
            context_messages=[],
            user_message=user_prompt,
            temperature=0.8,
            model=deepseek_model,
            thinking_enabled=thinking_enabled,
            reasoning_effort=effective_effort,
            max_tokens=max_tokens,
        )
    else:
        grok_model = grok_api_model_for_chat_model(selected_model) or "grok-4.3"
        if grok_model == "grok-4.6" and not thinking_enabled:
            thinking_enabled = True
            effective_effort = "low"
        output = GrokAPIClient.send_message(
            system_prompt=system_prompt,
            context_messages=[],
            user_message=user_prompt,
            model=grok_model,
            temperature=0.8,
            thinking_enabled=thinking_enabled,
            reasoning_effort=effective_effort,
        )
    output = str(output or "").strip()
    if not output:
        raise ValueError("模型没有返回内容。")
    return output


def _without_leading_heading(text: str, headings: set[str]) -> str:
    lines = str(text or "").strip().splitlines()
    if lines and lines[0].strip().replace(" ", "") in headings:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _without_character_personality_section(text: str) -> str:
    """Remove explicit Markdown character-personality sections from a card."""
    lines = str(text or "").strip().splitlines()
    kept_lines: list[str] = []
    skipped_heading_level: int | None = None
    personality_heading = re.compile(
        r"^(#{1,6})\s*(?:\d+\s*[.、．)]\s*)?角色性格\s*[:：]?\s*$"
    )
    markdown_heading = re.compile(r"^(#{1,6})(?:\s|[^#])")

    for line in lines:
        personality_match = personality_heading.match(line.strip())
        if personality_match:
            skipped_heading_level = len(personality_match.group(1))
            continue

        if skipped_heading_level is not None:
            heading_match = markdown_heading.match(line.strip())
            if heading_match and len(heading_match.group(1)) <= skipped_heading_level:
                skipped_heading_level = None
            else:
                continue

        kept_lines.append(line)

    return "\n".join(kept_lines).strip()


def other_character_section_names(other_characters: list[PromptTextRecord]) -> list[str]:
    names = [
        str(character.title or "").strip() or f"未命名角色 {index + 1}"
        for index, character in enumerate(other_characters)
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            "其他角色名称不能重复，否则无法安全同步："
            + "、".join(duplicates)
            + "。请先修改对应角色卡名称。"
        )
    return names


def build_final_prompt(
    template: FixedTemplateRecord,
    world_book: PromptTextRecord,
    user_character: PromptTextRecord,
    other_characters: list[PromptTextRecord],
) -> str:
    user_name = str(user_character.title or "").strip()
    if not user_name:
        raise ValueError("用户角色必须有角色名称，请先到角色卡页面填写并保存。")

    background = _without_leading_heading(template.background_intro, {"1.背景介绍", "1、背景介绍"})
    dm_core = _without_leading_heading(template.dm_core_tasks, {"5.DM的核心任务", "5、DM的核心任务"})
    response_format = _without_leading_heading(template.response_format, {"6.回复格式要求", "6、回复格式要求"})

    if other_characters:
        other_sections = []
        other_names = other_character_section_names(other_characters)
        for character, name in zip(other_characters, other_names):
            content = str(character.content or "").replace("{用户角色名}", user_name).strip()
            other_sections.append(f"### {name}\n{content}".strip())
        other_content = "\n\n".join(other_sections)
    else:
        other_content = "无"

    sections = [
        ("1. 背景介绍", background),
        ("2. 世界书", str(world_book.content or "").strip()),
        (
            "3. 用户角色",
            _without_character_personality_section(user_character.content),
        ),
        ("4. 其他角色", other_content),
        ("5. DM 的核心任务", dm_core),
        ("6. 回复格式要求", response_format),
    ]
    return "\n\n".join(f"{heading}\n{content}".strip() for heading, content in sections)


def _parse_final_sections(prompt: str) -> dict[str, tuple[str, int]]:
    lines = str(prompt or "").strip().splitlines()
    positions: dict[str, list[int]] = {heading: [] for heading in FINAL_SECTION_HEADINGS}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped in positions:
            positions[stripped].append(index)

    for heading in FINAL_SECTION_HEADINGS:
        found = positions[heading]
        if not found:
            raise ValueError(f'同步失败：缺少分区标题“{heading}”。本次未写入任何内容。')
        if len(found) > 1:
            line_numbers = "、".join(str(item + 1) for item in found)
            raise ValueError(
                f'同步失败：分区标题“{heading}”重复（第 {line_numbers} 行）。'
                "本次未写入任何内容。"
            )

    ordered_positions = [positions[heading][0] for heading in FINAL_SECTION_HEADINGS]
    if ordered_positions != sorted(ordered_positions):
        raise ValueError(
            "同步失败：六个分区的顺序不正确，必须按 1—6 排列。"
            "本次未写入任何内容。"
        )

    result: dict[str, tuple[str, int]] = {}
    for index, heading in enumerate(FINAL_SECTION_HEADINGS):
        heading_position = ordered_positions[index]
        end_position = (
            ordered_positions[index + 1] if index + 1 < len(ordered_positions) else len(lines)
        )
        content = "\n".join(lines[heading_position + 1 : end_position]).strip()
        content_start_line = heading_position + 2
        if not content:
            raise ValueError(
                f'同步失败：“{heading}”下方没有内容（第 {content_start_line} 行起）。'
                "为防止误清空原素材，本次未写入任何内容。"
            )
        result[heading] = (content, content_start_line)
    return result


def _parse_other_character_sections(
    content: str,
    *,
    content_start_line: int,
    expected_names: list[str],
) -> list[str]:
    if not expected_names:
        if str(content or "").strip() != "无":
            raise ValueError(
                '同步失败：该最终设定没有关联其他角色，“4. 其他角色”必须为“无”。'
                "本次未写入任何内容。"
            )
        return []

    lines = str(content or "").splitlines()
    expected_headings = [f"### {name}" for name in expected_names]
    heading_positions: list[int] = []
    for heading in expected_headings:
        found = [index for index, line in enumerate(lines) if line.strip() == heading]
        if not found:
            raise ValueError(
                f'同步失败：“4. 其他角色”缺少角色标题“{heading}”。'
                "请保留创建时的角色名称和顺序；本次未写入任何内容。"
            )
        if len(found) > 1:
            line_numbers = "、".join(str(content_start_line + item) for item in found)
            raise ValueError(
                f'同步失败：角色标题“{heading}”重复（第 {line_numbers} 行）。'
                "本次未写入任何内容。"
            )
        heading_positions.append(found[0])

    if heading_positions != sorted(heading_positions):
        raise ValueError(
            "同步失败：“4. 其他角色”中的角色顺序已改变。"
            "请恢复创建时的顺序；本次未写入任何内容。"
        )
    if any(line.strip() for line in lines[: heading_positions[0]]):
        raise ValueError(
            f'同步失败：“4. 其他角色”必须直接从“{expected_headings[0]}”开始。'
            "角色标题前不能添加额外内容；本次未写入任何内容。"
        )

    parsed_contents: list[str] = []
    for index, heading_position in enumerate(heading_positions):
        end_position = (
            heading_positions[index + 1]
            if index + 1 < len(heading_positions)
            else len(lines)
        )
        character_content = "\n".join(lines[heading_position + 1 : end_position]).strip()
        if not character_content:
            line_number = content_start_line + heading_position + 1
            raise ValueError(
                f'同步失败：角色“{expected_names[index]}”没有内容'
                f'（第 {line_number} 行起）。为防止误清空角色卡，本次未写入任何内容。'
            )
        parsed_contents.append(character_content)
    return parsed_contents


def bind_legacy_final_setting_by_titles(
    state,
    *,
    final_title: str,
    template_title: str,
    world_book_title: str,
    user_character_title: str,
    other_character_titles: list[str],
) -> bool:
    """Safely add source links to one unlinked legacy final setting by exact titles."""
    matches = [
        item
        for item in state.records + state.hidden_records
        if str(item.title or "").strip() == str(final_title or "").strip()
    ]
    if not matches:
        return False
    if len(matches) > 1:
        raise ValueError(
            f'无法为旧最终设定“{final_title}”补录关联：存在同名记录。'
        )

    record = matches[0]
    existing_link_values = [
        str(record.final_template_id or "").strip(),
        str(record.final_world_book_id or "").strip(),
        str(record.final_user_character_id or "").strip(),
        *[str(item or "").strip() for item in record.final_other_character_ids],
        str(record.final_user_character_name or "").strip(),
        *[str(item or "").strip() for item in record.final_other_character_names],
    ]
    expected_other_titles = [str(item or "").strip() for item in other_character_titles]
    is_complete = (
        bool(record.final_template_id)
        and bool(record.final_world_book_id)
        and bool(record.final_user_character_id)
        and bool(record.final_user_character_name)
        and len(record.final_other_character_ids) == len(expected_other_titles)
        and len(record.final_other_character_names) == len(expected_other_titles)
    )
    if is_complete:
        return False
    if any(existing_link_values):
        raise ValueError(
            f'无法为旧最终设定“{final_title}”自动补录关联：'
            "该记录已有部分来源信息，为防止覆盖已有关系，本次未修改。"
        )

    def unique_record(records, title: str, label: str):
        found = [item for item in records if str(item.title or "").strip() == title]
        if not found:
            raise ValueError(
                f'无法为“{final_title}”补录关联：找不到{label}“{title}”。'
            )
        if len(found) > 1:
            raise ValueError(
                f'无法为“{final_title}”补录关联：{label}“{title}”存在重名。'
            )
        return found[0]

    templates = load_fixed_templates()
    world_books = load_world_books()
    character_cards = load_character_cards()
    template = unique_record(templates, template_title, "固定模板")
    world_book = unique_record(world_books, world_book_title, "世界书")
    user_character = unique_record(character_cards, user_character_title, "角色卡")
    other_characters = [
        unique_record(character_cards, title, "角色卡")
        for title in expected_other_titles
    ]

    sections = _parse_final_sections(record.prompt)
    _parse_other_character_sections(
        sections["4. 其他角色"][0],
        content_start_line=sections["4. 其他角色"][1],
        expected_names=expected_other_titles,
    )

    from app.services import system_prompts

    system_prompts.bind_final_setting_sources(
        state,
        record_id=record.id,
        template_id=template.id,
        world_book_id=world_book.id,
        user_character_id=user_character.id,
        other_character_ids=[item.id for item in other_characters],
        user_character_name=user_character_title,
        other_character_names=expected_other_titles,
    )
    return True


def sync_final_setting(
    state,
    record,
    *,
    title: str,
    prompt: str,
    first_input: str,
) -> list[str]:
    """Validate and atomically sync one final setting back to its linked source assets."""
    if record is None:
        raise ValueError("同步失败：当前没有选中最终设定。")

    required_links = {
        "固定模板": str(record.final_template_id or "").strip(),
        "世界书": str(record.final_world_book_id or "").strip(),
        "用户角色": str(record.final_user_character_id or "").strip(),
    }
    missing_links = [label for label, record_id in required_links.items() if not record_id]
    if missing_links:
        raise ValueError(
            "同步失败：该最终设定是旧记录或不是通过组合流程创建，"
            "缺少来源关联（"
            + "、".join(missing_links)
            + "）。请重新新建最终设定；本次未写入任何内容。"
        )

    other_ids = [str(item or "").strip() for item in record.final_other_character_ids]
    other_names = [str(item or "").strip() for item in record.final_other_character_names]
    if len(other_ids) != len(other_names) or any(not item for item in other_ids + other_names):
        raise ValueError(
            "同步失败：该最终设定的其他角色来源关联不完整。"
            "请重新新建最终设定；本次未写入任何内容。"
        )
    if len(set(other_ids)) != len(other_ids) or len(set(other_names)) != len(other_names):
        raise ValueError(
            "同步失败：该最终设定的其他角色关联存在重复。"
            "请重新新建最终设定；本次未写入任何内容。"
        )

    templates = load_fixed_templates()
    world_books = load_world_books()
    character_cards = load_character_cards()
    template = next(
        (item for item in templates if item.id == required_links["固定模板"]), None
    )
    world_book = next(
        (item for item in world_books if item.id == required_links["世界书"]), None
    )
    user_character = next(
        (item for item in character_cards if item.id == required_links["用户角色"]), None
    )
    other_characters = [
        next((item for item in character_cards if item.id == record_id), None)
        for record_id in other_ids
    ]
    deleted_sources = []
    if template is None:
        deleted_sources.append("固定模板")
    if world_book is None:
        deleted_sources.append("世界书")
    if user_character is None:
        deleted_sources.append("用户角色卡")
    deleted_sources.extend(
        f'角色卡“{name}”'
        for name, character in zip(other_names, other_characters)
        if character is None
    )
    if deleted_sources:
        raise ValueError(
            "同步失败：以下关联来源已被删除："
            + "、".join(deleted_sources)
            + "。请重新新建最终设定；本次未写入任何内容。"
        )

    sections = _parse_final_sections(prompt)
    other_contents = _parse_other_character_sections(
        sections["4. 其他角色"][0],
        content_start_line=sections["4. 其他角色"][1],
        expected_names=other_names,
    )
    user_name = str(record.final_user_character_name or "").strip()
    if not user_name:
        raise ValueError(
            "同步失败：该最终设定缺少创建时的用户角色名称，"
            "无法安全恢复“{用户角色名}”。请重新新建最终设定。"
        )

    updated_labels: list[str] = []
    changed_at = now_iso()

    def update_text_record(item: PromptTextRecord, content: str, label: str) -> None:
        normalized = str(content or "").strip()
        if item.content != normalized:
            item.content = normalized
            item.updated_at = changed_at
            updated_labels.append(label)

    update_text_record(world_book, sections["2. 世界书"][0], f'世界书“{world_book.title or "未命名"}”')
    for name, character, content in zip(other_names, other_characters, other_contents):
        restored_content = content.replace(user_name, "{用户角色名}")
        update_text_record(character, restored_content, f'角色卡“{name}”')

    background = sections["1. 背景介绍"][0]
    dm_core = sections["5. DM 的核心任务"][0]
    response_format = sections["6. 回复格式要求"][0]
    if (
        template.background_intro != background
        or template.dm_core_tasks != dm_core
        or template.response_format != response_format
    ):
        template.background_intro = background
        template.dm_core_tasks = dm_core
        template.response_format = response_format
        template.updated_at = changed_at
        updated_labels.append(f'固定模板“{template.title or "未命名"}”')

    record.title = str(title or "").strip()
    record.prompt = str(prompt or "").strip()
    record.first_input = str(first_input or "").strip()
    record.updated_at = changed_at
    state.selected_record_id = record.id

    updates = {
        AppStorageKeys.WORLD_BOOK_RECORDS: json.dumps(
            [item.to_dict() for item in world_books], ensure_ascii=False
        ),
        AppStorageKeys.CHARACTER_CARD_RECORDS: json.dumps(
            [item.to_dict() for item in character_cards], ensure_ascii=False
        ),
        AppStorageKeys.FIXED_TEMPLATE_RECORDS: json.dumps(
            [item.to_dict() for item in templates], ensure_ascii=False
        ),
        AppStorageKeys.SYSTEM_PROMPT_RECORDS: json.dumps(
            [item.to_dict() for item in state.records], ensure_ascii=False
        ),
        AppStorageKeys.HIDDEN_SYSTEM_PROMPT_RECORDS: json.dumps(
            [item.to_dict() for item in state.hidden_records], ensure_ascii=False
        ),
        AppStorageKeys.SELECTED_SYSTEM_PROMPT_RECORD_ID: record.id,
        AppStorageKeys.SYSTEM_PROMPT: record.prompt,
    }
    settings.set_many_atomic(updates, backup_path=FINAL_SETTING_SYNC_BACKUP_PATH)
    return updated_labels
