"""Persistence compatibility for immersive background selection."""
from unittest.mock import patch

from app import storage
from app.config import SettingsStore
from app.models import ChatRecord, GeneratedImageRecord, Page2ConversationTurn
from app.services import chat_records, page2_service
from web.pages import page2


class Session(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def test_legacy_and_explicit_empty_background_are_distinct():
    assert ChatRecord.from_dict({}).background_media_id is None
    for selection, url, visible in [("", "", True), ("image-a", "", False),
                                     ("video-b", "", True), ("", "https://example.com/a.png", True)]:
        record = ChatRecord(title="test", turns=[], system_prompt="", background_media_id=selection, background_url=url, background_visible=visible)
        restored = ChatRecord.from_dict(record.to_dict())
        assert (restored.background_media_id, restored.background_url, restored.background_visible) == (selection, url, visible)


def test_background_selection_survives_real_record_save_and_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "settings", SettingsStore(tmp_path / "settings.json"))
    monkeypatch.setattr(storage, "CHAT_RECORDS_DIR", tmp_path / "chats")
    turns = [Page2ConversationTurn(user_message="继续", assistant_message="海风吹过。")]
    record_id = page2_service.upsert_chat_record(record_id=None, turns=turns, generated_media=[],
        system_prompt="", story_brain={}, background_media_id="", background_visible=False)
    restored = chat_records.load_record_by_id(record_id)
    assert restored.background_media_id == "" and not restored.background_visible
    branch = chat_records.create_branch(record_id)
    assert branch.background_media_id == "" and not branch.background_visible
    page2_service.upsert_chat_record(record_id=record_id, turns=turns, generated_media=[],
        system_prompt="", story_brain={}, background_media_id="video-1", background_visible=True)
    restored = chat_records.load_record_by_id(record_id)
    assert restored.background_media_id == "video-1" and restored.background_visible


def test_selecting_grid_clears_url_and_is_persisted():
    state = Session(page2_record_id="record-a", page2_media_selection_nonce=0,
                    page2_media_record_select="", page2_selected_media_id="old", page2_background_visible=True)
    with patch.object(page2.st, "session_state", state), patch.object(page2, "_upsert_record") as save, \
         patch.object(page2.url_favorites, "clear_preview_url") as clear, \
         patch.object(page2, "_find_video_source", return_value=None):
        page2._activate_media_selection()
    assert state.page2_selected_media_id == "" and not state.page2_background_visible
    clear.assert_called_once_with("page2")
    save.assert_called_once()


def test_background_payload_serves_local_cache_without_exposing_path(tmp_path):
    from web.components.immersive_workspace import background_source
    record = GeneratedImageRecord(id="media-a", provider="test", prompt="", media_managed=True)
    path = tmp_path / "image.webp"
    path.write_bytes(b"local image")
    from types import SimpleNamespace
    from unittest.mock import Mock
    manager = Mock()
    manager.add.return_value = "/media/local.webp"
    with patch("web.components.immersive_workspace.media_store.local_path", return_value=path), \
         patch("web.components.immersive_workspace.runtime.exists", return_value=True), \
         patch("web.components.immersive_workspace.runtime.get_instance", return_value=SimpleNamespace(media_file_mgr=manager)):
        source = background_source(record, "", True)
    assert source == {"id": "media-a", "kind": "image", "src": "/media/local.webp"}
    assert str(tmp_path) not in str(source)
    assert background_source(record, "", False)["kind"] == "none"


def test_only_the_active_speech_turn_mounts_a_polling_fragment():
    turn = Page2ConversationTurn(id="turn-a", assistant_message="文字")
    for active, polls in [(None, False), ({"turn_id": "turn-b"}, False),
                          ({"turn_id": "turn-a"}, True)]:
        with patch.object(page2.st, "session_state", Session(page2_tts_active=active)), \
             patch.object(page2, "_poll_turn_tts_status") as poll, \
             patch.object(page2, "_render_turn_tts_status") as static:
            page2._render_turn_audio_region(turn)
        assert poll.call_count == int(polls)
        assert static.call_count == int(not polls)


def test_history_loads_six_at_a_time_without_changing_the_conversation():
    turns = [Page2ConversationTurn(id=str(i), user_message=str(i)) for i in range(20)]
    state = Session(page2_turns=turns, page2_operation_scope_id="record-a")
    with patch.object(page2.st, "session_state", state):
        assert [turn.id for turn in page2._history_window()[1]] == [str(i) for i in range(14, 20)]
        for expected_start in (8, 2, 0, 0):
            page2._load_earlier_chat_turns()
            start, visible = page2._history_window()
            assert start == expected_start
            assert visible == turns[start:]
        assert state.page2_turns is turns
        state.page2_operation_scope_id = "record-b"
        assert page2._history_window()[0] == 14


def test_recent_history_stays_bounded_and_expanded_history_keeps_its_anchor():
    turns = [Page2ConversationTurn(id=str(i)) for i in range(15)]
    state = Session(page2_turns=turns, page2_operation_scope_id="record-a")
    with patch.object(page2.st, "session_state", state):
        assert page2._history_window()[0] == 9
        turns.append(Page2ConversationTurn(id="new"))
        assert page2._history_window()[0] == 10
        page2._load_earlier_chat_turns()
        assert page2._history_window()[0] == 4
        turns.append(Page2ConversationTurn(id="newer"))
        assert page2._history_window()[0] == 4
        turns.pop()
        assert page2._history_window()[0] == 4
        state.page2_turns = []
        assert page2._history_window() == (0, [])


def test_deferred_sections_are_isolated_between_records():
    state = Session(page2_operation_scope_id="record-a", page2_workspace_lazy=True)
    with patch.object(page2.st, "session_state", state):
        assert not page2._workspace_section_mounted("media")
        page2._mount_workspace_section("media")
        assert page2._workspace_section_mounted("media")
        assert not page2._workspace_section_mounted("brain_editor")
        state.page2_operation_scope_id = "record-b"
        assert not page2._workspace_section_mounted("media")


def test_audio_toggle_has_one_initial_value_source():
    from streamlit.testing.v1 import AppTest
    from streamlit.elements.lib import policies
    source = '''
from unittest.mock import patch
from web.pages import page2
from app.config import SettingsStore
from pathlib import Path
import tempfile
store = SettingsStore(Path(tempfile.gettempdir()) / "unused-dnd-widget-settings.json")
store.values = {page2.AppStorageKeys.PAGE2_VIDEO_AUDIO_BY_MODEL: {page2.WAN3_MODEL: False}}
store.set = lambda key, value: store.values.__setitem__(key, value)
with patch.object(page2, "settings", store):
    page2._render_video_duration_controls(page2.WAN3_MODEL)
'''
    with patch.object(policies, "_shown_default_value_warning", False), \
         patch.object(policies._LOGGER, "warning") as warning:
        app = AppTest.from_string(source).run()
    assert not app.exception
    assert app.toggle(key="page2_video_generate_audio").value is False
    assert not warning.called
