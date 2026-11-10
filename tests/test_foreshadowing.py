import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.models import ChatRecord, Page2ConversationTurn as Turn, SystemPromptRecord
from app.services import foreshadowing as f, page2_service, system_prompts
from app.services.story_brain_prompts import remove_obsolete_foreshadow_rule
from web.pages import page2


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

    def test_calendar_jump_and_absolute_date(self):
        first = Turn(user_message="开始", assistant_message=">当前时间：2月12日 11:00，当前地点：学校")
        latest = Turn(user_message="继续", assistant_message=">当前时间：2月13日 15:00，当前地点：学校")
        config = {"enabled": True, "time_events": [event("relative", day=2), event("absolute", "14:30", date="2月13日"), event("tomorrow", date="2月14日")]}
        self.assertEqual([e["id"] for e in f.due_events(config, [first, latest])], ["relative", "absolute"])
        self.assertEqual(f.game_state([first, latest])["day"], 2)

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
        self.assertIn("【事件触发】", message)
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
        self.assertIn("【特殊事件】", context[0]["content"])
        self.assertNotIn("【特殊事件】", t.user_message)
        with patch.object(page2_service, "_send_with_current_chat_model", return_value="A已到家（主角并不知道此事）") as send:
            page2_service.generate_short_story_brain(ctx=page2_service.default_context(), turns=[t], story_brain_short="", long_term_memory_prompt="现有规则")
        self.assertEqual(send.call_args.kwargs["system_prompt"], "现有规则")
        self.assertIn("【特殊事件】home已到家", send.call_args.kwargs["user_message"])
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
            self.assertIn("【特殊事件】", send.call_args.kwargs["user_message"])
            self.assertEqual(state.page2_turns[-1].user_message, "继续")
            self.assertTrue(state.page2_turns[-1].special_events)
            task.error = RuntimeError("断网")
            with patch.object(page2, "_schedule_chat_followups"):
                page2._apply_chat_operation(task)
            self.assertEqual(state.page2_turns[-1].special_events, [])
            self.assertEqual(f.due_events(plan(), state.page2_turns), f.due_events(plan(), state.page2_turns[:-1]))

    def test_questions_return_text_and_only_final_setting_is_input(self):
        reply = "1. 世界的主要矛盾如何发展？\nA. 甲方向\nB. 乙方向\nC. 丙方向\nD. 丁方向"
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=reply) as send:
            result = f.generate("questions", page2_service.default_context(), "唯一最终设定")
        self.assertEqual(result, reply)
        self.assertIn("唯一最终设定", send.call_args.kwargs["user_message"])
        self.assertEqual(send.call_args.kwargs["context_messages"], [])
        self.assertEqual(send.call_args.kwargs["temperature"], 0.8)
        self.assertFalse(send.call_args.kwargs["thinking_enabled"])
        self.assertEqual(send.call_args.kwargs["ctx"].selected_chat_model, "deepseekFlash")
        self.assertFalse(send.call_args.kwargs["ctx"].chat_reasoning_enabled)

    def generation_payload(self):
        return {"story": "未来4天的世界背景：收粮队即将抵达，地下室中留有账本。",
                "scene_events": [{"location": "小A家&地下室", "patterns": ["小A家&地下室", "A家&地下室", "A住所&地下室", "A宅&地下室"], "event_content": "屋顶突然倒塌。"} for _ in range(5)],
                "time_events": [event(str(i)) for i in range(5)]}

    def test_background_and_events_generated_together_with_questions_and_answers(self):
        payload = self.generation_payload()
        ctx = page2_service.default_context()
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)) as send:
            result = f.generate("events", ctx, "设定", "1 A", questions="1. 问题")
        send.assert_called_once()
        self.assertIn("【8道问卷】\n1. 问题", send.call_args.kwargs["user_message"])
        self.assertIn("【用户回答】\n1 A", send.call_args.kwargs["user_message"])
        self.assertNotIn("已确认故事草案", send.call_args.kwargs["user_message"])
        self.assertEqual(result["story"], payload["story"])
        self.assertIs(send.call_args.kwargs["ctx"], ctx)
        self.assertTrue(send.call_args.kwargs["thinking_enabled"])
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)) as send:
            f.generate("events", ctx, "设定")
        self.assertIn("【8道问卷】\n空", send.call_args.kwargs["user_message"])
        self.assertIn("【用户回答】\n空", send.call_args.kwargs["user_message"])

    def test_two_rounds_dispatch_without_mutating_original_model(self):
        ctx = page2_service.default_context()
        original = deepcopy(ctx)
        with patch.object(page2_service, "_send_with_current_chat_model", side_effect=["问卷正文", json.dumps(self.generation_payload())]) as send:
            questions = f.generate("questions", ctx, "世界")
            result = f.generate("events", ctx, "世界", "选A", questions=questions)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(ctx, original)
        self.assertEqual(send.call_args_list[0].kwargs["ctx"].selected_chat_model, "deepseekFlash")
        self.assertFalse(send.call_args_list[0].kwargs["thinking_enabled"])
        self.assertEqual(result["story"], self.generation_payload()["story"])
        self.assertEqual(len(result["scene_events"]), 5)
        self.assertEqual(len(result["time_events"]), 5)

    def test_generation_rejects_missing_background_or_incomplete_events(self):
        payload = self.generation_payload()
        with patch.object(page2_service, "_send_with_current_chat_model", return_value=json.dumps(payload)):
            result = f.generate("events", page2_service.default_context(), "设定", "用户答案")
        self.assertEqual(len({e["id"] for group in ("scene_events", "time_events") for e in result[group]}), 10)
        missing_story = deepcopy(payload)
        missing_story.pop("story")
        missing_event = deepcopy(payload)
        missing_event["scene_events"].pop()
        for bad in ("不是JSON", "{}", "[]", json.dumps(missing_story), json.dumps(missing_event)):
            with patch.object(page2_service, "_send_with_current_chat_model", return_value=bad):
                with self.assertRaises(ValueError):
                    f.generate("events", page2_service.default_context(), "设定")

    def test_obsolete_rule_removal_preserves_other_text(self):
        self.assertEqual(remove_obsolete_foreshadow_rule("4.只记录结果 5.不记录与伏笔重复的内容\n其他规则"), "4.只记录结果\n其他规则")


if __name__ == "__main__":
    unittest.main()
