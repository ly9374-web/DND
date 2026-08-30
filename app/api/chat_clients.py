import json
from dataclasses import dataclass
from typing import Optional, List

import requests

from app.config import (
    XAIConfig,
    DeepSeekConfig,
    debug_log,
    mask_authorization_header,
)


# =========================
# 对应 Swift: GrokResponseResult
# =========================

@dataclass
class GrokResponseResult:
    id: Optional[str]
    text: str


# =========================
# 对应 Swift: GrokInputMessage
# =========================

@dataclass
class GrokInputMessage:
    role: str
    content: str

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
        }


# =========================
# 对应 Swift: GrokAPIClient
# =========================

class GrokAPIClient:
    """
    对应 Swift 里的 final class GrokAPIClient。

    功能：
    1. 普通聊天 send_message()
    2. 生成图片prompt generate_image_prompt()
    3. 第一人称图片prompt generate_first_person_image_prompt()
    4. 人物特写图片prompt generate_character_closeup_image_prompt()
    5. 解析 Grok Responses API 返回
    """

    RESPONSES_URL = "https://api.x.ai/v1/responses"

    @classmethod
    def send_message(
        cls,
        system_prompt,
        context_messages,
        user_message,
        model="grok-4.3",
        temperature=0.8,
        thinking_enabled=None,
        reasoning_effort=None,
    ):
        """
        对应 Swift:
        sendMessage(systemPrompt:contextMessages:userMessage:model:temperature:)
        """

        input_messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        for message in context_messages:
            if isinstance(message, GrokInputMessage):
                input_messages.append(message.to_dict())
            elif isinstance(message, dict):
                input_messages.append(message)

        input_messages.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        body = {
            "model": model,
            "input": input_messages,
            "temperature": temperature,
        }
        if thinking_enabled is not None:
            effective_reasoning_effort = (
                str(reasoning_effort or "high") if thinking_enabled else "none"
            )
            body["reasoning"] = {"effort": effective_reasoning_effort}

        data = cls._post_responses(body, label="Grok Request")
        return cls.parse_response(data).text

    @classmethod
    def generate_image_prompt(cls, user_message):
        """
        对应 Swift:
        generateImagePrompt(from userMessage:)
        """

        system_prompt = """
# 任务
根据 `userMessage` 提取最适合视觉化的一个瞬间，生成可直接用于 AI 绘图的图片描述。不要复述剧情，只描述摄像机能够直接看到的内容。
# 输出格式
场景：
描述人物所在环境，按前景、中景、背景说明可见内容，包括建筑、家具、道路、植物、车辆、天气、时间、背景人物等。不要使用剧情中的专有地点名称，不描述情绪或氛围。
主要人物：
先说明主要人物数量，再逐一描述。每个人物尽量包含：性别、年龄段、外貌特征、发型发色、身材、穿着特征、身体姿势、手部动作、头部朝向、视线和面部表情。不要使用剧情中的人物姓名。
人物互动：
明确人物之间的位置和距离，以及谁在看谁、谁碰到谁、谁拿着什么。只描述能直接看见的动作。
构图：
说明人物位于画面左、中、右，以及前景、中景、背景的空间关系。确保主体清晰，避免重要人物互相遮挡。除非剧情要求，否则尽量让主要人物脸部和动作完整可见。
摄像头角度：
按照“景别 + 机位高度 + 拍摄方向 + 俯仰角度”描述。例如：中远景，摄像机位于人物胸部高度，从人物左前方45度拍摄，轻微俯视，可以同时看到两名人物和后方环境。
画风：动漫画风
# 规则
* 只选择一个明确瞬间，不混合多个时间点。
* 忠于给你的userMessage，不省略任何东西。
* 不能输出人名或者地名。
严格按照以上格式输出，不附加解释。
# 案例：
userMessage：
放学后，小雨回到教室拿遗忘的作业本，发现班主任还站在讲台旁。她刚准备离开，老师突然叫住她，把一张满是红色批注的试卷递了过来。小雨低着头接过试卷，教室门口还有三个正在等她的同学，窗外已经完全黑了。
输出：
场景：
一间普通学校教室，室内亮着白色顶灯。前景有几排整齐排列的木质课桌和椅子，中景是讲台和黑板，桌面放着几本书和教学用品。背景右侧是打开的教室门，门口站着三名穿校服的学生。左侧窗户外为黑夜，玻璃上映出部分室内灯光。
主要人物：
画面中有两名主要人物。一名十几岁的亚洲女学生站在讲台前，皮肤白皙，黑色中长发，身材纤细，穿白色校服衬衫、深色百褶裙和运动鞋。她微微低头，右手伸向前方接试卷，视线落在试卷上，眉毛轻微下垂，嘴唇闭合。另一名三十岁左右的亚洲女教师站在讲台旁，黑色长发扎在脑后，穿浅色衬衫和深色长裤，右手拿着一张布满红色批注的白色试卷并向学生递出，脸朝向学生。
人物互动：
女教师站在女学生正前方约半米处，右手将试卷递向女学生。女学生伸出右手接住试卷下方，两人的视线都集中在试卷附近。门口三名学生站在较远的背景位置，看向教室内部。
构图：
女学生位于画面中央偏左，女教师位于中央偏右，两人构成画面主体。讲台位于两人后方，课桌占据前景下方区域，门口三名学生位于右侧背景，左侧窗户可以清楚看到黑色夜景。两名主要人物身体和脸部均无遮挡。
摄像头角度：
中远景，摄像机位于教室后方、接近人物胸部高度，从两名主要人物左前方约45度拍摄，轻微俯视，可以同时看到讲台前的两名人物、前景课桌、左侧窗户和右侧门口的三名学生。
画风：动漫
""".strip()

        body = {
            "model": "grok-4.3",
            "input": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
        }

        data = cls._post_responses(body, label="Grok Prompt Request")
        return cls.parse_response(data).text

    @classmethod
    def generate_first_person_image_prompt(cls, user_message, subject):
        """
        对应 Swift:
        generateFirstPersonImagePrompt(from userMessage:subject:)
        """

        subject = str(subject or "").strip()
        if not subject:
            raise ValueError("Subject is empty")

        system_prompt = (
            f"分析我给你的故事，以“{subject}”为第一人称，并用“我”代指自己的名字，"
            f"使用客观具体的描述，最终输出不超过100字“{subject}”所看到的画面，"
            f"用第一人称“我”来描述所看到的画面。禁止使用抽象的词汇，"
            f"禁止生成“{subject}”看不到的内容，比如“{subject}”的形象，"
            f"“{subject}”背后的环境，禁止使用成语抽象描述场景。"
            f"并且在prompt中描述“{subject}”的一个肢体在画面何处出现输出少于150字。"
            "输出结尾加上“摄像头视角：我的第一人称视角”和“画风：写实”\n"
            "案例：我看到前方有一位黄头发蓝眼睛的可爱亚洲成年女性蹲在教室的地面上，"
            "她的表情悲伤，穿着校服。背景是教室，周围有其他学生，后面的光线昏暗发黄。"
            "画面左侧伸出我拿着铅笔的手指着那位女生 摄像头视角：我的第一人称视角 画风：写实"
        )

        body = {
            "model": "grok-4.3",
            "input": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
        }

        data = cls._post_responses(body, label="Grok First Person Prompt Request")
        return cls.parse_response(data).text

    @classmethod
    def generate_character_closeup_image_prompt(cls, user_message, subject):
        """
        对应 Swift:
        generateCharacterCloseupImagePrompt(from userMessage:subject:)
        """

        subject = str(subject or "").strip()
        if not subject:
            raise ValueError("Character closeup subject is empty")

        system_prompt = f"""
根据 userMessage 生成一个图片描述，以以下模板提取画面主要角并生成{subject}的人物特写，不要出现{subject}的人物的名字，给我详细描述{subject}的外貌,穿着，姿势，状态，表情，特征,还有场景背景。描述中不要出现别的角色。最终输出字数不超过80字
范例：一个漂亮皮肤白皙的蓝头发女生。女生带着红色帽子，穿着蓝色格子校服，脸上有泪痕，头上有杂草。女生左手举起右手放松的放在大腿上，两腿分开。女生眉毛皱起，眼睛眯成一条缝露出一个勉强的微笑。背景是学校的老师办公室
""".strip()

        body = {
            "model": "grok-4.3",
            "input": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
        }

        data = cls._post_responses(body, label="Grok Character Closeup Prompt Request")
        return cls.parse_response(data).text

    @classmethod
    def _post_responses(cls, body, label="Grok Request"):
        effective_grok_chat_api_key = XAIConfig.chat_api_key()
        if not effective_grok_chat_api_key:
            raise RuntimeError(
                "缺少 Grok 聊天 API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 GROK_CHAT_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_grok_chat_api_key,
        }

        debug_log("====== " + label + " ======")
        debug_log("URL:", cls.RESPONSES_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", json.dumps(body, ensure_ascii=False))

        response = requests.post(
            cls.RESPONSES_URL,
            headers=headers,
            json=body,
            timeout=3600,
        )

        debug_log("====== " + label + " HTTP Status ======")
        debug_log(response.status_code)

        debug_log("====== " + label + " Raw Response ======")
        debug_log(response.text)

        try:
            data = response.json()
        except Exception:
            return {
                "error": {
                    "message": "Grok 返回内容不是 JSON：" + response.text
                }
            }

        return data

    @staticmethod
    def parse_response(data):
        """
        对应 Swift:
        private static func parseResponse(from data: Data, rawResponse: String? = nil)
        """

        if not isinstance(data, dict):
            return GrokResponseResult(
                id=None,
                text="未解析到 Grok 回复内容。",
            )

        response_id = data.get("id")

        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or "unknown"
            return GrokResponseResult(
                id=response_id,
                text="Grok API 错误：" + str(message),
            )

        if isinstance(error, str) and error.strip():
            return GrokResponseResult(
                id=response_id,
                text="Grok API 错误：" + error,
            )

        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return GrokResponseResult(
                id=response_id,
                text=output_text,
            )

        pieces = []

        output = data.get("output")
        if isinstance(output, list):
            extracted_from_message_items = False

            # 优先只取 type == message 的 output_text
            for item in output:
                if not isinstance(item, dict):
                    continue

                if item.get("type") != "message":
                    continue

                extracted_from_message_items = True

                content_array = item.get("content")
                if not isinstance(content_array, list):
                    continue

                for block in content_array:
                    if not isinstance(block, dict):
                        continue

                    if block.get("type") == "output_text":
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            pieces.append(text)

            # 兼容其他返回结构
            if not extracted_from_message_items:
                for item in output:
                    if not isinstance(item, dict):
                        continue

                    content_array = item.get("content")
                    if isinstance(content_array, list):
                        for block in content_array:
                            if not isinstance(block, dict):
                                continue

                            text = block.get("text") or block.get("content")
                            if isinstance(text, str) and text.strip():
                                pieces.append(text)

                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        pieces.append(text)

                    message = item.get("message")
                    if isinstance(message, dict):
                        message_content = message.get("content")

                        if isinstance(message_content, str) and message_content.strip():
                            pieces.append(message_content)

                        if isinstance(message_content, list):
                            for block in message_content:
                                if not isinstance(block, dict):
                                    continue

                                text = block.get("text") or block.get("content")
                                if isinstance(text, str) and text.strip():
                                    pieces.append(text)

        # 去重，避免上下文污染
        seen = set()
        unique_pieces = []

        for piece in pieces:
            text = str(piece).strip()
            if not text:
                continue

            if text not in seen:
                seen.add(text)
                unique_pieces.append(text)

        combined = "\n".join(unique_pieces).strip()

        if not combined:
            return GrokResponseResult(
                id=response_id,
                text="未解析到 Grok 回复内容。",
            )

        return GrokResponseResult(
            id=response_id,
            text=combined,
        )


# =========================
# 对应 Swift: DeepSeekAPIClient
# =========================

class DeepSeekAPIClient:
    """
    对应 Swift 里的 final class DeepSeekAPIClient。
    """

    CHAT_URL = "https://api.deepseek.com/v1/chat/completions"
    DEFAULT_MODEL = "deepseek-v4-flash"
    SUPPORTED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}

    @classmethod
    def send_message(
        cls,
        system_prompt,
        context_messages,
        user_message,
        temperature=0.8,
        model=DEFAULT_MODEL,
        thinking_enabled=True,
        reasoning_effort="high",
        max_tokens=50000,
    ):
        """
        对应 Swift:
        sendMessage(systemPrompt:contextMessages:userMessage:temperature:)
        """

        model = str(model or cls.DEFAULT_MODEL).strip()
        if model not in cls.SUPPORTED_MODELS:
            raise ValueError("未知 DeepSeek 模型：" + model)

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        for message in context_messages:
            if isinstance(message, GrokInputMessage):
                messages.append(message.to_dict())
            elif isinstance(message, dict):
                messages.append(message)

        messages.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        effective_deepseek_api_key = DeepSeekConfig.api_key()
        if not effective_deepseek_api_key:
            raise RuntimeError(
                "缺少 DeepSeek API Key。请在 APIkey 页面填写，或在 Streamlit Secrets 配置 DEEPSEEK_API_KEY。"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + effective_deepseek_api_key,
        }

        body = {
            "messages": messages,
            "model": model,
            "thinking": {
                "type": "enabled" if thinking_enabled else "disabled",
            },
            "response_format": {
                "type": "text",
            },
            "stream": False,
            "temperature": temperature,
            "top_p": 1,
            "tool_choice": "none",
            "logprobs": False,
        }
        if thinking_enabled and reasoning_effort:
            body["reasoning_effort"] = str(reasoning_effort)
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)

        debug_log("====== DeepSeek Chat Request ======")
        debug_log("URL:", cls.CHAT_URL)
        debug_log("Authorization:", mask_authorization_header(headers["Authorization"]))
        debug_log("Body:", json.dumps(body, ensure_ascii=False))

        response = requests.post(
            cls.CHAT_URL,
            headers=headers,
            json=body,
            timeout=3600,
        )

        debug_log("====== DeepSeek Chat HTTP Status ======")
        debug_log(response.status_code)

        debug_log("====== DeepSeek Chat Raw Response ======")
        debug_log(response.text)

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"DeepSeek 返回了无法解析的响应（HTTP {response.status_code}）。"
            ) from exc

        if data.get("error"):
            debug_log("====== DeepSeek Chat Error ======")
            debug_log(data.get("error"))
            raise RuntimeError("DeepSeek API error: " + str(data.get("error")))

        if int(response.status_code) >= 400:
            raise RuntimeError(f"DeepSeek API HTTP 错误：{response.status_code}")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("DeepSeek 响应缺少 choices，没有可用的模型输出。")

        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("DeepSeek 响应中的 choice 格式异常。")

        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("DeepSeek 响应缺少 message。")

        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or item.get("content") or "")
                for item in content
                if isinstance(item, dict)
            )

        finish_reason = str(first.get("finish_reason") or "unknown")
        content = str(content or "").strip()
        if finish_reason == "length" and thinking_enabled:
            debug_log(
                "DeepSeek thinking output reached max_tokens; "
                "retrying the same request with thinking disabled."
            )
            return cls.send_message(
                system_prompt=system_prompt,
                context_messages=context_messages,
                user_message=user_message,
                temperature=temperature,
                model=model,
                thinking_enabled=False,
                reasoning_effort=None,
                max_tokens=max_tokens,
            )
        if content:
            return content

        if finish_reason == "length":
            raise RuntimeError(
                "DeepSeek 未返回最终内容（finish_reason=length），"
                "关闭思考自动重试后仍用完了本次输出 token。"
            )
        raise RuntimeError(
            "DeepSeek 未返回可用的最终内容"
            f"（finish_reason={finish_reason}）。"
        )
