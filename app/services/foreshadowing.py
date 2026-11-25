"""Authoring, location matching and one-shot events, independent of first inputs."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import re
from uuid import uuid4

import regex

from app.services import foreshadow_prompts as prompts


def new_plan(title="事件方案"):
    return {"id": uuid4().hex, "title": title, "story": "", "scene_events": [], "time_events": []}


def selected_plan(config):
    return next((deepcopy(p) for p in config.get("plans", [])
                 if p.get("id") == config.get("selected_plan_id")), {})


def snapshot(config):
    plan = selected_plan(config)
    return without_event_dates({**plan, "enabled": bool(config.get("enabled", True)) and bool(plan)})


def _is_regex_term(term):
    """Legacy plans stored regex; new plans store plain location aliases."""
    # '+' is not treated as regex-only: it doubles as the plain "must also contain" separator.
    return bool(re.search(r"[\\^$.|?*()\[\]{}]", term))


def _normalize_place(value):
    """Treat '的' and whitespace as optional when comparing plain aliases."""
    return re.sub(r"[\s的]", "", str(value or "")).lower()


def _required_keywords(term):
    """A plain alias can require several keywords: split on space, '&', '＆', '+' or '、'."""
    parts = (_normalize_place(part) for part in re.split(r"[\s&＆+＋、]+", str(term or "")))
    return [part for part in parts if part]


def validate_patterns(patterns):
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("场景事件至少需要一个地点表述。")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 500:
            raise ValueError("地点表述不能为空，且不能超过500个字符。")
        if _is_regex_term(pattern):
            try:
                regex.compile(pattern)
            except regex.error as exc:
                raise ValueError(f"无效的正则表达式：{pattern}（{exc}）") from exc


def matches(patterns, location):
    if not location:
        return False
    text = location[:1000]
    normalized = _normalize_place(text)
    for pattern in patterns:
        try:
            if _is_regex_term(pattern):
                if regex.search(pattern, text, flags=regex.IGNORECASE, timeout=0.02):
                    return True
            else:
                keywords = _required_keywords(pattern)
                if keywords and all(keyword in normalized for keyword in keywords):
                    return True
        except (regex.error, TimeoutError, TypeError):
            continue
    return False


def clock_minutes(value):
    match = re.fullmatch(r"\s*(\d{1,2})\s*[:：]\s*(\d{2})\s*", str(value))
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError("时间请使用00:00至23:59的格式。")
    return int(match[1]) * 60 + int(match[2])


def _calendar(value, year=2000):
    match = re.search(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})(?:日|号)?", value)
    if not match:
        return None
    try:
        return date(int(match[1] or year), int(match[2]), int(match[3]))
    except ValueError:
        return None


def game_state(turns):
    """Read only explicit time/location footers from successful replies."""
    state = {"day": 1, "minute": None, "calendar": None, "location": ""}
    origin = None
    for turn in turns:
        if not successful(turn):
            continue
        text = str(turn.assistant_message)
        footers = list(re.finditer(r"(?m)^\s*>?\s*(?:\*\*)?当前时间\s*[:：]", text))
        if not footers:
            continue
        footer = text[footers[-1].end():].replace("**", "")
        location = re.search(r"(?:当前(?:所处)?地点|当前位置)\s*[:：]\s*([^\n]+)", footer)
        if location:
            state["location"] = location[1].strip().rstrip("。")
        time_text = re.split(r"当前(?:所处)?地点|当前位置|\n", footer)[0]
        clock = re.search(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)", time_text)
        if not clock or int(clock[1]) > 23 or int(clock[2]) > 59:
            # Never infer a trigger time from prose such as “下午”.
            state["minute"] = None
            continue
        minute = int(clock[1]) * 60 + int(clock[2])
        day = re.search(r"第\s*(\d+)\s*天", time_text)
        previous_date = state["calendar"]
        calendar = _calendar(time_text, previous_date.year if previous_date else 2000)
        if calendar:
            if previous_date and calendar.month == 1 and previous_date.month == 12 and not re.search(r"\d{4}[年/-]", time_text):
                calendar = date(previous_date.year + 1, calendar.month, calendar.day)
            if day and int(day[1]) > 0:
                state["day"] = int(day[1])
                origin = calendar.toordinal() - state["day"] + 1
            else:
                if origin is None:
                    origin = calendar.toordinal() - state["day"] + 1
                state["day"] = calendar.toordinal() - origin + 1
            state["calendar"] = calendar
        elif day and int(day[1]) > 0:
            state["day"] = int(day[1])
            if origin is not None:
                state["calendar"] = date.fromordinal(origin + state["day"] - 1)
        elif state["minute"] is not None and minute < state["minute"]:
            state["day"] += 1
            if previous_date:
                state["calendar"] = date.fromordinal(previous_date.toordinal() + 1)
        state["minute"] = minute
    return state


def due_events(plan, turns):
    if not plan.get("enabled", False):
        return []
    state = game_state(turns)
    consumed = handled_event_ids(turns)
    found = []
    for event in plan.get("scene_events", []):
        if event.get("enabled", True) and event.get("id") not in consumed and matches(event.get("patterns", []), state["location"]):
            copy = deepcopy(event)
            copy["event_content"] = event_with_suffix(event["event_content"], timed=False)
            found.append(copy)
    timed = []
    if state["minute"] is not None:
        for event in plan.get("time_events", []):
            if not event.get("enabled", True) or event.get("id") in consumed:
                continue
            try:
                minute = clock_minutes(event.get("time", ""))
                due = (int(event["day"]) - 1) * 1440 + minute
                now = (state["day"] - 1) * 1440 + state["minute"]
                if due <= now:
                    copy = {key: value for key, value in event.items() if key != "date"}
                    content = event_with_suffix(event["event_content"], timed=True)
                    copy["event_content"] = f"{event_time(event)}：{content}"
                    # Sort by actual game day and time, including skipped times.
                    timed.append((due - now, copy))
            except (ValueError, TypeError, KeyError):
                continue
    return found + [e for _, e in sorted(timed, key=lambda pair: pair[0])]


def successful(turn):
    return bool(str(turn.assistant_message or "").strip()) and not turn.is_loading and not str(turn.assistant_message).startswith("请求失败，请稍后重试。")


def handled_event_ids(turns):
    return {event.get("id") for turn in turns if successful(turn)
            for event in getattr(turn, "special_events", [])}


def time_batch_finished(plan, turns, trigger):
    if not plan.get("enabled") or not successful(trigger):
        return False
    active_ids = {event.get("id") for event in plan.get("time_events", []) if event.get("enabled", True)}
    return bool(active_ids & handled_event_ids([trigger])) and active_ids <= handled_event_ids(turns)


EVENT_PREFIX = "【待处理的特殊事件，if成立前保密if...then部分】"
EVENT_RESULT_MARKER = "【特殊事件处理结果】"
_EVENT_RESULT_LINE = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]+|>[ \t]*)?(?:\*\*)?"
    + re.escape(EVENT_RESULT_MARKER) + r"(?:\*\*)?[ \t]*[:：]?[ \t]*"
)
EVENT_RESULT_RULE = (
    "【特殊事件处理规则】以下事件不属于玩家发言。先判断与过去剧情是否有明确冲突。"
    "缺少自然的获知渠道只表示用户角色不知情。时间事件按给定的游戏时间发生，"
    "正文仅自然呈现玩家角色可以获知的内容，由程序在下一次 Story Brain 更新时记入已发生的时间事件。"
    "照常写完正文、时间地点和其他格式要求，然后在最后单独一行写"
    f"{EVENT_RESULT_MARKER}，下一行输出一个JSON对象，以各事件的编号为键。"
    '每个值是 {"status":"occurred","summary":"实际发生的简短客观事实","player_knows":false}，'
    '或在过去剧情已使事件不可能发生时写 {"status":"invalid"}。'
    '地点事件没有自然获知渠道时写 {"status":"skipped"}。'
    "player_knows 使用布尔值，只有用户角色截至本轮已通过自然渠道获知时才填true。"
    "已发生的地点事件如果还有未满足的if...then条件，可以用字符串 followup 保留仍适用的条件及其对应内容。"
    "如果正文此前已经写出了同一事件的发生，则确认已有事实，不重复演出。"
    "如果不能确认某条事件如何处理，可以省略该编号。不要把处理结果写进故事正文。"
)

_OLD_SCENE_SUFFIX = "若此相关事件在storybrain中发现被剧情影响，则忽略这一特殊事件。不要编写用户角色的行为"
_OLD_TIME_SUFFIX = "若此相关事件在storybrain中发现本事件被剧情影响不再应该发生，则忽略这一特殊事件，否则尽量让主角通过合理渠道知道这件事情，"


def event_body(content):
    old_suffixes = (prompts.TIME_EVENT_SUFFIX, prompts.SCENE_EVENT_SUFFIX, _OLD_SCENE_SUFFIX, _OLD_SCENE_SUFFIX + "。",
                    _OLD_TIME_SUFFIX + "将事件发生在游戏第几天、几点几分记录在story brain中。",
                    _OLD_TIME_SUFFIX + "将时间和发生具体时间和日期在story brain中。")
    content = str(content or "").strip()
    for old_suffix in old_suffixes:
        if content.endswith(old_suffix):
            return content.removesuffix(old_suffix).rstrip()
    return content


def event_with_suffix(content, *, timed):
    content = event_body(content)
    suffix = prompts.TIME_EVENT_SUFFIX if timed else prompts.SCENE_EVENT_SUFFIX
    return f"{content}\n{suffix}" if content else suffix


def event_time(event):
    minute = clock_minutes(event["time"])
    return f"第{event['day']}天 {minute // 60:02d}:{minute % 60:02d}"


def resolve_events(reply, events):
    """Strip the model-only trailer and keep only confirmed outcomes."""
    if not events:
        return reply, []
    marker = _EVENT_RESULT_LINE.search(reply)
    if marker is None:
        return reply, []
    story = reply[:marker.start()].rstrip()
    try:
        results = _json(reply[marker.end():])
    except ValueError:
        return story, []
    resolved = []
    for event in events:
        result = results.get(event["id"])
        if not isinstance(result, dict):
            continue
        timed = "day" in event
        allowed = ("invalid", "occurred") if timed else ("invalid", "occurred", "skipped")
        status = result.get("status")
        if status not in allowed:
            continue
        outcome = {"id": event["id"], "status": status}
        if status == "occurred" and timed:
            summary = result.get("summary")
            player_knows = result.get("player_knows")
            if not isinstance(summary, str) or not summary.strip() or not isinstance(player_knows, bool):
                continue
            outcome.update(day=event["day"], time=event["time"], summary=" ".join(summary.split()),
                           player_knows=player_knows)
        elif status == "occurred" and result.get("player_knows") is True:
            followup = result.get("followup")
            if isinstance(followup, str) and followup.strip():
                outcome["followup"] = followup.strip()
        resolved.append(outcome)
    return story, resolved


def confirmed_time_event_facts(turns):
    return [
        f"{event_time(event)} {event['summary']}（用户角色当时{'已获知' if event['player_knows'] else '不知情'}）"
        for turn in turns if successful(turn)
        for event in getattr(turn, "special_events", [])
        if event.get("status") == "occurred" and "day" in event
    ]


def pending_event_message(user_message, events):
    if not events:
        return user_message
    pending = [f"{EVENT_PREFIX}编号 {event['id']}\n{event['event_content']}" for event in events]
    return "\n\n".join([user_message, EVENT_RESULT_RULE, *pending])


def turn_user_message(turn, *, include_scene_followups=False):
    events = getattr(turn, "special_events", [])
    parts = [str(turn.user_message or "")]
    if events and successful(turn):
        facts = confirmed_time_event_facts([turn])
        if facts:
            parts.append("【已确认的世界事件】\n" + "\n".join(facts))
        if include_scene_followups:
            parts.extend("【先前地点事件的后续条件，成立前保密，已实现的结果勿重复】" + event["followup"]
                         for event in events if "followup" in event)
        # Old turns predate the result protocol and retain their historical context.
        parts.extend("【历史事件提示】" + event_body(event.get("event_content"))
                     for event in events if "status" not in event)
    return "\n\n".join(parts)


def _json(text):
    value = str(text or "").strip()
    if value.startswith("```"):
        value = "\n".join(value.splitlines()[1:-1])
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("模型没有返回有效JSON，请重试；原方案未被修改。") from exc
    if not isinstance(result, dict):
        raise ValueError("模型返回的内容必须为JSON对象。")
    return result


def extract_story_material(final_setting):
    """Keep Markdown subtrees intact; also accept the app's numbered plain headings."""
    from app.services.prompt_assets import FINAL_SECTION_HEADINGS
    names = {re.sub(r"^\d+\.\s*", "", heading).replace(" ", ""): heading
             for heading in FINAL_SECTION_HEADINGS}
    lines = str(final_setting or "").splitlines()
    headings = []
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + r"{" + str(fence[1]) + r",}\s*", line):
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            continue
        match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$", line)
        if match:
            level = len(match[1])
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
        elif line.strip() in FINAL_SECTION_HEADINGS and len(line) - len(line.lstrip()) <= 3:
            level, title = 1, line.strip()
        else:
            continue
        title = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)\s*[.．、)）:：]\s*", "", title)
        headings.append((index, level, names.get(title.replace(" ", ""))))

    material = ("2. 世界书", "3. 用户角色", "4. 其他角色")
    # A character's own subheading named “世界书” is still part of its parent section.
    levels = [level for _, level, name in headings if name in material]
    section_level = min(levels) if levels else None
    sections = {}
    for position, (start, level, name) in enumerate(headings):
        if level != section_level or name not in material:
            continue
        if name in sections:
            raise ValueError(f"无法提取事件素材：分区标题“{name}”重复，请明确保留一份。")
        end = next((line for line, next_level, _ in headings[position + 1:] if next_level <= level), len(lines))
        content = "\n".join(lines[start + 1:end]).strip()
        sections[name] = (lines[start].strip(), content)
    required = material[:2]
    missing = [name for name in required if name not in sections or not sections[name][1]]
    if missing:
        raise ValueError("无法提取事件素材，请补齐最终设定分区：" + "、".join(missing))
    parts = []
    for name in material:
        if name in sections:
            heading, content = sections[name]
            if content:
                parts.append(heading + "\n" + content)
    return "\n\n".join(parts)


def generation_context(final_setting, turns=None, story_brain="", history_turn_count=6):
    from app.services import page2_service
    turns = list(turns or [])
    state = game_state(turns)
    minute = state["minute"]
    initial = f"游戏第{state['day']}天"
    if minute is not None:
        initial += f" {minute // 60:02d}:{minute % 60:02d}"
    elif turns:
        initial = "未解析到明确时分，请以最新一轮聊天及Story Brain中的最新游戏时间为初始时间，不使用现实时间，不默认重置为游戏第1天。"
    else:
        initial += " 00:00（尚未开局，按开局第1天零点起算）"
    if not turns:
        state["minute"] = 0
    recent = page2_service.build_context_messages(
        [turn for turn in turns if successful(turn)], max(1, int(history_turn_count))
    )
    history = "\n\n".join(("用户：" if item["role"] == "user" else "助手：") + item["content"] for item in recent)
    return {
        "final_setting": extract_story_material(final_setting),
        "history": history or "暂无", "story_brain": str(story_brain or "").strip() or "暂无",
        "initial_time": initial, "clock": state,
    }


def generate(stage, ctx, final_setting, *, turns=None, story_brain="", history_turn_count=6, captured_context=None):
    from app.services import page2_service
    if stage != "events":
        raise ValueError("事件生成已取消问卷，请直接生成事件。")
    context = captured_context or generation_context(final_setting, turns, story_brain, history_turn_count)
    direction = context.get("direction", "").strip()
    direction_section = ""
    if direction:
        direction_section = (
            "【用户期望的事件生成方向】\n"
            f"{direction}\n"
            "请在符合世界设定、已发生的剧情和编排范围的前提下，优先参考这个方向。\n\n"
        )
    window_hours = prompts.EVENT_WINDOW_HOURS
    lead_window_hours = prompts.LEAD_EVENT_WINDOW_HOURS
    lead_event_count = prompts.LEAD_EVENT_COUNT
    result = page2_service._send_with_current_chat_model(
        ctx=ctx, system_prompt=prompts.EVENTS_SYSTEM, context_messages=[],
        user_message=prompts.EVENTS_USER.format(
            **context, direction_section=direction_section, window_hours=window_hours,
            lead_window_hours=lead_window_hours, lead_event_count=lead_event_count,
        ),
        temperature=0.8, thinking_enabled=True, log_label="生成背景与事件",
    )
    data = _json(result)
    if not isinstance(data.get("story"), str) or not data["story"].strip():
        raise ValueError("模型没有返回故事背景，请重新生成；原方案未被修改。")
    clock = context["clock"]
    scene_locations = {}
    lead_count = 0
    for key in ("scene_events", "time_events"):
        rows = data.get(key)
        if not isinstance(rows, list) or len(rows) != 5:
            raise ValueError("需要5条场景事件和5条时间事件，请重新生成；原方案未被修改。")
        for event in rows:
            if not isinstance(event, dict) or not isinstance(event.get("event_content"), str) or not event["event_content"].strip():
                raise ValueError("每条事件必须包含事件内容。")
            if key == "scene_events":
                event.pop("title", None)
                validate_patterns(event.get("patterns"))
                if not 4 <= len(event["patterns"]) <= 5 or not isinstance(event.get("location"), str) or not event["location"].strip():
                    raise ValueError("场景事件需包含具体地点和4至5个地点表述。")
                location = event["location"].strip()
                scene_locations.setdefault(location, []).extend(event["patterns"])
            else:
                if not isinstance(event.get("title"), str) or not event["title"].strip():
                    raise ValueError("时间事件必须包含名称。")
                event_minute = clock_minutes(event.get("time", ""))
                if type(event.get("day")) is not int or event["day"] < 1:
                    raise ValueError("时间事件的游戏天数必须为正整数。")
                destination = event.pop("leads_to_location", None)
                if destination is not None and not isinstance(destination, str):
                    raise ValueError("引导地点必须是场景事件的地点名称。")
                destination = (destination or "").strip()
                if destination:
                    if not matches(scene_locations.get(destination, []), event["event_content"]):
                        raise ValueError("引导事件必须对应本次生成的场景事件，并在正文中写清目的地。")
                    lead_count += 1
                if clock["minute"] is not None:
                    offset = (event["day"] - clock["day"]) * 1440 + event_minute - clock["minute"]
                    if not 0 < offset <= window_hours * 60:
                        raise ValueError(f"时间事件必须安排在本次生成初始时间点之后的{window_hours}小时内。原方案未被修改。")
                    if (offset <= lead_window_hours * 60) != bool(destination):
                        raise ValueError(f"引导事件须在初始时间点后的{lead_window_hours}小时内发生，其余时间事件须安排在此后。")
                event.pop("date", None)
            event["event_content"] = event_with_suffix(event["event_content"], timed=key == "time_events")
            event["id"] = uuid4().hex
            event["enabled"] = True
    if lead_count != lead_event_count:
        raise ValueError(f"需要恰好{lead_event_count}条引导事件。原方案未被修改。")
    return {key: data[key] for key in ("story", "scene_events", "time_events")}


def add_generated_plan(config, result):
    config = deepcopy(config)
    plan = new_plan(f"事件方案 {len(config.get('plans', [])) + 1}")
    plan.update(deepcopy(result))
    config.setdefault("plans", []).append(plan)
    config.update(selected_plan_id=plan["id"], enabled=True)
    return config


def without_event_dates(plan):
    """Legacy date fields no longer override the game's day/time schedule."""
    plan = deepcopy(plan or {})
    for event in plan.get("time_events", []):
        event.pop("date", None)
        event["event_content"] = str(event.get("event_content") or "").replace(
            "将时间和发生具体时间和日期在story brain中。",
            "将事件发生在游戏第几天、几点几分记录在story brain中。",
        )
    return plan


def validate_plan(plan):
    """Validate manual edits independently of generation counts and time windows."""
    for event in plan.get("scene_events", []):
        validate_patterns(event.get("patterns"))
        if not str(event.get("location") or "").strip() or not str(event.get("event_content") or "").strip():
            raise ValueError("场景事件的地点和内容不能为空。")
    for event in plan.get("time_events", []):
        clock_minutes(event.get("time", ""))
        if type(event.get("day")) is not int or event["day"] < 1:
            raise ValueError("游戏第几天必须为正整数。")
        if not str(event.get("title") or "").strip() or not str(event.get("event_content") or "").strip():
            raise ValueError("时间事件的名称和内容不能为空。")
