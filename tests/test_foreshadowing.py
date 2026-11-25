import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.models import ChatRecord, Page2ConversationTurn as Turn, SystemPromptRecord
from app.services import foreshadow_prompts, foreshadowing as f, page2_service, system_prompts
from app.services.story_brain_prompts import remove_obsolete_foreshadow_rule
from web.pages import page2


FINAL = "1. 背景介绍\n固定背景指令\n2. 世界书\n世界事实\n3. 用户角色\n用户设定\n4. 其他角色\n其他人物\n5. DM 的核心任务\n固定DM指令\n6. 回复格式要求\n固定格式指令"

def turn(day=1, time="15:00", location="A家的地下室", **kwargs):
    return Turn(user_message="继续", assistant_message=f">当前时间：第{day}天 {time}，当前地点：{location}", **kwargs)


def event(event_id, time="14:00", day=1, **kwargs):
    return {"id": event_id, "title": event_id, "time": time, "day": day,
            "event_content": event_id + "已到家。主角不知情时不在正文显示。", "enabled": True, **kwargs}


def plan():
    return {"enabled": True, "scene_events": [{"id": "basement", "title": "地下室秘密",
        "patterns": [r"(?=.*A家)(?=.*地下室)", r"小A家的地下室"], "event_content": "箱中藏着信件。"}],
        "time_events": [event("dinner", "14:30"), event("home"), event("future", "16:00")]}


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class ForeshadowingTests(unittest.TestCase):
    def test_skipped_times_all_fire_once_in_chronological_order(self):
        turns = [turn(time="11:00", location="自己的家"), turn(location="自己的家")]
        found = f.due_events(plan(), turns)
        self.assertEqual([e["id"] for e in found], ["home", "dinner"])
        self.assertIn("第1天 14:00", found[0]["event_content"])
        completed = turn(special_events=found)
        self.assertEqual([e["id"] for e in f.due_events(plan(), turns + [completed])], ["basement"])
        self.assertEqual([e["id"] for e in f.due_events(plan(), turns)], ["home", "dinner"])

    def test_scene_only_matches_actual_footer_location_and_fires_once(self):
        before = turn(location="B家的地下室")
        before.user_message = "我打算前往A家的地下室"
        self.assertNotIn("basement", [e["id"] for e in f.due_events(plan(), [before])])
        at = turn(time="10:00")
        found = f.due_events(plan(), [before, at])
        self.assertEqual([e["id"] for e in found], ["basement"])
        self.assertEqual(f.due_events(plan(), [at, turn(time="10:01", special_events=found)]), [])

    def test_disabled_plan_and_individual_events(self):
        config = plan()
        config["enabled"] = False
        self.assertEqual(f.due_events(config, [turn()]), [])
        config["enabled"] = True
        config["scene_events"][0]["enabled"] = False
        config["time_events"][1]["enabled"] = False
        self.assertEqual([e["id"] for e in f.due_events(config, [turn()])], ["dinner"])

    def test_calendar_history_still_derives_days_but_events_ignore_legacy_dates(self):
        first = Turn(user_message="开始", assistant_message=">当前时间：2月12日 11:00，当前地点：学校")
        latest = Turn(user_message="继续", assistant_message=">当前时间：2月13日 15:00，当前地点：学校")
        config = {"enabled": True, "time_events": [event("relative", day=2), event("absolute", "14:30", day=2, date="9999-12-31"), event("tomorrow", day=3, date="2月14日")]}
        self.assertEqual([e["id"] for e in f.due_events(config, [first, latest])], ["relative", "absolute"])
        self.assertEqual(f.game_state([first, latest])["day"], 2)
        found = f.due_events(config, [first, latest])
        self.assertTrue(all("date" not in row for row in found))
        self.assertTrue(all(row["event_content"].startswith("第2天") for row in found))

    def test_explicit_game_day_wins_over_inconsistent_legacy_calendar(self):
        turns = [Turn(assistant_message=">当前时间：第8天 12月31日 23:00，当前地点：家"),
                 Turn(assistant_message=">当前时间：第9天 12月31日 00:30，当前地点：家")]
        self.assertEqual(f.game_state(turns)["day"], 9)

    def test_midnight_and_year_boundary(self):
        turns = [Turn(assistant_message=">当前时间：12月31日 23:00，当前地点：家"),
                 Turn(assistant_message=">当前时间：1月1日 00:30，当前地点：家")]
        state = f.game_state(turns)
        self.assertEqual((state["day"], state["calendar"].year), (2, 2001))
        state = f.game_state([turn(time="23:00"), Turn(assistant_message=">当前时间：00:30，当前地点：家")])
        self.assertEqual(state["day"], 2)

    def test_failed_and_empty_replies_do_not_consume(self):
        found = f.due_events(plan(), [turn()])
        for response in (None, "", "请求失败，请稍后重试。\n断网"):
            failed = Turn(user_message="继续", assistant_message=response, special_events=found)
            self.assertEqual(f.due_events(plan(), [turn(), failed]), found)

    def test_unparseable_time_does_not_guess(self):
        state_turn = Turn(assistant_message=">当前时间：下午，当前地点：自己的家")
        self.assertEqual(f.due_events(plan(), [turn(), state_turn]), [])
        self.assertEqual(f.due_events(plan(), [Turn(assistant_message="你计划14:00前往A家的地下室")]), [])

    def test_regex_validation_and_timeout(self):
        with self.assertRaises(ValueError):
            f.validate_patterns(["["])
        self.assertFalse(f.matches([r"(a+)+$"], "a" * 999 + "!"))
        self.assertTrue(f.matches(["小A家的地下室", "A家.*地下室"], "小A家的地下室角落"))
        self.assertTrue(f.matches(["猪圈后栅栏"], "猪圈后的栅栏"))
        self.assertTrue(f.matches(["A家 地下室"], "小A家的地下室角落"))
        self.assertTrue(f.matches(["道合 祭坛"], "旧道合的荒废祭坛"))
        self.assertTrue(f.matches(["道合&祭坛"], "祭坛旁的道合石碑"))
        self.assertTrue(f.matches(["道合+石碑"], "石碑林中的道合遗址"))
        self.assertFalse(f.matches(["道合 祭坛"], "道合石碑"))
        self.assertFalse(f.matches(["道合+石碑"], "道合祭坛"))
        self.assertFalse(f.matches(["猪圈后栅栏"], "猪圈后墙"))

    def test_triggered_events_are_written_to_chat_log(self):
        state = SessionState(page2_foreshadowing=plan())
        with patch.object(page2.st, "session_state", state), patch.object(page2, "debug_log") as log:
            events = page2._events_for_turn([turn()])
        self.assertTrue(events)
        log.assert_called_once()
        message = log.call_args.args[0]
        self.assertIn("【候选事件】", message)
        self.assertIn("地下室秘密", message)
        self.assertIn("home已到家", message)
        self.assertEqual(log.call_args.kwargs, {"category": "chat"})

    def test_snapshot_and_serialization_are_independent_from_templates_and_first_inputs(self):
        authored = f.new_plan()
        authored.update(plan())
        config = {"enabled": True, "plans": [authored], "selected_plan_id": authored["id"]}
        snapshot = f.snapshot(config)
        record = SystemPromptRecord(title="测试", prompt="世界", first_input="开局一", foreshadowing=config)
        record.first_input = "开局二"
        loaded = SystemPromptRecord.from_dict(record.to_dict())
        self.assertEqual(loaded.foreshadowing, config)
        config["plans"][0]["time_events"][0]["event_content"] = "改写"
        self.assertNotEqual(snapshot["time_events"][0]["event_content"], "改写")
        turns = [turn(), turn(special_events=f.due_events(snapshot, [turn()]))]
        game = ChatRecord(title="游戏", turns=turns, system_prompt="原设定", foreshadowing=snapshot)
        restored = ChatRecord.from_dict(game.to_dict())
        self.assertEqual(restored.foreshadowing, snapshot)
        self.assertEqual(f.due_events(snapshot, restored.turns), [])
        self.assertTrue(f.due_events(snapshot, restored.turns[:-1]))

    def test_hidden_events_reach_chat_history_and_memory_without_changing_system(self):
        t = Turn(user_message="我在自己家看书", assistant_message="书页翻动。", special_events=[event("home")])
        context = page2_service.build_context_messages([t], 1)
        self.assertIn("【历史事件提示】home已到家", context[0]["content"])
        self.assertNotIn(f.EVENT_PREFIX, t.user_message)
        with patch.object(page2_service, "_send_with_current_chat_model", return_value="A已到家（主角并不知道此事）") as send:
            page2_service.generate_short_story_brain(ctx=page2_service.default_context(), turns=[t], story_brain_short="", long_term_memory_prompt="现有规则")
        self.assertEqual(send.call_args.kwargs["system_prompt"], "现有规则")
        self.assertIn("【历史事件提示】home已到家", send.call_args.kwargs["user_message"])
        self.assertIn("书页翻动", send.call_args.kwargs["user_message"])

    def test_first_memory_update_does_not_drop_old_hidden_events(self):
        turns = [turn(special_events=[event("home")])] + [turn() for _ in range(10)]
        self.assertEqual(page2._story_brain_turns_since_last_update(turns, "", 2), turns)
        self.assertEqual(page2._story_brain_turns_since_last_update(turns, turns[5].id, 2), turns[6:])

    def test_chat_stores_visible_input_separately_and_failure_releases_events(self):
        state = SessionState(page2_turns=[turn()], page2_foreshadowing=plan(), page2_story_brain_short="")
        task = page2._Page2OperationTask(kind="chat", label="发送", scope_id="test", payload={"ctx": page2_service.default_context(), "user_text": "继续"})
        with patch.object(page2.st, "session_state", state), patch.object(page2, "_upsert_record"), patch.object(page2_service, "send_message", return_value="正文") as send:
            page2._prepare_chat_operation(task)()
            self.assertIn(f.EVENT_PREFIX, send.call_args.kwargs["user_message"])
            self.assertEqual(state.page2_turns[-1].user_message, "继续")
            self.assertTrue(task.meta["pending_events"])
            self.assertEqual(state.page2_turns[-1].special_events, [])
            task.error = RuntimeError("断网")
            with patch.object(page2, "_schedule_chat_followups"):
                page2._apply_chat_operation(task)
            self.assertEqual(state.page2_turns[-1].special_events, [])
            self.assertEqual(f.due_events(plan(), state.page2_turns), f.due_events(plan(), state.page2_turns[:-1]))

    def test_confirmed_hidden_time_event_enters_memory_without_replaying_other_candidates(self):
        pending = f.due_events(plan(), [turn()])
        result = {"home": {"status": "occurred", "summary": "邻居回家。", "player_knows": False},
                  "basement": {"status": "invalid"}}
        story = ">当前时间：第1天 15:00，当前地点：A家的地下室"
        reply, outcomes = f.resolve_events(story + "\n\n" + f.EVENT_RESULT_MARKER + "\n" + json.dumps(result), pending)
        self.assertEqual(reply, story)
        saved = Turn.from_dict(Turn(user_message="继续", assistant_message=reply, special_events=outcomes).to_dict())
        self.assertEqual(saved.special_events[0], {"id": "basement", "status": "invalid"})
        self.assertEqual([row["id"] for row in f.due_events(plan(), [saved])], ["dinner"])
        self.assertNotIn("箱中藏着信件", f.turn_user_message(saved))
        self.assertNotIn("dinner", f.turn_user_message(saved))
        with patch.object(page2_service, "_send_with_current_chat_model", return_value="常规记忆"):
            memory = page2_service.generate_short_story_brain(ctx=page2_service.default_context(),
                                                              turns=[saved], story_brain_short="")
        self.assertIn("第1天 14:00 邻居回家。（用户角色当时不知情）", memory)
        self.assertNotIn("箱中藏着信件", memory)

    def test_runtime_replaces_old_suffixes_without_altering_event_body(self):
        plan_with_old_suffixes = plan()
        plan_with_old_suffixes["scene_events"][0]["event_content"] = "手写地点。" + f._OLD_SCENE_SUFFIX + "。"
        plan_with_old_suffixes["time_events"][1]["event_content"] = (
            "手写时间。" + f._OLD_TIME_SUFFIX + "将时间和发生具体时间和日期在story brain中。"
        )
        scene, timed, _ = f.due_events(plan_with_old_suffixes, [turn()])
        self.assertEqual(scene["event_content"], "手写地点。\n" + foreshadow_prompts.SCENE_EVENT_SUFFIX)
        self.assertEqual(timed["event_content"], "第1天 14:00：手写时间。\n" + foreshadow_prompts.TIME_EVENT_SUFFIX)

    def test_scene_followup_stays_in_chat_context_without_becoming_a_world_fact(self):
        pending = f.due_events(plan(), [turn(time="10:00")])
        followup = "if 用户打开箱子 then 能看到信。"
        trailer = {"basement": {"status": "occurred", "player_knows": True, "followup": followup}}
        reply, outcomes = f.resolve_events(f"房间角落有箱子。\n**{f.EVENT_RESULT_MARKER}**：\n{json.dumps(trailer)}", pending)
        saved = Turn(user_message="继续", assistant_message=reply, special_events=outcomes)
        chat = page2_service.build_context_messages([saved], 1, include_scene_followups=True)
        self.assertIn(followup, chat[0]["content"])
        self.assertNotIn(followup, f.turn_user_message(saved))

    def test_removed_questionnaire_cannot_call_model(self):
        with patch.object(page2_service, "_send_with_current_chat_model") as send:
            with self.assertRaisesRegex(ValueError, "已取消问卷"):
                f.generate("questions", page2_service.default_context(), FINAL)
        send.assert_not_called()

    def generation_payload(self):
        target = "小A家&地下室"
        leads = [event("口信", "00:30", leads_to_location=target,
                       event_content="邻居带来口信，邀请主角去小A家的地下室辨认失物。"),
                 event("账本", "02:00", leads_to_location=target,
                       event_content="收粮队送来账本残页，纸上注明剩余账本在A宅的地下室。")]
        return {"story": "未来6小时的世界背景：收粮队即将抵达，地下室中留有账本。",
                "scene_events": [{"location": target, "patterns": [target, "A家&地下室", "A住所&地下室", "A宅&地下室"], "event_content": "屋顶突然倒塌。"} for _ in range(5)],
                "time_events": leads + [event(str(i), f"{3 + i:02d}:00") for i in range(3)]}

    def test_single_call_uses_only_material_recent_chat_and_brain(self):
        ctx = page2_service.default_context()
        original = deepcopy(ctx)
        turns = [Turn(user_message=f"输入{i}", assistant_message=f"回复{i}") for i in range(9)]
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(self.generation_payload())) as send:
            result = f.generate("events", ctx, FINAL, turns=turns, story_brain="既有记忆", history_turn_count=6)
        send.assert_called_once()
        user = send.call_args.kwargs["user_message"]
        for value in ("世界事实", "用户设定", "其他人物", "既有记忆", "回复3", "回复8"):
            self.assertIn(value, user)
        for value in ("固定背景指令", "固定DM指令", "固定格式指令", "回复2", "问卷", "用户回答", "用户期望的事件生成方向"):
            self.assertNotIn(value, user)
        self.assertIn("参考story brain和过去的聊天记录以最新轮次时间点作为初始时间点往后设计事件", user)
        for prompt in (user, send.call_args.kwargs["system_prompt"]):
            self.assertIn("未来6小时", prompt)
            self.assertIn("2小时内", prompt)
            self.assertNotIn("48小时", prompt)
        self.assertEqual(result["story"], self.generation_payload()["story"])
        self.assertEqual(ctx, original)
        self.assertIs(send.call_args.kwargs["ctx"], ctx)
        self.assertTrue(send.call_args.kwargs["thinking_enabled"])

    def test_captured_direction_is_only_added_to_the_event_user_prompt(self):
        context = f.generation_context(FINAL)
        context["direction"] = "  围绕港口失踪案展开。\n增加与{旧码头}有关的线索。\n"
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(self.generation_payload())) as send:
            f.generate("events", page2_service.default_context(), FINAL, captured_context=context)
        self.assertEqual(send.call_args.kwargs["system_prompt"], f.prompts.EVENTS_SYSTEM)
        self.assertIn(
            "【用户期望的事件生成方向】\n围绕港口失踪案展开。\n增加与{旧码头}有关的线索。\n",
            send.call_args.kwargs["user_message"],
        )

    def test_missing_or_duplicate_sections_do_not_fall_back_to_full_prompt(self):
        for source in ("未分区正文", FINAL + "\n2. 世界书\n重复"):
            with patch.object(page2_service, "_send_with_current_chat_model") as send:
                with self.assertRaises(ValueError):
                    f.generate("events", page2_service.default_context(), source)
            send.assert_not_called()

    def test_other_characters_are_optional_when_missing_or_empty(self):
        for replacement in ("", "4. 其他角色\n\n"):
            source = FINAL.replace("4. 其他角色\n其他人物\n", replacement)
            with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(self.generation_payload())) as send:
                result = f.generate("events", page2_service.default_context(), source)
            self.assertEqual(len(result["scene_events"]), 5)
            self.assertIn("世界事实", send.call_args.kwargs["user_message"])
            self.assertIn("用户设定", send.call_args.kwargs["user_message"])
            self.assertNotIn("固定DM指令", send.call_args.kwargs["user_message"])
            self.assertNotIn("4. 其他角色", send.call_args.kwargs["user_message"])

    def test_markdown_subheadings_and_content_reach_model_unchanged(self):
        source = "# 背景介绍\n不要发送的固定背景\n# 世界书\n世界导言\n## 城市\n城市事实\n### 港口\n港口细节\n# 用户角色\n## 身份\n用户身份\n### 经历\n经历正文\n# 其他角色\n## 同伴\n### 秘密\n同伴秘密\n# DM 的核心任务\n不要发送的DM任务\n# 回复格式要求\n不要发送的格式"
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(self.generation_payload())) as send:
            f.generate("events", page2_service.default_context(), source)
        user = send.call_args.kwargs["user_message"]
        for subtree in ("# 世界书\n世界导言\n## 城市\n城市事实\n### 港口\n港口细节",
                        "# 用户角色\n## 身份\n用户身份\n### 经历\n经历正文",
                        "# 其他角色\n## 同伴\n### 秘密\n同伴秘密"):
            self.assertIn(subtree, user)
        for fixed in ("不要发送的固定背景", "不要发送的DM任务", "不要发送的格式"):
            self.assertNotIn(fixed, user)

    def test_numbered_markdown_under_a_document_heading_and_optional_other(self):
        source = "# 最终设定\n## 2. 世界书\n### 地理\n地理细节\n## 3. 用户角色\n### 身份\n人物身份\n## 5. DM 的核心任务\n固定指令"
        material = f.extract_story_material(source)
        self.assertIn("### 地理\n地理细节", material)
        self.assertIn("### 身份\n人物身份", material)
        self.assertNotIn("固定指令", material)

    def test_code_fence_headings_and_deeper_matching_names_stay_inside_parent(self):
        source = "# 世界书\n## 用户角色\n世界书内的角色说明\n```markdown\n# 用户角色\n示例代码\n```\n# 用户角色\n真实角色\n# 附注\n不属于角色的附注\n"
        material = f.extract_story_material(source)
        self.assertIn("## 用户角色\n世界书内的角色说明", material)
        self.assertIn("```markdown\n# 用户角色\n示例代码\n```", material)
        self.assertIn("# 用户角色\n真实角色", material)
        self.assertNotIn("不属于角色的附注", material)

    def test_plain_sections_keep_nested_markdown_and_still_require_user(self):
        source = FINAL.replace("世界事实", "## 地理\n### 街道\n街道事实")
        self.assertIn("## 地理\n### 街道\n街道事实", f.extract_story_material(source))
        with self.assertRaisesRegex(ValueError, "用户角色"):
            f.extract_story_material("# 世界书\n世界事实\n# 其他角色\n同伴事实")

    def test_new_final_settings_use_markdown_without_breaking_legacy_sync(self):
        from app.models import FixedTemplateRecord, PromptTextRecord
        from app.services import prompt_assets
        template = FixedTemplateRecord(title="模板", background_intro="1. 背景介绍\n背景", dm_core_tasks="任务", response_format="格式")
        result = prompt_assets.build_final_prompt(
            template, PromptTextRecord(title="世界", content="## 地理\n### 港口\n港口细节"),
            PromptTextRecord(title="主角", content="## 身份\n角色身份"), [],
        )
        self.assertEqual([line for line in result.splitlines() if line.startswith("# ")],
                         ["# " + heading.split(". ", 1)[1] for heading in prompt_assets.FINAL_SECTION_HEADINGS])
        legacy = result
        for heading in prompt_assets.FINAL_SECTION_HEADINGS:
            legacy = legacy.replace("# " + heading.split(". ", 1)[1] + "\n", heading + "\n")
        self.assertEqual(prompt_assets._parse_final_sections(result), prompt_assets._parse_final_sections(legacy))
        self.assertEqual(prompt_assets._parse_available_final_sections(result), prompt_assets._parse_available_final_sections(legacy))
        self.assertIn("## 地理\n### 港口\n港口细节", f.extract_story_material(result))

    def test_generation_rejects_missing_background_or_incomplete_events(self):
        payload = self.generation_payload()
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
            result = f.generate("events", page2_service.default_context(), FINAL)
        self.assertEqual(len({e["id"] for group in ("scene_events", "time_events") for e in result[group]}), 10)
        missing_story = deepcopy(payload)
        missing_story.pop("story")
        missing_event = deepcopy(payload)
        missing_event["scene_events"].pop()
        for bad in ("不是JSON", "{}", "[]", json.dumps(missing_story), json.dumps(missing_event)):
            with patch.object(page2_service, "_send_with_current_chat_model", return_value=bad):
                with self.assertRaises(ValueError):
                    f.generate("events", page2_service.default_context(), FINAL)

    def test_opening_accepts_times_after_midnight_up_to_6_hours(self):
        payload = self.generation_payload()
        payload["time_events"][-1].update(day=1, time="06:00")
        ctx = page2_service.default_context()
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
            result = f.generate("events", ctx, FINAL)
        self.assertEqual(result["time_events"][-1]["day"], 1)
        self.assertEqual(result["time_events"][-1]["time"], "06:00")
        for day, clock, error in ((0, "04:00", "正整数"), (1, "00:00", "6小时"),
                                  (1, "06:01", "6小时"), (2, "00:00", "6小时")):
            with self.subTest(day=day, clock=clock):
                payload["time_events"][-1].update(day=day, time=clock)
                with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
                    with self.assertRaisesRegex(ValueError, error):
                        f.generate("events", ctx, FINAL)

    def test_current_game_clock_bounds_and_calendar_rollover(self):
        turns = [Turn(assistant_message=">当前时间：2026年12月31日 第8天 23:00，当前地点：学校")]
        payload = self.generation_payload()
        for row, clock in zip(payload["time_events"], ("00:10", "01:00", "02:00", "04:00", "05:00"), strict=True):
            row.update(day=9, time=clock, date="1999-01-01")
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)) as send:
            result = f.generate("events", page2_service.default_context(), FINAL, turns=turns)
        self.assertIn("游戏第8天 23:00", send.call_args.kwargs["user_message"])
        self.assertNotIn("2026-12-31", send.call_args.kwargs["user_message"])
        self.assertNotIn("date", result["time_events"][0])
        self.assertEqual(result["time_events"][1]["time"], "01:00")
        self.assertEqual(result["time_events"][-1]["time"], "05:00")
        for day, clock in ((8, "22:59"), (8, "23:00"), (9, "05:01"), (10, "00:00"), (1, "14:00")):
            with self.subTest(day=day, clock=clock):
                payload["time_events"][0].update(day=day, time=clock)
                with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
                    with self.assertRaisesRegex(ValueError, "6小时"):
                        f.generate("events", page2_service.default_context(), FINAL, turns=turns)

    def test_generation_rejects_wrong_lead_windows_and_destinations(self):
        for index, change, error in (
            (0, {"time": "02:01"}, "2小时内"),
            (2, {"time": "01:59"}, "2小时内"),
            (0, {"leads_to_location": "市场"}, "写清目的地"),
            (0, {"event_content": "邻居送来一条口信。"}, "写清目的地"),
            (2, {"time": "01:45", "leads_to_location": "小A家&地下室",
                 "event_content": "主角收到一封邀请去小A家地下室的信。"}, "恰好2条"),
        ):
            with self.subTest(index=index, change=change):
                payload = self.generation_payload()
                payload["time_events"][index].update(change)
                with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
                    with self.assertRaisesRegex(ValueError, error):
                        f.generate("events", page2_service.default_context(), FINAL)

    def test_lead_content_triggers_first_and_scene_waits_for_arrival(self):
        payload = self.generation_payload()
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
            result = f.generate("events", page2_service.default_context(), FINAL)
        first_lead = result["time_events"][0]
        self.assertNotIn("leads_to_location", first_lead)
        config = {**result, "enabled": True}
        before_arrival = turn(time="00:30", location="学校")
        leads = f.due_events(config, [before_arrival])
        self.assertEqual([lead["id"] for lead in leads], [first_lead["id"]])
        self.assertIn("小A家的地下室", leads[0]["event_content"])
        arrival = turn(time="00:35", special_events=leads)
        triggered = f.due_events(config, [before_arrival, arrival])
        self.assertIn(result["scene_events"][0]["id"], [item["id"] for item in triggered])

    def test_missing_footer_leaves_time_inference_to_latest_history(self):
        turns = [Turn(user_message="继续", assistant_message="第8天的下午，学校里仍然很安静。")]
        context = f.generation_context(FINAL, turns, "第8天仍在学校")
        self.assertIn("不默认重置", context["initial_time"])
        self.assertIsNone(context["clock"]["minute"])
        payload = self.generation_payload()
        for row, clock in zip(payload["time_events"], ("15:30", "16:00", "17:30", "20:00", "21:00"), strict=True):
            row.update(day=8, time=clock)
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
            result = f.generate("events", page2_service.default_context(), FINAL, captured_context=context)
        self.assertEqual(result["time_events"][0]["day"], 8)

    def test_obsolete_rule_removal_preserves_other_text(self):
        self.assertEqual(remove_obsolete_foreshadow_rule("4.只记录结果 5.不记录与伏笔重复的内容\n其他规则"), "4.只记录结果\n其他规则")


if __name__ == "__main__":
    unittest.main()
