import io
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from app.api import speech_client as client
from app.services.speech_generation import (
    ACTIVE, SpeechJobs, extract_quoted_lines, owner_key, split_text,
)
from app.models import Page2ConversationTurn


def wav_bytes(frames=2400):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * frames)
    return output.getvalue()


def settled(jobs, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = jobs.get(job_id, "owner")
        if job["status"] not in ACTIVE:
            return job
        time.sleep(0.01)
    raise AssertionError("speech task did not finish")


class SpeechPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.voice = next(iter(client.VOICES))

    def test_split_preserves_text_and_reference_boundaries(self):
        self.assertEqual(split_text("一行\r\n二行\n三行"), ["一行\r\n", "二行\n", "三行"])
        text = "a" * 1200 + "." + "b" * 1400
        chunks = split_text(text)
        self.assertEqual(len(chunks[0]), 1201)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 1300 for chunk in chunks))
        self.assertEqual(split_text(""), [])
        self.assertEqual(split_text("hello. small text"), ["hello. small text"])

    def test_extract_only_paired_quotes_in_source_order(self):
        self.assertEqual(
            extract_quoted_lines('旁白“ 第一条 ”，又说"second"，空的“ ”，未结束“忽略'),
            ["第一条", "second"],
        )

    def test_quote_batch_uses_separate_files_and_per_quote_settings(self):
        other_voice = list(client.VOICES)[1]
        synth = Mock(return_value=wav_bytes())
        jobs = SpeechJobs(self.root / "jobs", self.root / "ChatSpeech", synth)
        job_id = jobs.start_quotes("owner", [
            {"quote": "别过来！", "emotion": "[angry]", "voice": self.voice},
            {"quote": "没事了。", "emotion": "", "voice": other_voice},
        ], "test-key")
        done = settled(jobs, job_id)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["total"], 2)
        self.assertEqual(synth.call_count, 2)
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in synth.call_args_list],
            [("[angry]别过来！", self.voice), ("没事了。", other_voice)],
        )
        self.assertEqual(len(list(jobs.output.glob("*.wav"))), 2)
        for clip in done["clips"]:
            client.validate_wav((jobs.output / clip["filename"]).read_bytes())
        turn = Page2ConversationTurn(assistant_message="原文", assistant_audio_clips=done["clips"])
        self.assertEqual(Page2ConversationTurn.from_dict(turn.to_dict()).assistant_audio_clips,
                         done["clips"])

    def test_quote_batch_resume_reuses_completed_audio(self):
        synth = Mock(side_effect=[wav_bytes(), client.SpeechError("临时失败")])
        jobs = SpeechJobs(self.root / "jobs", self.root / "ChatSpeech", synth)
        clips = [{"quote": word, "emotion": "", "voice": self.voice}
                 for word in ("第一句", "第二句")]
        job_id = jobs.start_quotes("owner", clips, "test-key")
        self.assertEqual(settled(jobs, job_id)["status"], "failed")
        synth.side_effect = [wav_bytes()]
        jobs.retry(job_id, "owner", "test-key")
        self.assertEqual(settled(jobs, job_id)["status"], "completed")
        self.assertEqual(synth.call_count, 3)
        self.assertEqual(synth.call_args.args[0], "第二句")

    def test_failed_chunk_retries_without_regenerating_success_and_merges_pause(self):
        synth = Mock(side_effect=[wav_bytes(), client.SpeechError("临时失败")])
        jobs = SpeechJobs(self.root / "jobs", self.root / "故事", synth)
        job_id = jobs.start("owner", "第一段\n第二段", self.voice, "test-key")
        failed = settled(jobs, job_id)
        self.assertEqual((failed["status"], failed["completed"]), ("failed", 1))
        self.assertNotIn("test-key", (self.root / "jobs" / job_id / "job.json").read_text())
        synth.side_effect = [wav_bytes()]
        jobs.retry(job_id, "owner", "new-test-key")
        done = settled(jobs, job_id)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(synth.call_count, 3)
        self.assertEqual(synth.call_args.args[0], "第二段")
        with wave.open(str(jobs.output / done["filename"]), "rb") as wav:
            self.assertEqual(wav.getnframes(), 2400 * 2 + 14400)
            frames = wav.readframes(wav.getnframes())
            self.assertEqual(frames[4800:4800 + 28800], b"\0" * 28800)
        self.assertFalse((self.root / "jobs" / job_id / "chunks").exists())

    def test_cancel_duplicate_protection_and_resume(self):
        entered, release = threading.Event(), threading.Event()
        def synth(*args, **kwargs):
            entered.set()
            release.wait(3)
            return wav_bytes()
        jobs = SpeechJobs(self.root / "jobs", self.root / "故事", synth)
        job_id = jobs.start("owner", "一\n二", self.voice, "test-key")
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(client.SpeechError, "正在运行"):
                jobs.start("owner", "重复", self.voice, "test-key")
            jobs.cancel(job_id, "owner")
        finally:
            release.set()
        cancelled = settled(jobs, job_id)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["completed"], 1)
        self.assertFalse(list(jobs.output.glob("*.wav")))
        jobs.retry(job_id, "owner", "test-key")
        self.assertEqual(settled(jobs, job_id)["status"], "completed")

    def test_restart_recovers_interrupted_task_and_rejects_other_owner(self):
        synth = Mock(side_effect=client.SpeechError("失败"))
        jobs = SpeechJobs(self.root / "jobs", self.root / "故事", synth)
        job_id = jobs.start("owner", "原文", self.voice, "test-key")
        settled(jobs, job_id)
        path = self.root / "jobs" / job_id / "job.json"
        job = json.loads(path.read_text())
        job["status"] = "running"
        path.write_text(json.dumps(job))
        restarted = SpeechJobs(jobs.root, jobs.output, Mock(return_value=wav_bytes()))
        self.assertEqual(restarted.latest("owner")["status"], "interrupted")
        self.assertIsNone(restarted.latest("someone-else"))
        with self.assertRaises(client.SpeechError):
            restarted.retry(job_id, "someone-else", "test-key")
        restarted.retry(job_id, "owner", "test-key")
        self.assertEqual(settled(restarted, job_id)["status"], "completed")

    def test_http_payload_fixed_flash_no_language_and_key_not_forwarded(self):
        response = Mock(status_code=200, json=Mock(return_value={
            "output": {"audio": {"url": "https://audio.example.com/result.wav"}},
        }))
        download = Mock(status_code=200, iter_content=Mock(return_value=[wav_bytes()]))
        progress = Mock()
        session = client.requests.Session()
        with patch.object(client.requests, "Session", return_value=session), \
                patch.object(session, "request", side_effect=[response, download]) as request:
            audio = client.synthesize("原文", self.voice, "test-key", threading.Event(),
                                      on_progress=progress)
        self.assertFalse(session.trust_env)
        client.validate_wav(audio)
        post, get = request.call_args_list
        self.assertEqual(post.kwargs["json"], {"model": client.MODEL, "input": {
            "text": "原文", "voice": self.voice, "format": "wav", "sample_rate": 24000, "rate": 1.0,
        }})
        self.assertNotIn("headers", get.kwargs)
        self.assertNotIn("Authorization", session.headers)
        self.assertEqual([call.args[0] for call in progress.call_args_list],
                         ["synthesizing", "downloading", "downloading"])
        response.close.assert_called_once()
        download.close.assert_called_once()

    def test_interrupted_download_retries_without_resynthesizing(self):
        response = Mock(status_code=200, json=Mock(return_value={
            "output": {"audio": {"url": "https://audio.example.com/result.wav"}},
        }))
        def broken_stream(*args):
            yield b"partial audio"
            raise client.requests.ConnectionError("interrupted")
        broken = Mock(status_code=200, iter_content=broken_stream)
        download = Mock(status_code=200, iter_content=Mock(return_value=[wav_bytes()]))
        progress = Mock()
        session = client.requests.Session()
        with patch.object(client.requests, "Session", return_value=session), \
                patch.object(session, "request", side_effect=[response, broken, download]) as request:
            audio = client.synthesize("原文", self.voice, "test-key", threading.Event(),
                                      on_progress=progress)
        client.validate_wav(audio)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["POST", "GET", "GET"])
        self.assertIn(("downloading", 2, 0), [call.args for call in progress.call_args_list])
        broken.close.assert_called_once()
        download.close.assert_called_once()

    def test_cancel_during_download_does_not_retry(self):
        cancel = threading.Event()
        def cancelled_stream(*args):
            cancel.set()
            yield b"partial"
        download = Mock(status_code=200, iter_content=cancelled_stream)
        session = Mock()
        session.request.return_value = download
        with self.assertRaises(client.SpeechCancelled):
            client._request(session, "GET", "https://audio.example.com/result.wav", cancel,
                            phase="downloading", read_audio=True, stream=True)
        session.request.assert_called_once()
        download.close.assert_called_once()

    def test_stage_is_saved_while_first_chunk_is_in_flight(self):
        entered, release = threading.Event(), threading.Event()
        def synth(*args, on_progress):
            on_progress("downloading", 2, 65536)
            entered.set()
            release.wait(3)
            return wav_bytes()
        jobs = SpeechJobs(self.root / "jobs", self.root / "故事", synth)
        job_id = jobs.start("owner", "原文", self.voice, "test-key")
        try:
            self.assertTrue(entered.wait(2))
            job = jobs.get(job_id, "owner")
            self.assertEqual(job["completed"], 0)
            self.assertEqual((job["phase"], job["attempt"], job["downloaded_bytes"]),
                             ("downloading", 2, 65536))
            self.assertTrue(job["started_at"])
            self.assertTrue(job["phase_started_at"])
        finally:
            release.set()
        self.assertEqual(settled(jobs, job_id)["status"], "completed")

    def test_bad_audio_and_credentials_fail_before_publication(self):
        with self.assertRaises(client.SpeechError):
            client.validate_wav(b"not a wav")
        synth = Mock(return_value=b"not a wav")
        jobs = SpeechJobs(self.root / "jobs", self.root / "故事", synth)
        with self.assertRaises(client.SpeechError):
            jobs.start("owner", "原文", self.voice, "")
        synth.assert_not_called()
        job_id = jobs.start("owner", "原文", self.voice, "test-key")
        self.assertEqual(settled(jobs, job_id)["status"], "failed")
        self.assertFalse(list(jobs.output.glob("*.wav")))


class SpeechPageTests(unittest.TestCase):
    def test_chat_quote_dialog_collects_selected_voice_and_emotion(self):
        source = '''
import streamlit as st
from unittest.mock import patch
from app.models import Page2ConversationTurn
from web.pages import page2
st.session_state.setdefault("page2_turns", [
    Page2ConversationTurn(id="turn", assistant_message="旁白“你好！”")
])
st.session_state.setdefault("page2_tts_dialog_turn_id", "turn")
st.session_state.setdefault("page2_tts_dialog_nonce", "test")
def fake_start(turn, clips):
    st.session_state["generated_clips"] = clips
    return True
with patch.object(page2, "_start_turn_tts", side_effect=fake_start):
    page2._render_tts_quote_dialog(next(iter(page2.VOICES)))
'''
        app = AppTest.from_string(source).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(app.button[1].disabled)
        app.button(key="page2_tts_select_test_0").click().run()
        self.assertFalse(app.button[1].disabled)
        voice = list(client.VOICES)[1]
        app.selectbox(key="page2_tts_voice_test_0").set_value(voice).run()
        app.selectbox(key="page2_tts_emotion_test_0").set_value("[angry]").run()
        self.assertFalse(list(app.exception))
        app.button[1].click().run()
        self.assertEqual(app.session_state["generated_clips"], [
            {"quote": "你好！", "voice": voice, "emotion": "[angry]"},
        ])

    def test_page_start_cancel_resume_and_audio_result(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "result.wav").write_bytes(wav_bytes())
            source = '''
import streamlit as st
from pathlib import Path
from unittest.mock import Mock, patch
from web.pages import speech
st.session_state.auth_ok = True
st.session_state.auth_mode = 'user'
st.session_state.user_id = 'test-user'
if 'fake_jobs' not in st.session_state:
    jobs = Mock(output=Path(TEMP))
    job = {'id': 'a' * 32, 'voice': next(iter(speech.VOICES)), 'created_at': '2026-09-16T12:00:00',
           'completed': 0, 'total': 2, 'status': 'running', 'filename': 'result.wav', 'error': '',
           'phase': 'downloading', 'attempt': 2, 'downloaded_bytes': 65536}
    jobs.latest.return_value = None
    def start(*args):
        jobs.latest.return_value = job
    def cancel(*args):
        job.update(status='cancelled', error='已取消')
    def retry(*args):
        job.update(status='completed', completed=2)
    jobs.start.side_effect = start
    jobs.cancel.side_effect = cancel
    jobs.retry.side_effect = retry
    st.session_state.fake_jobs = jobs
with patch.object(speech, 'speech_jobs', return_value=st.session_state.fake_jobs), patch.object(speech, '_api_key', return_value='test-key'):
    speech.render()
'''.replace("TEMP", repr(temp))
            app = AppTest.from_string(source).run()
            prefix = "speech_" + owner_key("test-user") + "_"
            app.text_area[0].input("第一段\n第二段").run()
            app.button(key=prefix + "start").click().run()
            self.assertTrue(app.button(key=prefix + "start").disabled)
            captions = " ".join(item.value for item in app.caption)
            self.assertIn("正在下载音频（正在重试，第 2 / 3 次尝试）", captions)
            self.assertIn("已下载 64 KB", captions)
            self.assertIn("当前步骤已等待", captions)
            app.button(key=prefix + "cancel").click().run()
            app.button(key=prefix + "retry").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.get("audio")), 1)
            self.assertEqual(len(app.get("download_button")), 1)
            self.assertIn("故事/result.wav", app.success[0].value)
            jobs = app.session_state.fake_jobs
            self.assertEqual(jobs.start.call_args.args[1], "第一段\n第二段")
            jobs.cancel.assert_called_once()
            jobs.retry.assert_called_once()

    def test_dashscope_key_saves_in_existing_settings_page(self):
        from app.config import SettingsStore
        from web.pages import model_settings
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "settings.json")
            source = '''
from web.pages import model_settings
model_settings.render()
'''
            # Callbacks run before the app script, so keep the patch active across runs.
            with patch.object(model_settings, "settings", SettingsStore(Path(path))):
                app = AppTest.from_string(source).run()
                app.text_input(key="model_settings_pending_dashscopeApiKey").input("test-dashscope-key").run()
                self.assertEqual(len(app.exception), 0)
            self.assertEqual(json.loads(Path(path).read_text())["dashscopeApiKey"], "test-dashscope-key")

    def test_form_drafts_survive_navigation_and_guest_cannot_access(self):
        with tempfile.TemporaryDirectory() as temp:
            source = '''
import streamlit as st
from pathlib import Path
from unittest.mock import patch
from app.services.speech_generation import SpeechJobs
from web.pages import speech
st.session_state.auth_ok = True
if 'auth_mode' not in st.session_state:
    st.session_state.auth_mode = 'user'
st.session_state.user_id = 'test-user'
if st.button('切换页面', key='navigate'):
    st.session_state.away = not st.session_state.get('away', False)
if st.button('游客', key='guest'):
    st.session_state.auth_mode = 'guest'
jobs = SpeechJobs(Path(TEMP) / 'jobs', Path(TEMP) / 'output')
with patch.object(speech, 'speech_jobs', return_value=jobs), patch.object(speech, '_api_key', return_value='test-key'):
    if not st.session_state.get('away'):
        speech.render()
'''.replace("TEMP", repr(temp))
            app = AppTest.from_string(source).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.selectbox), 2)
            self.assertEqual(len(app.selectbox[1].options), 14)
            app.text_area[0].input("故事第一段\n第二段").run()
            app.selectbox[0].select("英文").run()
            app.button(key="navigate").click().run()
            app.button(key="navigate").click().run()
            self.assertEqual(app.text_area[0].value, "故事第一段\n第二段")
            self.assertEqual(app.selectbox[0].value, "英文")
            app.button(key="guest").click().run()
            self.assertEqual(len(app.text_area), 0)
            self.assertEqual(len(app.exception), 0)


if __name__ == "__main__":
    unittest.main()
