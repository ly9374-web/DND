import json
from unittest.mock import Mock

from app import config, log_store


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
    files = list(tmp_path.glob("debug-*.jsonl"))
    assert len(files) == 1
    entries = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [entry["message"] for entry in entries] == ["new"]
    clock[0] += 10
    log_store.cleanup(tmp_path)
    assert not list(tmp_path.glob("debug-*.jsonl"))
    assert unrelated.read_text() == "leave me alone"


def test_base64_and_credentials_are_redacted_in_file_and_terminal(tmp_path, capsys, monkeypatch):
    encoded = "YWJj" * 2000
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "settings", Mock(load=Mock(), bool=Mock(return_value=True)))
    monkeypatch.setattr(log_store, "start_maintenance", Mock())
    config.debug_log({"image": f"data:image/png;base64,{encoded}", "Authorization": "Bearer key"})
    config.debug_log(json.dumps({"b64_json": encoded, "api_key": "secret-key"}))
    config.debug_log("https://example.com/image?signature=secret-signature")
    contents = "".join(p.read_text() for p in (tmp_path / "logs").glob("debug-*.jsonl"))
    terminal = capsys.readouterr().out
    for private in (encoded, "secret-key", "secret-signature", "Bearer key"):
        assert private not in contents
        assert private not in terminal
    assert len(contents) < 2000
    assert "[已隐藏]" in terminal


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
    assert not list(tmp_path.glob("debug-*.jsonl"))
    thread.start.assert_called_once()
