import json
from types import SimpleNamespace
from unittest.mock import Mock

from app import config, log_store
from app.models import Page2ConversationTurn
from app.services import page2_service


def test_logs_expire_per_entry_without_touching_unrelated_files(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(log_store.time, "time", lambda: clock[0])
    log_store.write(tmp_path, "old")
    clock[0] += 10
    log_store.write(tmp_path, "new")
    unrelated = tmp_path / "user-notes.jsonl"
    unrelated.write_text("leave me alone")
    clock[0] = 1_800_000_000 + log_store.RETENTION_SECONDS
    log_store.cleanup(tmp_path)
    files = list(tmp_path.glob("chat-*.jsonl"))
    assert len(files) == 1
    entries = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [entry["message"] for entry in entries] == ["new"]
    clock[0] += 10
    log_store.cleanup(tmp_path)
    assert not list(tmp_path.glob("chat-*.jsonl"))
    assert unrelated.read_text() == "leave me alone"


def test_base64_and_credentials_are_redacted_in_file_and_terminal(tmp_path, capsys, monkeypatch):
    encoded = "YWJj" * 2000
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "settings", Mock(load=Mock(), bool=Mock(return_value=True)))
    monkeypatch.setattr(log_store, "start_maintenance", Mock())
    config.debug_log({"image": f"data:image/png;base64,{encoded}", "Authorization": "Bearer key"}, category="chat")
    config.chat_log("用户", json.dumps({"b64_json": encoded, "api_key": "secret-key"}), model="test")
    config.chat_log("助手", "https://example.com/image?signature=secret-signature", model="test")
    contents = "".join(p.read_text() for p in (tmp_path / "logs").glob("chat-*.jsonl"))
    terminal = capsys.readouterr().out
    for private in (encoded, "secret-key", "secret-signature", "Bearer key"):
        assert private not in contents
        assert private not in terminal
    assert len(contents) < 2000
    assert "[已隐藏]" in terminal


def test_only_explicit_chat_events_reach_terminal_and_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "settings", Mock(load=Mock(), bool=Mock(return_value=True)))
    monkeypatch.setattr(log_store, "start_maintenance", Mock())
    config.debug_log("IMAGE REQUEST body should stay out")
    config.chat_log("用户", "你好", model="deepseekFlash")
    config.chat_log("助手", "你好！", model="deepseekFlash", elapsed_seconds=1.2)
    terminal = capsys.readouterr().out
    entries = [json.loads(line)["message"] for path in (tmp_path / "logs").glob("*.jsonl")
               for line in path.read_text().splitlines()]
    assert len(entries) == 1
    assert "IMAGE REQUEST" not in terminal + "".join(entries)
    assert "用户：\"你好\"" in entries[0]
    assert "你好！" not in terminal + "".join(entries)


def test_chat_and_image_prompt_use_final_assembled_model_input(monkeypatch):
    requests = []
    monkeypatch.setattr(page2_service, "_send_with_current_chat_model",
                        lambda **kwargs: requests.append(kwargs) or "模型回复")
    ctx = SimpleNamespace(context_turn_count=0, unexpected_event_enabled=False,
                          selected_chat_model="deepseekFlash")
    assert page2_service.send_message(ctx=ctx, turns=[], user_message="你好",
                                      story_brain_enabled=False) == "模型回复"
    assert requests[0]["user_message"] == "你好"
    assert requests[0]["context_messages"] == []

    turn = Page2ConversationTurn(assistant_message="故事正文")
    image_prompt = page2_service.generate_image_prompt(
        ctx=ctx, turns=[turn], story_brain_enabled=False)
    assert image_prompt == "模型回复"
    assert requests[1]["log_label"] == page2_service.IMAGE_PROMPT_LOG_LABEL
    assert requests[1]["user_message"] == page2_service.build_image_prompt_system_prompt("first_person", "")


def test_disabled_logging_stops_new_entries_but_does_not_clear_existing(tmp_path, monkeypatch):
    log_store.write(tmp_path / "logs", "existing")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "settings", Mock(bool=Mock(return_value=False)))
    config.debug_log("should not appear")
    contents = "".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert "existing" in contents and "should not appear" not in contents


def test_startup_cleans_expired_logs_even_when_debug_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(log_store.time, "time", lambda: 1000)
    log_store.write(tmp_path, "old")
    monkeypatch.setattr(log_store.time, "time", lambda: 1000 + log_store.RETENTION_SECONDS + 61)
    thread = Mock()
    monkeypatch.setattr(log_store.threading, "Thread", Mock(return_value=thread))
    log_store.start_maintenance(tmp_path)
    log_store.start_maintenance(tmp_path)
    assert not list(tmp_path.glob("chat-*.jsonl"))
    thread.start.assert_called_once()


def test_cleanup_also_removes_expired_legacy_debug_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(log_store.time, "time", lambda: log_store.RETENTION_SECONDS + 120)
    legacy = tmp_path / "debug-0-abcdef123456.jsonl"
    legacy.write_text('{"time": 0, "message": "旧生图日志"}\n')
    log_store.cleanup(tmp_path)
    assert not legacy.exists()
