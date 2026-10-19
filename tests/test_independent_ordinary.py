import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="vs-ordinary-independent-test-"))
os.environ["VS_IMAGE_NOVEL_DATA_DIR"] = str(TEST_DATA_DIR)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import SettingsStore
from app.default_prompts.internal_catalog import prompt as internal_prompt, render as render_internal_prompt
from app.models import (
    ChatRecord,
    FixedTemplateRecord,
    Page2ConversationTurn,
    PromptTextRecord,
    SystemPromptRecord,
)
from app.services import chat_records, page2_service, prompt_assets, system_prompts


class IndependentOrdinaryTests(unittest.TestCase):
    def _image_prompt_editor_app(self):
        from streamlit.testing.v1 import AppTest

        return AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from web.pages import page2

page2._ensure_state()
with (
    patch.object(page2.url_favorites, "get_preview_url", return_value=""),
    patch.object(page2.url_favorites, "render_url_favorites"),
    patch.object(page2.page2_service, "load_context_from_settings",
                 return_value=page2.page2_service.default_context()),
):
    page2._render_media_column()

if st.button("模拟生成完成"):
    task = page2._Page2OperationTask(
        kind="image_prompt", label="生成图片 prompt", scope_id="test"
    )
    task.result = "模型生成的图片描述"
    page2._apply_operation(task)
    st.rerun()
''').run()

    def test_image_prompt_consecutive_edits_survive_reruns(self):
        app = self._image_prompt_editor_app()
        for value in ("第一版草稿", "第二版草稿", "", "重新输入\n第二行"):
            app.text_area(key="_page2_image_prompt").input(value).run()
            self.assertEqual(app.text_area(key="_page2_image_prompt").value, value)
            self.assertEqual(app.session_state["page2_image_prompt"], value)
            app.run()
            self.assertEqual(app.text_area(key="_page2_image_prompt").value, value)
            self.assertEqual(len(app.exception), 0)

    def test_image_prompt_subject_survives_edits_and_mode_changes(self):
        app = self._image_prompt_editor_app()
        app.selectbox(key="_page2_image_prompt_mode").select("closeup").run()
        for value in ("角色甲", "角色乙"):
            app.text_input(key="_page2_image_prompt_subject").input(value).run()
            self.assertEqual(app.text_input(key="_page2_image_prompt_subject").value, value)
        app.selectbox(key="_page2_image_prompt_mode").select("normal").run()
        app.run()
        app.selectbox(key="_page2_image_prompt_mode").select("first_person").run()
        self.assertEqual(len(app.text_input), 0)
        self.assertEqual(len(app.exception), 0)

    def test_generated_image_prompt_can_be_written_after_render_and_edited(self):
        app = self._image_prompt_editor_app()
        app.text_area(key="_page2_image_prompt").input("旧草稿").run()
        next(button for button in app.button if button.label == "模拟生成完成").click().run()
        self.assertEqual(app.text_area(key="_page2_image_prompt").value, "模型生成的图片描述")
        app.text_area(key="_page2_image_prompt").input("手动修改模型结果").run()
        app.run()
        self.assertEqual(app.text_area(key="_page2_image_prompt").value, "手动修改模型结果")
        self.assertEqual(len(app.exception), 0)

    def test_empty_image_prompt_queues_prompt_then_image_but_nonempty_skips_prompt(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        turn = Page2ConversationTurn(id="t8", assistant_message="第八轮")
        session = SessionState(
            page2_operation_scope_id="scope",
            page2_operation_queue=[],
            page2_active_operation=None,
            page2_operation_notice="",
            page2_turns=[turn],
            page2_image_prompt_source={"source_turn_id": "t8"},
        )
        with patch.object(page2.st, "session_state", session):
            image_task = page2._queue_image_generation(
                provider="grok", prompt="", image_urls=[], aspect_ratio="3:4",
                mode="normal", subject="",
            )
            self.assertEqual([task.kind for task in session.page2_operation_queue], [
                "image_prompt", "image",
            ])
            prompt_task = session.page2_operation_queue[0]
            self.assertEqual(
                image_task.payload["prompt_operation_id"], prompt_task.id
            )
            self.assertEqual(image_task.payload["prompt"], "")

            session.page2_operation_queue = []
            image_task = page2._queue_image_generation(
                provider="grok", prompt="手工图片 prompt", image_urls=[],
                aspect_ratio="3:4", mode="normal", subject="",
                refiner_prompt="correct hands",
            )
            self.assertEqual([task.kind for task in session.page2_operation_queue], ["image"])
            self.assertEqual(image_task.payload["prompt_operation_id"], "")
            self.assertEqual(
                image_task.payload["image_prompt_source"], {"source_turn_id": "t8"}
            )
            self.assertEqual(image_task.payload["refiner_prompt"], "correct hands")

    def test_user_operations_of_the_same_kind_append_in_click_order(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        session = SessionState(
            page2_operation_scope_id="scope",
            page2_operation_queue=[],
            page2_active_operation=None,
            page2_operation_notice="",
        )
        ctx = page2_service.default_context()
        with patch.object(page2.st, "session_state", session):
            page2._queue_chat_message(
                ctx=ctx,
                user_text="第一条",
                hide_user_message=False,
                story_brain_enabled=True,
            )
            page2._queue_chat_message(
                ctx=ctx,
                user_text="第二条",
                hide_user_message=False,
                story_brain_enabled=True,
            )
            page2._enqueue_operation(
                "image",
                "第一张图",
                {"prompt": "one"},
                allow_same_kind=True,
            )
            page2._enqueue_operation(
                "image",
                "第二张图",
                {"prompt": "two"},
                allow_same_kind=True,
            )

        self.assertEqual(
            [task.kind for task in session.page2_operation_queue],
            ["chat", "chat", "image", "image"],
        )
        self.assertEqual(
            [task.payload.get("user_text") for task in session.page2_operation_queue[:2]],
            ["第一条", "第二条"],
        )

    def test_first_person_has_no_subject_requirement_but_closeup_does(self):
        from app.services.image_prompts import build_image_prompt_system_prompt

        self.assertIn(
            "以我为第一人称视角",
            build_image_prompt_system_prompt("first_person", ""),
        )
        with self.assertRaisesRegex(ValueError, "主体不能为空"):
            build_image_prompt_system_prompt("closeup", "")

    def test_long_story_brain_implementation_is_not_packaged(self):
        self.assertFalse((ROOT / "agent_extension").exists())
        self.assertFalse((ROOT / "graph_view.py").exists())
        self.assertFalse((ROOT / "story_brain.py").exists())
        self.assertFalse((ROOT / "web/components/story_brain_graph").exists())
        self.assertFalse((ROOT / ".python_app_data/AgentChatRecords").exists())

        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("pyvis", requirements)
        self.assertNotIn("networkx", requirements)
        self.assertNotIn("anthropic", requirements)

    def test_packaged_settings_have_no_agent_only_keys(self):
        settings_path = ROOT / ".python_app_data/user_defaults.json"
        if not settings_path.is_file():
            self.skipTest("private runtime settings are intentionally not packaged")
        values = json.loads(settings_path.read_text(encoding="utf-8"))
        forbidden_keys = {
            "agentActionDecisionHistoryTurns",
            "agentChatRecordIndex",
            "agentEvolutionRounds",
            "agentNPCHistoryTurns",
            "agentPlayerRouteHistoryTurns",
            "agentPromptRecordNextIndex",
            "agentPromptRecords",
            "agentSceneHistoryTurns",
            "agentSelectedChatModel",
            "agentStoryBrainTurns",
            "agentTemperature",
            "claudeApiKey",
            "guestAgentChatRecordIndex",
            "hiddenAgentPromptRecordNextIndex",
            "hiddenAgentPromptRecords",
            "page2StoryBrainMode",
            "selectedAgentPromptRecordID",
        }
        self.assertFalse(forbidden_keys.intersection(values))

    def test_every_packaged_chat_file_is_visible_in_the_index(self):
        settings_path = ROOT / ".python_app_data/user_defaults.json"
        if not settings_path.is_file():
            self.skipTest("private chat records are intentionally not packaged")
        values = json.loads(settings_path.read_text(encoding="utf-8"))
        index = json.loads(values.get("chatRecordIndex") or "[]")
        indexed_files = {str(item.get("file_name") or "") for item in index}
        record_files = {
            path.name
            for path in (ROOT / ".python_app_data/ChatRecords").glob("record-*.json")
        }
        self.assertEqual(indexed_files, record_files)

    def test_context_has_only_short_story_brain_mode(self):
        ctx = page2_service.default_context()
        self.assertFalse(hasattr(ctx, "story_brain_mode"))
        self.assertFalse(hasattr(page2_service, "STORY_BRAIN_LONG"))
        self.assertFalse(hasattr(page2_service, "STORY_BRAIN_MODES"))

    def test_short_story_brain_is_injected_without_archive(self):
        ctx = page2_service.default_context()
        ctx.system_prompt = "原始 system"
        turns = [
            Page2ConversationTurn(
                user_message="旧用户消息",
                assistant_message="旧回复",
            )
        ]

        with patch.object(
            page2_service.GrokAPIClient,
            "send_message",
            return_value="新回复",
        ) as send_message:
            result = page2_service.send_message(
                ctx=ctx,
                turns=turns,
                user_message="打开门",
                story_brain_short="雨夜的旅店",
                story_brain_enabled=True,
            )

        self.assertEqual(result, "新回复")
        kwargs = send_message.call_args.kwargs
        self.assertEqual(kwargs["system_prompt"], "原始 system")
        self.assertEqual(
            kwargs["user_message"],
            render_internal_prompt(
                "story_brain", "context_wrapper",
                story_brain="雨夜的旅店", user_message="打开门",
            ),
        )
        self.assertNotIn("characters", str(kwargs))
        self.assertNotIn("Memory Pack", str(kwargs))

    def test_image_prompt_uses_latest_conversation_and_story_brain_without_chat_turn(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        ctx = page2_service.default_context()
        turns = [
            Page2ConversationTurn(user_message="继续", assistant_message="剧情回复"),
            Page2ConversationTurn(user_message="查看四周", assistant_message="最新的场景描述"),
        ]
        task = page2._Page2OperationTask(
            kind="image_prompt",
            label="生成图片 prompt",
            payload={"ctx": ctx, "mode": "closeup", "subject": "艾琳"},
            scope_id="test-scope",
        )
        session_state = SessionState(
            page2_turns=turns,
            page2_story_brain_short="雨夜旅店",
            page2_story_brain_enabled=True,
        )

        with patch.object(page2.st, "session_state", session_state):
            with patch.object(
                page2_service,
                "generate_image_prompt",
                return_value="最终图片 prompt",
            ) as generate_prompt:
                operation = page2._prepare_image_prompt_operation(task)
                result = operation()

        self.assertEqual(result, "最终图片 prompt")
        generate_prompt.assert_called_once_with(
            ctx=ctx,
            turns=turns,
            story_brain_short="雨夜旅店",
            story_brain_enabled=True,
            mode="closeup",
            subject="艾琳",
        )
        self.assertEqual(session_state.page2_turns, turns)

    def test_closeup_image_prompt_hides_leading_dice_decision(self):
        ctx = page2_service.default_context()
        turns = [Page2ConversationTurn(assistant_message="本轮不掷骰子\n雨还在下。")]
        examples = (
            ("本轮不掷骰子。\n\n蓝衣少女站在窗边。", "蓝衣少女站在窗边。"),
            ("**本轮不掷骰子。**\n\n蓝衣少女站在窗边。", "蓝衣少女站在窗边。"),
            ("画面中的纸条写着本轮不掷骰子。", "画面中的纸条写着本轮不掷骰子。"),
        )
        for model_reply, expected in examples:
            with self.subTest(model_reply=model_reply):
                with (
                    patch.object(page2_service, "_send_with_current_chat_model", return_value=model_reply),
                ):
                    actual = page2_service.generate_image_prompt(
                        ctx=ctx, turns=turns, story_brain_enabled=False,
                        mode="closeup", subject="蓝衣少女",
                    )
                self.assertEqual(actual, expected)

    def test_image_prompt_modes_follow_current_chat_model(self):
        models = [
            ("grok1", "grok-4.3", "grok"),
            ("grok2", "grok-4.6", "grok"),
            ("deepseekPro", "deepseek-v4-pro", "deepseek"),
            ("deepseekFlash", "deepseek-v4-flash", "deepseek"),
            (
                "mistralLarge2512",
                "mistralai/mistral-large-2512",
                "openrouter",
            ),
        ]
        modes = [
            ("normal", "", "以第三人称视角描述一下当前场景"),
            ("first_person", "", "以我为第一人称视角"),
            ("closeup", "艾琳", "艾琳当前的人物特写"),
        ]
        for selected, api_model, provider in models:
            for mode, subject, prompt_marker in modes:
                for reasoning in (False, True):
                    with self.subTest(model=selected, mode=mode, reasoning=reasoning):
                        ctx = page2_service.default_context()
                        ctx.selected_chat_model = selected
                        ctx.system_prompt = "聊天专用系统设定"
                        ctx.temperature = 0.6
                        ctx.chat_reasoning_enabled = reasoning
                        with (
                            patch.object(page2_service.GrokAPIClient, "send_message") as grok,
                            patch.object(page2_service.DeepSeekAPIClient, "send_message") as deepseek,
                            patch.object(
                                page2_service.OpenRouterChatAPIClient,
                                "send_message",
                            ) as openrouter,
                        ):
                            clients = {
                                "grok": grok,
                                "deepseek": deepseek,
                                "openrouter": openrouter,
                            }
                            client = clients[provider]
                            client.return_value = "最终图片 prompt"
                            result = page2_service.generate_image_prompt(
                                ctx=ctx,
                                turns=[Page2ConversationTurn(
                                    user_message="观察房间",
                                    assistant_message="最新助手回复",
                                )],
                                story_brain_short="旅店停电",
                                story_brain_enabled=True,
                                mode=mode,
                                subject=subject,
                            )

                        self.assertEqual(result, "最终图片 prompt")
                        for name, other_client in clients.items():
                            if name != provider:
                                other_client.assert_not_called()
                        self.assertEqual(client.call_count, 1)
                        kwargs = client.call_args.kwargs
                        self.assertEqual(kwargs["model"], api_model)
                        self.assertEqual(kwargs["temperature"], 0.6)
                        if provider == "openrouter":
                            self.assertNotIn("thinking_enabled", kwargs)
                            self.assertNotIn("reasoning_effort", kwargs)
                        else:
                            self.assertEqual(
                                kwargs["thinking_enabled"],
                                reasoning or selected == "grok2",
                            )
                        self.assertIn(prompt_marker, kwargs["user_message"])
                        self.assertTrue(kwargs["user_message"].startswith(
                            render_internal_prompt(
                                "story_brain", "context_wrapper",
                                story_brain="旅店停电", user_message="",
                            ).strip()
                        ))
                        self.assertEqual(kwargs["context_messages"], [
                            {"role": "user", "content": "观察房间"},
                            {"role": "assistant", "content": "最新助手回复"},
                        ])
                        self.assertEqual(kwargs["system_prompt"], ctx.system_prompt)
                        self.assertEqual(ctx.system_prompt, "聊天专用系统设定")

    def test_image_command_turn_round_trips_and_is_excluded_from_context(self):
        from web.pages import page2

        image_turn = Page2ConversationTurn(
            user_message="/图片",
            assistant_message="场景描述",
            is_context_excluded=True,
        )
        restored = Page2ConversationTurn.from_dict(image_turn.to_dict())
        self.assertTrue(restored.is_context_excluded)

        normal_turns = [
            Page2ConversationTurn(user_message="用户1", assistant_message="助手1"),
            Page2ConversationTurn(user_message="用户2", assistant_message="助手2"),
        ]
        self.assertEqual(
            page2_service.build_context_messages(
                [normal_turns[0], restored, normal_turns[1]],
                2,
            ),
            [
                {"role": "user", "content": "用户1"},
                {"role": "assistant", "content": "助手1"},
                {"role": "user", "content": "用户2"},
                {"role": "assistant", "content": "助手2"},
            ],
        )
        self.assertEqual(
            [turn.id for turn in page2._completed_story_brain_turns(
                [normal_turns[0], restored, normal_turns[1]]
            )],
            [normal_turns[0].id, normal_turns[1].id],
        )
        self.assertEqual(
            page2._completed_turn_count(
                [normal_turns[0], restored, normal_turns[1]]
            ),
            2,
        )

    def test_shortcut_text_and_literal_slash_commands_use_normal_chat(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        defaults = json.loads((ROOT / "app/default_prompts/quick_inputs.json").read_text())
        self.assertEqual(defaults, [])
        for text in ["/图片", "/图片 追加内容"]:
            with self.subTest(text=text):
                ctx = page2_service.default_context()
                ctx.story_brain_turns = 1
                task = page2._Page2OperationTask(
                    kind="chat", label="发送消息", scope_id="test",
                    payload={"ctx": ctx, "user_text": text, "story_brain_enabled": True},
                )
                session = SessionState(page2_turns=[], page2_story_brain_short="")
                with (
                    patch.object(page2.st, "session_state", session),
                    patch.object(page2, "_upsert_record"),
                    patch.object(page2_service, "send_message", return_value="场景描述") as send,
                    patch.object(page2, "_schedule_story_brain_update") as memory,
                    patch.object(page2_service, "should_auto_roll_dice", return_value=False),
                ):
                    task.result = page2._prepare_chat_operation(task)()
                    page2._apply_chat_operation(task)
                turn = session.page2_turns[-1]
                self.assertEqual(send.call_args.kwargs["user_message"], text)
                self.assertFalse(turn.is_context_excluded)
                self.assertEqual(page2_service.build_context_messages([turn], 8), [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "场景描述"},
                ])
                memory.assert_called_once()

    def test_archived_long_story_brain_round_trips_unchanged(self):
        archive = {
            "characters": [{"id": "char-1", "name": "旧角色", "secret": "原样保留"}],
            "relationships": [{"from": "旧角色", "to": "另一角色"}],
            "events": [{"type": "伏笔", "trigger": "旧触发条件"}],
            "unknown_future_field": {"nested": [1, 2, 3]},
        }
        record = ChatRecord.from_dict(
            {
                "id": "archive-round-trip",
                "title": "旧记录",
                "turns": [{"user_message": "继续", "assistant_message": "好的"}],
                "system_prompt": "系统设定",
                "generated_images": [],
                "story_brain": archive,
                "story_brain_short": "短 Story Brain",
                "story_brain_next_update_turn": 12,
                "story_brain_last_update_turn_id": "turn-9",
                "final_setting_id": "final-setting-1",
            }
        )

        self.assertEqual(record.to_dict()["story_brain"], archive)

        saved_id = page2_service.upsert_chat_record(
            record_id=record.id,
            turns=record.turns,
            generated_media=record.generated_images,
            system_prompt=record.system_prompt,
            story_brain=record.story_brain,
            story_brain_short=record.story_brain_short,
            story_brain_next_update_turn=record.story_brain_next_update_turn,
            story_brain_last_update_turn_id=record.story_brain_last_update_turn_id,
            final_setting_id=record.final_setting_id,
        )
        loaded = chat_records.load_record_by_id(saved_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.story_brain, archive)
        self.assertEqual(loaded.story_brain_short, "短 Story Brain")
        self.assertEqual(loaded.story_brain_next_update_turn, 12)
        self.assertEqual(loaded.story_brain_last_update_turn_id, "turn-9")
        self.assertEqual(loaded.final_setting_id, "final-setting-1")

    def test_legacy_first_input_migrates_to_named_selected_record(self):
        record = SystemPromptRecord.from_dict(
            {
                "title": "旧最终设定",
                "prompt": "旧 prompt",
                "first_input": "旧开场",
            }
        )

        self.assertEqual(len(record.first_inputs), 2)
        self.assertEqual(record.first_inputs[0].title, "默认首轮输入")
        self.assertEqual(record.first_inputs[0].content, "旧开场")
        self.assertEqual(record.selected_first_input_id, record.first_inputs[0].id)
        self.assertEqual(record.to_dict()["first_input"], "旧开场")

    def test_first_inputs_can_be_created_selected_edited_and_deleted(self):
        record = SystemPromptRecord(
            title="最终设定",
            prompt="prompt",
            first_input="默认开场",
        )
        state = system_prompts.PromptState(
            hidden_space=False,
            records=[record],
            hidden_records=[],
            next_index=2,
            hidden_next_index=1,
            selected_record_id=record.id,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            with patch.object(system_prompts, "settings", store):
                created = system_prompts.create_first_input(state, record.id)
                self.assertEqual(created.content, "")
                self.assertEqual(record.selected_first_input_id, created.id)

                system_prompts.update_first_input(
                    state,
                    record.id,
                    created.id,
                    title="潜入开场",
                    content="从屋顶开始",
                )
                self.assertEqual(
                    system_prompts.first_input_for_record(state, record.id),
                    "从屋顶开始",
                )

                remaining_id = system_prompts.delete_first_input(
                    state,
                    record.id,
                    created.id,
                )
                self.assertEqual(len(record.first_inputs), 2)
                self.assertEqual(record.selected_first_input_id, remaining_id)
                self.assertEqual(
                    system_prompts.first_input_for_record(state, record.id),
                    "默认开场",
                )

    def test_new_long_term_memory_is_prefilled_with_current_story_brain_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            with patch.object(prompt_assets, "settings", store):
                seeded = prompt_assets.load_long_term_memories()
                prompt_assets.save_long_term_memory(
                    seeded[0].id,
                    "默认记忆",
                    "前端保存后的最新默认规则",
                )
                saved_default = prompt_assets.default_long_term_memory_record()
                created = prompt_assets.create_prefilled_long_term_memory()

        self.assertEqual([item.title for item in seeded], ["默认记忆"])
        self.assertEqual(saved_default.content, "前端保存后的最新默认规则")
        self.assertEqual(created.content, "前端保存后的最新默认规则")

    def test_legacy_final_setting_keeps_empty_memory_when_binding_default(self):
        legacy = SystemPromptRecord.from_dict(
            {"title": "旧最终设定", "prompt": "旧 prompt", "first_input": "开始"}
        )
        state = system_prompts.PromptState(
            hidden_space=False,
            records=[legacy],
            hidden_records=[],
            next_index=2,
            hidden_next_index=1,
            selected_record_id=legacy.id,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            with (
                patch.object(prompt_assets, "settings", store),
                patch.object(system_prompts, "settings", store),
            ):
                default_record = prompt_assets.load_long_term_memories()[0]
                prompt_assets.save_long_term_memory(
                    default_record.id,
                    "默认",
                    "最新迁移规则",
                )
                system_prompts._ensure_record_defaults(state)

        self.assertEqual(legacy.long_term_memory, "")
        self.assertEqual(legacy.final_long_term_memory_id, default_record.id)

    def test_story_brain_update_uses_final_setting_long_term_memory_prompt(self):
        ctx = page2_service.default_context()
        turns = [
            Page2ConversationTurn(user_message="查看门", assistant_message="门上有血迹")
        ]
        with patch.object(
            page2_service.DeepSeekAPIClient,
            "send_message",
            return_value="更新后的 Story Brain",
        ) as send_message:
            result = page2_service.generate_short_story_brain(
                ctx=ctx,
                turns=turns,
                story_brain_short="旧记忆",
                long_term_memory_prompt="这是一套自定义长期记忆规则",
            )

        self.assertEqual(result, "更新后的 Story Brain")
        self.assertEqual(
            send_message.call_args.kwargs["system_prompt"],
            "这是一套自定义长期记忆规则",
        )

    def test_story_brain_history_count_can_include_all_turns_since_last_update(self):
        ctx = page2_service.default_context()
        ctx.story_brain_turns = 2
        turns = [
            Page2ConversationTurn(
                user_message=f"新用户消息{i}",
                assistant_message=f"新助手回复{i}",
            )
            for i in range(1, 5)
        ]
        with patch.object(
            page2_service.DeepSeekAPIClient,
            "send_message",
            return_value="更新后的 Story Brain",
        ) as send_message:
            page2_service.generate_short_story_brain(
                ctx=ctx,
                turns=turns,
                story_brain_short="旧记忆",
                history_turn_count=4,
            )

        user_message = send_message.call_args.kwargs["user_message"]
        self.assertIn("新用户消息1", user_message)
        self.assertIn("新用户消息4", user_message)

    def test_story_brain_turns_since_last_success_use_dynamic_window(self):
        from web.pages import page2

        turns = [
            Page2ConversationTurn(user_message=f"用户{i}", assistant_message=f"助手{i}")
            for i in range(1, 7)
        ]

        dynamic = page2._story_brain_turns_since_last_update(
            turns,
            turns[1].id,
            legacy_fallback_turns=2,
        )
        legacy = page2._story_brain_turns_since_last_update(
            turns,
            "",
            legacy_fallback_turns=2,
        )
        no_new = page2._story_brain_turns_since_last_update(
            turns,
            turns[-1].id,
            legacy_fallback_turns=2,
        )

        self.assertEqual([turn.id for turn in dynamic], [turn.id for turn in turns[2:]])
        self.assertEqual([turn.id for turn in legacy], [turn.id for turn in turns[-2:]])
        self.assertEqual(no_new, [])

    def test_story_brain_update_reuses_latest_turn_when_there_are_no_new_turns(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        ctx = page2_service.default_context()
        turn = Page2ConversationTurn(
            user_message="已经处理的用户消息",
            assistant_message="已经处理的助手回复",
        )
        task = page2._Page2OperationTask(
            kind="story_brain",
            label="更新 Story Brain",
            payload={
                "ctx": ctx,
                "manual": True,
                "turn_id": turn.id,
                "reply": turn.assistant_message,
            },
            scope_id="test-scope",
        )
        session_state = SessionState(
            page2_turns=[turn],
            page2_story_brain_last_update_turn_id=turn.id,
            page2_story_brain_short="旧 Story Brain",
        )

        with patch.object(page2.st, "session_state", session_state):
            runner = page2._prepare_story_brain_operation(task)

        self.assertTrue(callable(runner))
        self.assertEqual(task.meta["processed_turn_count"], 1)
        self.assertEqual(task.meta["last_processed_turn_id"], turn.id)

    def test_one_click_sync_writes_long_term_memory_back_to_source(self):
        template = FixedTemplateRecord(
            title="模板",
            background_intro="旧背景",
            dm_core_tasks="旧任务",
            response_format="旧格式",
        )
        world = PromptTextRecord(title="世界", content="旧世界")
        user = PromptTextRecord(title="主角", content="主角内容")
        memory = PromptTextRecord(title="默认", content="旧记忆规则")
        prompt = "\n\n".join(
            [
                "1. 背景介绍\n新背景",
                "2. 世界书\n新世界",
                "3. 用户角色\n主角内容",
                "4. 其他角色\n无",
                "5. DM 的核心任务\n新任务",
                "6. 回复格式要求\n新格式",
            ]
        )
        final = SystemPromptRecord(
            title="最终设定",
            prompt=prompt,
            first_input="开始",
            long_term_memory="旧记忆规则",
            final_template_id=template.id,
            final_world_book_id=world.id,
            final_long_term_memory_id=memory.id,
            final_user_character_id=user.id,
            final_user_character_name=user.title,
        )
        state = system_prompts.PromptState(
            hidden_space=False,
            records=[final],
            hidden_records=[],
            next_index=2,
            hidden_next_index=1,
            selected_record_id=final.id,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            store.set(
                "fixedTemplateRecords",
                json.dumps([template.to_dict()], ensure_ascii=False),
            )
            store.set(
                "worldBookRecords",
                json.dumps([world.to_dict()], ensure_ascii=False),
            )
            store.set(
                "characterCardRecords",
                json.dumps([user.to_dict()], ensure_ascii=False),
            )
            store.set(
                "longTermMemoryRecords",
                json.dumps([memory.to_dict()], ensure_ascii=False),
            )
            store.set("longTermMemoryDefaultSeeded", True)
            store.set("defaultLongTermMemoryRecordID", memory.id)
            backup_path = Path(temp_dir) / "sync-backup.json"
            with (
                patch.object(prompt_assets, "settings", store),
                patch.object(
                    prompt_assets,
                    "FINAL_SETTING_SYNC_BACKUP_PATH",
                    backup_path,
                ),
            ):
                updated = prompt_assets.sync_final_setting(
                    state,
                    final,
                    title=final.title,
                    prompt=prompt,
                    long_term_memory="前端编辑后的记忆规则",
                    first_inputs=list(final.first_inputs),
                    selected_first_input_id=final.selected_first_input_id,
                )

            saved_memories = json.loads(store.get("longTermMemoryRecords", "[]"))

        self.assertIn('长期记忆“默认”', updated)
        self.assertEqual(saved_memories[0]["content"], "前端编辑后的记忆规则")

    def test_one_click_sync_writes_user_character_and_preserves_personality(self):
        user = PromptTextRecord(
            title="狼影2",
            content="\n\n".join(
                [
                    "## 1. 角色身份\n旧身份",
                    "## 3. 角色性格\n冷静但记仇",
                    "## 5. 角色能力\n-旧能力",
                ]
            ),
        )
        prompt = "\n\n".join(
            [
                "3. 用户角色",
                "## 1. 角色身份\n新身份",
                "## 5. 角色能力\n-读心：可以读取目标的真实想法",
            ]
        )
        final = SystemPromptRecord(
            title="学校2",
            prompt=prompt,
            final_user_character_id=user.id,
            final_user_character_name=user.title,
        )
        state = system_prompts.PromptState(
            hidden_space=False,
            records=[final],
            hidden_records=[],
            next_index=2,
            hidden_next_index=1,
            selected_record_id=final.id,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            store.set(
                "characterCardRecords",
                json.dumps([user.to_dict()], ensure_ascii=False),
            )
            store.set("longTermMemoryDefaultSeeded", True)
            with (
                patch.object(prompt_assets, "settings", store),
                patch.object(
                    prompt_assets,
                    "FINAL_SETTING_SYNC_BACKUP_PATH",
                    Path(temp_dir) / "sync-backup.json",
                ),
            ):
                updated = prompt_assets.sync_final_setting(
                    state,
                    final,
                    title=final.title,
                    prompt=prompt,
                    long_term_memory="",
                    first_inputs=list(final.first_inputs),
                    selected_first_input_id=final.selected_first_input_id,
                )

            saved_user = json.loads(store.get("characterCardRecords", "[]"))[0]

        self.assertIn('角色卡“狼影2”', updated)
        self.assertIn("新身份", saved_user["content"])
        self.assertIn("-读心：可以读取目标的真实想法", saved_user["content"])
        self.assertIn("## 3. 角色性格\n冷静但记仇", saved_user["content"])
        self.assertLess(
            saved_user["content"].index("## 3. 角色性格"),
            saved_user["content"].index("## 5. 角色能力"),
        )

    def test_one_click_sync_skips_missing_other_characters_and_syncs_the_rest(self):
        template = FixedTemplateRecord(
            title="模板",
            background_intro="旧背景",
            dm_core_tasks="旧任务",
            response_format="旧格式",
        )
        world = PromptTextRecord(title="世界", content="旧世界")
        user = PromptTextRecord(title="主角", content="主角内容")
        missing_heading_character = PromptTextRecord(title="甲", content="甲的旧内容")
        remaining_character = PromptTextRecord(title="乙", content="乙的旧内容")
        deleted_character = PromptTextRecord(title="丙", content="丙的旧内容")
        memory = PromptTextRecord(title="默认", content="旧记忆规则")
        prompt = "\n\n".join(
            [
                "1. 背景介绍\n新背景",
                "2. 世界书\n新世界",
                "3. 用户角色\n主角内容",
                "4. 其他角色\n### 乙\n乙与主角并肩行动\n\n### 丙\n丙的新内容",
                "5. DM 的核心任务\n新任务",
                "6. 回复格式要求\n新格式",
            ]
        )
        final = SystemPromptRecord(
            title="最终设定",
            prompt=prompt,
            long_term_memory="旧记忆规则",
            final_template_id=template.id,
            final_world_book_id=world.id,
            final_long_term_memory_id=memory.id,
            final_user_character_id=user.id,
            final_user_character_name=user.title,
            final_other_character_ids=[
                missing_heading_character.id,
                remaining_character.id,
                deleted_character.id,
            ],
            final_other_character_names=["甲", "乙", "丙"],
        )
        state = system_prompts.PromptState(
            hidden_space=False,
            records=[final],
            hidden_records=[],
            next_index=2,
            hidden_next_index=1,
            selected_record_id=final.id,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            store.set("fixedTemplateRecords", json.dumps([template.to_dict()], ensure_ascii=False))
            store.set("worldBookRecords", json.dumps([world.to_dict()], ensure_ascii=False))
            store.set(
                "characterCardRecords",
                json.dumps(
                    [
                        user.to_dict(),
                        missing_heading_character.to_dict(),
                        remaining_character.to_dict(),
                    ],
                    ensure_ascii=False,
                ),
            )
            store.set("longTermMemoryRecords", json.dumps([memory.to_dict()], ensure_ascii=False))
            store.set("longTermMemoryDefaultSeeded", True)
            store.set("defaultLongTermMemoryRecordID", memory.id)
            with (
                patch.object(prompt_assets, "settings", store),
                patch.object(
                    prompt_assets,
                    "FINAL_SETTING_SYNC_BACKUP_PATH",
                    Path(temp_dir) / "sync-backup.json",
                ),
            ):
                updated = prompt_assets.sync_final_setting(
                    state,
                    final,
                    title=final.title,
                    prompt=prompt,
                    long_term_memory="新记忆规则",
                    first_inputs=list(final.first_inputs),
                    selected_first_input_id=final.selected_first_input_id,
                )

            saved_characters = {
                item["title"]: item
                for item in json.loads(store.get("characterCardRecords", "[]"))
            }
            saved_worlds = json.loads(store.get("worldBookRecords", "[]"))

        self.assertEqual(saved_characters["甲"]["content"], "甲的旧内容")
        self.assertEqual(saved_characters["乙"]["content"], "乙与{用户角色名}并肩行动")
        self.assertEqual(saved_worlds[0]["content"], "新世界")
        self.assertIn('角色卡“乙”', updated)
        self.assertNotIn('角色卡“甲”', updated)
        self.assertNotIn('角色卡“丙”', updated)

    def test_one_click_sync_skips_missing_links_and_prompt_sections(self):
        template = FixedTemplateRecord(
            title="模板",
            background_intro="旧背景",
            dm_core_tasks="旧任务",
            response_format="旧格式",
        )
        memory = PromptTextRecord(title="默认", content="旧记忆规则")
        prompt = "\n\n".join(
            [
                "1. 背景介绍\n新背景",
                "3. 用户角色\n主角内容",
                "5. DM 的核心任务\n新任务",
                "6. 回复格式要求\n新格式",
            ]
        )
        final = SystemPromptRecord(
            title="最终设定",
            prompt=prompt,
            long_term_memory="旧记忆规则",
            final_template_id=template.id,
            final_long_term_memory_id=memory.id,
        )
        state = system_prompts.PromptState(
            hidden_space=False,
            records=[final],
            hidden_records=[],
            next_index=2,
            hidden_next_index=1,
            selected_record_id=final.id,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir) / "user_defaults.json")
            store.set("fixedTemplateRecords", json.dumps([template.to_dict()], ensure_ascii=False))
            store.set("longTermMemoryRecords", json.dumps([memory.to_dict()], ensure_ascii=False))
            store.set("longTermMemoryDefaultSeeded", True)
            store.set("defaultLongTermMemoryRecordID", memory.id)
            with (
                patch.object(prompt_assets, "settings", store),
                patch.object(
                    prompt_assets,
                    "FINAL_SETTING_SYNC_BACKUP_PATH",
                    Path(temp_dir) / "sync-backup.json",
                ),
            ):
                updated = prompt_assets.sync_final_setting(
                    state,
                    final,
                    title="改名后的最终设定",
                    prompt=prompt,
                    long_term_memory="新记忆规则",
                    first_inputs=list(final.first_inputs),
                    selected_first_input_id=final.selected_first_input_id,
                )

            saved_templates = json.loads(store.get("fixedTemplateRecords", "[]"))
            saved_finals = json.loads(store.get("systemPromptRecords", "[]"))

        self.assertEqual(saved_templates[0]["background_intro"], "新背景")
        self.assertEqual(saved_templates[0]["dm_core_tasks"], "新任务")
        self.assertEqual(saved_templates[0]["response_format"], "新格式")
        self.assertEqual(saved_finals[0]["title"], "改名后的最终设定")
        self.assertIn('固定模板“模板”', updated)
        self.assertIn('长期记忆“默认”', updated)

    def test_manual_story_brain_update_defers_next_update_by_five_turns(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        task = page2._Page2OperationTask(
            kind="story_brain",
            label="更新 Story Brain",
            payload={"manual": True},
            scope_id="test-scope",
        )
        task.result = "更新后的 Story Brain"
        task.meta = {
            "turn_id": "turn-6",
            "completed_turn_count": 6,
            "next_update_delay": 5,
            "processed_turn_count": 3,
            "last_processed_turn_id": "turn-6",
            "previous_short_story_brain": "旧 Story Brain",
        }
        session_state = SessionState(
            page2_story_brain_short="旧 Story Brain",
            page2_turns=[Page2ConversationTurn(id=f"turn-{i}", user_message="用户", assistant_message="回复") for i in range(1, 7)],
            page2_story_brain_short_editor_nonce=0,
            page2_story_brain_retry_pending=False,
            page2_story_brain_next_update_turn=None,
            page2_story_brain_last_update_turn_id="turn-3",
        )

        with (
            patch.object(page2.st, "session_state", session_state),
            patch.object(page2, "_upsert_record"),
        ):
            page2._apply_story_brain_update_task(task)

        self.assertEqual(session_state["page2_story_brain_next_update_turn"], 11)
        self.assertEqual(
            session_state["page2_story_brain_last_update_turn_id"],
            "turn-6",
        )
        self.assertIn(
            "下次将在第 11 轮更新",
            session_state["page2_story_brain_short_update_notice"],
        )
        self.assertIn(
            "本次读取了 3 轮新问答",
            session_state["page2_story_brain_short_update_notice"],
        )

    def test_failed_story_brain_update_does_not_advance_processed_turn(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        task = page2._Page2OperationTask(
            kind="story_brain",
            label="更新 Story Brain",
            scope_id="test-scope",
        )
        task.error = RuntimeError("更新失败")
        task.meta = {
            "turn_id": "turn-6",
            "last_processed_turn_id": "turn-6",
        }
        session_state = SessionState(
            page2_story_brain_retry_pending=False,
            page2_story_brain_last_update_turn_id="turn-3",
        )

        with patch.object(page2.st, "session_state", session_state):
            page2._apply_story_brain_update_task(task)

        self.assertEqual(
            session_state["page2_story_brain_last_update_turn_id"],
            "turn-3",
        )
        self.assertTrue(session_state["page2_story_brain_retry_pending"])

    def test_story_brain_next_update_turn_overrides_old_modulo_schedule(self):
        from web.pages import page2

        turns = [
            Page2ConversationTurn(user_message=f"用户{i}", assistant_message=f"助手{i}")
            for i in range(1, 12)
        ]
        self.assertFalse(
            page2._should_update_story_brain(turns[:6], 6, next_update_turn=11)
        )
        self.assertTrue(
            page2._should_update_story_brain(turns, 6, next_update_turn=11)
        )

    def test_story_brain_editor_is_three_times_taller_in_edit_mode(self):
        editor_html = (
            ROOT / "web/components/story_brain_text_editor/index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("min-height: 780px;", editor_html)

    def test_hidden_user_message_round_trips_and_stays_in_model_context(self):
        turn = Page2ConversationTurn(
            user_message="继续发展",
            assistant_message="故事继续",
            is_user_message_hidden=True,
        )

        restored = Page2ConversationTurn.from_dict(turn.to_dict())
        self.assertTrue(restored.is_user_message_hidden)
        self.assertEqual(
            page2_service.build_context_messages([restored], 1),
            [
                {"role": "user", "content": "继续发展"},
                {"role": "assistant", "content": "故事继续"},
            ],
        )

    def test_hidden_continue_message_is_not_used_as_record_title(self):
        turns = [
            Page2ConversationTurn(
                user_message="继续发展",
                assistant_message="雨幕中出现了一道门。",
                is_user_message_hidden=True,
            )
        ]

        self.assertEqual(
            page2_service.make_record_title(turns),
            "雨幕中出现了一道门。",
        )

    def test_auto_dice_roll_requires_exact_reply_ending(self):
        marker = internal_prompt("chat", "auto_roll_marker")
        self.assertTrue(page2_service.should_auto_roll_dice(marker))
        self.assertTrue(
            page2_service.should_auto_roll_dice(
                f"##【行动判定】\n0—12：失败\n13—24：成功\n{marker}\n\n"
            )
        )
        self.assertFalse(
            page2_service.should_auto_roll_dice(
                f"{marker}\n>当前时间：7月20日 17:25"
            )
        )
        self.assertFalse(
            page2_service.should_auto_roll_dice(f"正文提到{marker}随后继续。")
        )
        self.assertFalse(page2_service.should_auto_roll_dice("请求失败，请稍后重试。"))

    def test_dice_decision_marker_is_hidden_only_at_assistant_message_start(self):
        from web.pages import page2

        self.assertEqual(
            page2._assistant_message_for_display("本轮不掷骰子\n\n雨还在下。"),
            "雨还在下。",
        )
        self.assertEqual(
            page2._assistant_message_for_display("本轮掷骰子：\n##【行动判定】"),
            "##【行动判定】",
        )
        self.assertEqual(
            page2._assistant_message_for_display("正文提到本轮不掷骰子。"),
            "正文提到本轮不掷骰子。",
        )

    def test_formatted_dice_decision_marker_is_hidden(self):
        from web.pages import page2

        for opening in (
            "**本轮不掷骰子**",
            "**本轮不掷骰子。**",
            "__本轮不掷骰子__：",
            "*本轮掷骰子*",
            "`本轮掷骰子`",
            "\n  本轮不掷骰子。",
            "\ufeff\u200b**本轮不掷骰子**。",
        ):
            with self.subTest(opening=opening):
                self.assertEqual(
                    page2._assistant_message_for_display(opening + "\n\n**雨还在下。**"),
                    "**雨还在下。**",
                )

        narrative = "\n**雨还在下。**正文提到本轮不掷骰子。"
        self.assertEqual(page2._assistant_message_for_display(narrative), narrative)

    def test_completed_chat_queues_visible_auto_dice_followup(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        ctx = page2_service.default_context()
        turn = Page2ConversationTurn(
            user_message="尝试潜行",
            assistant_message=None,
            is_loading=True,
        )
        task = page2._Page2OperationTask(
            kind="chat",
            label="发送消息",
            scope_id="test-scope",
        )
        task.result = (
            "##【行动判定】\n0—12：失败\n13—24：成功\n"
            + internal_prompt("chat", "auto_roll_marker")
        )
        task.meta = {
            "turn_id": turn.id,
            "ctx": ctx,
            "story_brain_enabled": False,
        }
        session_state = SessionState(
            page2_turns=[turn],
            page2_operation_queue=[],
            page2_active_operation=task,
            page2_operation_scope_id="test-scope",
            page2_story_brain_retry_pending=False,
        )

        with (
            patch.object(page2.st, "session_state", session_state),
            patch.object(page2, "_upsert_record"),
            patch.object(page2.page2_service, "roll_point", return_value=17),
        ):
            page2._apply_chat_operation(task)

        self.assertEqual(turn.assistant_message, task.result)
        self.assertFalse(turn.is_loading)
        self.assertEqual(len(session_state["page2_operation_queue"]), 1)
        followup = session_state["page2_operation_queue"][0]
        self.assertEqual(followup.kind, "chat")
        self.assertEqual(followup.label, "自动掷骰并发送")
        self.assertEqual(followup.payload["user_text"], render_internal_prompt(
            "chat", "dice_result", point=17,
        ))
        self.assertFalse(followup.payload["hide_user_message"])
        self.assertTrue(followup.payload["is_auto_dice_followup"])

    def test_auto_dice_followup_does_not_recursively_roll(self):
        from web.pages import page2

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        ctx = page2_service.default_context()
        turn = Page2ConversationTurn(
            user_message=render_internal_prompt("chat", "dice_result", point=17),
            assistant_message=None,
            is_loading=True,
        )
        task = page2._Page2OperationTask(
            kind="chat",
            label="自动掷骰并发送",
            scope_id="test-scope",
        )
        task.result = "结算后又出现判定。\n" + internal_prompt("chat", "auto_roll_marker")
        task.meta = {
            "turn_id": turn.id,
            "ctx": ctx,
            "story_brain_enabled": False,
            "is_auto_dice_followup": True,
        }
        session_state = SessionState(
            page2_turns=[turn],
            page2_operation_queue=[],
            page2_active_operation=task,
            page2_operation_scope_id="test-scope",
            page2_story_brain_retry_pending=False,
        )

        with (
            patch.object(page2.st, "session_state", session_state),
            patch.object(page2, "_upsert_record"),
        ):
            page2._apply_chat_operation(task)

        self.assertEqual(session_state["page2_operation_queue"], [])


if __name__ == "__main__":
    unittest.main()
