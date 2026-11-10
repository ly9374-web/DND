"""Authoring, location matching and one-shot events, independent of first inputs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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
    return {**plan, "enabled": bool(config.get("enabled", True)) and bool(plan)}


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
            if origin is None:
                origin = calendar.toordinal() - (int(day[1]) - 1 if day else state["day"] - 1)
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
    consumed = {e.get("id") for t in turns if successful(t)
                for e in getattr(t, "special_events", [])}
    found = []
    for event in plan.get("scene_events", []):
        if event.get("enabled", True) and event.get("id") not in consumed and matches(event.get("patterns", []), state["location"]):
            found.append(deepcopy(event))
    timed = []
    if state["minute"] is not None:
        for event in plan.get("time_events", []):
            if not event.get("enabled", True) or event.get("id") in consumed:
                continue
            try:
                minute = clock_minutes(event.get("time", ""))
                if event.get("date"):
                    current = state["calendar"]
                    calendar = _calendar(event["date"], current.year if current else 2000)
                    if current is None or calendar is None:
                        continue
                    due = calendar.toordinal() * 1440 + minute
                    now = current.toordinal() * 1440 + state["minute"]
                else:
                    due = (int(event["day"]) - 1) * 1440 + minute
                    now = (state["day"] - 1) * 1440 + state["minute"]
                if due <= now:
                    copy = deepcopy(event)
                    when = event.get("date") or f"第{event['day']}天"
                    copy["event_content"] = f"{when} {event['time']}：{event['event_content']}"
                    # Sort on a shared relative axis when absolute and relative dates mix.
                    timed.append((due - now, copy))
            except (ValueError, TypeError, KeyError):
                continue
    return found + [e for _, e in sorted(timed, key=lambda pair: pair[0])]


def successful(turn):
    return bool(str(turn.assistant_message or "").strip()) and not turn.is_loading and not str(turn.assistant_message).startswith("请求失败，请稍后重试。")


def append_events(message, events):
    parts = [str(message or "")]
    parts.extend("【特殊事件】" + str(event.get("event_content", "")) for event in events)
    return "\n\n".join(parts)


def turn_user_message(turn):
    return append_events(turn.user_message, getattr(turn, "special_events", []))


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


def generate(stage, ctx, final_setting, answers="", questions=""):
    from app.services import page2_service
    system, user = {
        "questions": (prompts.QUESTIONS_SYSTEM, prompts.QUESTIONS_USER),
        "events": (prompts.EVENTS_SYSTEM, prompts.EVENTS_USER),
    }[stage]
    result = page2_service._send_with_current_chat_model(
        ctx=replace(ctx, selected_chat_model="deepseekFlash", chat_reasoning_enabled=False) if stage == "questions" else ctx,
        system_prompt=system, context_messages=[],
        user_message=user.format(
            final_setting=final_setting,
            questions=str(questions or "").strip() or "空",
            answers=str(answers or "").strip() or "空"),
        temperature=0.8, thinking_enabled=stage != "questions",
        log_label={"questions": "事件问卷", "events": "生成背景与事件"}[stage],
    )
    if stage == "questions":
        if not str(result or "").strip():
            raise ValueError("模型返回了空内容，请重试。")
        return str(result).strip()
    data = _json(result)
    if not isinstance(data.get("story"), str) or not data["story"].strip():
        raise ValueError("模型没有返回故事背景，请重新生成；原方案未被修改。")
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
            else:
                if not isinstance(event.get("title"), str) or not event["title"].strip():
                    raise ValueError("时间事件必须包含名称。")
                clock_minutes(event.get("time", ""))
                if type(event.get("day")) is not int or not 1 <= event["day"] <= 4:
                    raise ValueError("生成的时间事件应在第1至第4天内。")
            event["id"] = uuid4().hex
            event["enabled"] = True
    return {key: data[key] for key in ("story", "scene_events", "time_events")}
