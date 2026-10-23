import os
import json
import threading
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import QApplication

import gui
from gpt_sovits import SpeechSettings
from llm import LlmSettings
from interaction import InteractionState, likely_playback_echo, speech_chunks
from wake import ConversationGate


class FakeCapture:
    def __init__(self):
        self.stop_requested = threading.Event()
        self.paused = False
        self.submitted = []

    def submit(self, samples):
        self.submitted.append(samples)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def request_stop(self):
        self.stop_requested.set()


class FakeStream:
    def __init__(self):
        self.stops = 0
        self.starts = 0

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1

    def close(self):
        pass


class FakeChatWorker(QObject):
    replied = Signal(str)
    failed = Signal(str)
    memory_warning = Signal(str)
    finished = Signal()
    messages = []

    def __init__(self, _settings, _key, _history, message, _parent, **_kwargs):
        super().__init__()
        self.settings = _settings
        self.message = message
        self.messages.append(message)

    def start(self):
        self.replied.emit("你好，我在这里。")
        self.finished.emit()


class DeferredChatWorker(QObject):
    replied = Signal(str)
    failed = Signal(str)
    memory_warning = Signal(str)
    finished = Signal()
    instances = []

    def __init__(self, _settings, _key, _history, message, _parent, **_kwargs):
        super().__init__()
        self.settings = _settings
        self.message = message
        self.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return False


class FakeTts:
    def __init__(self):
        self.spoken = []
        self.stops = 0

    def say(self, text):
        self.spoken.append(text)

    def state(self):
        return QTextToSpeech.State.Speaking

    def stop(self):
        self.stops += 1


class FakeSpeechReply(QObject):
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.aborted = False
        self.status = 200
        self.body = b"RIFFmock-audio"

    def attribute(self, _attribute):
        return self.status

    def readAll(self):
        return self.body

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def abort(self):
        self.aborted = True


class FakeSpeechNetwork:
    def __init__(self):
        self.requests = []
        self.transfer_timeouts = []

    def post(self, request, payload):
        import json
        reply = FakeSpeechReply()
        self.transfer_timeouts.append(request.transferTimeout())
        self.requests.append((json.loads(bytes(payload))["text"], reply))
        return reply


class VoiceLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Voice-loop tests must not use the owner's local Mem0/API setting.
        self.settings_patch = patch.object(gui, "load_settings", return_value=LlmSettings(memory_enabled=False))
        self.settings_patch.start()
        self.owner_patch = patch.object(gui, "load_link", return_value=None)
        self.owner_patch.start()
        self.speech_patch = patch.object(gui, "load_speech_settings", return_value=SpeechSettings())
        self.speech_patch.start()

    def tearDown(self):
        self.speech_patch.stop()
        self.owner_patch.stop()
        self.settings_patch.stop()

    def test_one_shot_voice_skips_model_and_disarms_after_trigger(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            path = Path(directory) / "preview.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0" * 320)
            path.with_suffix(".txt").write_text("固定台词", encoding="utf-8")
            path.with_suffix(".segments.json").write_text(json.dumps({"segments": [
                {"duration_ms": 1000, "pause_after_ms": 200},
                {"duration_ms": 1000, "pause_after_ms": 0},
            ]}), encoding="utf-8")
            window = gui.MainWindow()
            window.one_shot_audio_path = path
            window._update_one_shot_audio_label()
            window.one_shot_trigger.setText("今晚回家感觉如何")
            initial_status = window.status_label.text()
            window.one_shot_enabled.setChecked(True)
            self.assertEqual(window.status_label.text(), initial_status)
            window.hide_on_close = True
            window.show()
            window.close()
            self.assertFalse(window.isVisible())
            self.assertTrue(window.one_shot_enabled.isChecked())
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            try:
                with patch.object(window, "begin_chat_turn") as chat, \
                     patch.object(window, "_play_custom_audio") as play, \
                     patch.object(gui.QTimer, "singleShot") as schedule:
                    window.continuous_result("庄方宜，今晚回家感觉如何？", "zh")
                    self.assertEqual(window.interaction_state, InteractionState.THINKING)
                    self.assertEqual(play.call_count, 0)
                    self.assertEqual([call.args[0] for call in schedule.call_args_list],
                                     [1200, 9000, 10500, 12500])
                    turn = window.active_turn
                    window._one_shot_waiting(turn)
                    self.assertIn("等待模型回复", window.status_label.text())
                    window._one_shot_thinking(turn)
                    self.assertIn("组织", window.status_label.text())
                    window._one_shot_preparing(turn)
                    self.assertIn("生成", window.status_label.text())
                    window._one_shot_start_playback(turn, path.read_bytes(), "固定台词", path)
                    self.assertIn("第 1/2 段", window.status_label.text())
                    window._one_shot_position_changed(1300)
                    self.assertIn("第 2/2 段", window.status_label.text())
                chat.assert_not_called()
                self.assertEqual(play.call_count, 1)
                self.assertTrue(play.call_args.args[0].startswith(b"RIFF"))
                self.assertFalse(window.one_shot_enabled.isChecked())
                self.assertEqual(window.interaction_state, InteractionState.SPEAKING)
                self.assertEqual(window.chat_history, [])
                self.assertIn("固定台词", window.result_edit.toPlainText())
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.hide_on_close = False
                window.close()

    def test_interrupted_one_shot_thinking_never_starts_audio(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            path = Path(directory) / "preview.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0" * 320)
            window = gui.MainWindow()
            window.one_shot_audio_path = path
            self.assertEqual(window.one_shot_trigger.text(), "来到这里感觉如何")
            window.one_shot_enabled.setChecked(True)
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.set_interaction_state(InteractionState.LISTENING)
            try:
                with patch.object(window, "_play_custom_audio") as play:
                    window.continuous_result("庄方宜来到这里感觉如何？", "zh")
                    turn = window.active_turn
                    window.interrupt_reply()
                    window._one_shot_start_playback(turn, path.read_bytes(), "", path)
                play.assert_not_called()
                self.assertEqual(window.interaction_state, InteractionState.LISTENING)
            finally:
                window.continuous_worker = None
                window.close()

    def test_custom_speech_prefetches_while_playing_and_discards_on_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            reference.write_bytes(b"RIFFmock-audio")
            settings = SpeechSettings("gpt_sovits", reference_audio=str(reference), reference_text="你好")
            with patch.object(gui, "SETTINGS", Path(directory) / "settings.json"), \
                    patch.object(gui, "load_speech_settings", return_value=settings):
                window = gui.MainWindow()
                network = FakeSpeechNetwork()
                window.custom_tts_network = network
                played = []

                def start_playback(_data):
                    played.append(window.current_tts_chunk)
                    window.custom_tts_playing = True
                    window._prefetch_custom_tts()

                try:
                    with patch.object(window, "_play_custom_audio", side_effect=start_playback):
                        window.custom_tts_active = True
                        window.tts_pending = True
                        window.tts_chunks = ["第一段", "第二段", "第三段"]
                        window.set_interaction_state(InteractionState.SPEAKING)
                        window.speak_next_chunk()
                        self.assertEqual([item[0] for item in network.requests], ["第一段"])
                        network.requests[0][1].finished.emit()
                        self.assertEqual(played, ["第一段"])
                        self.assertEqual([item[0] for item in network.requests], ["第一段", "第二段"])
                        network.requests[1][1].finished.emit()
                        self.assertEqual(played, ["第一段"])
                        window._custom_playback_finished(window.custom_playback_generation)
                        self.assertEqual(played, ["第一段", "第二段"])
                        self.assertEqual([item[0] for item in network.requests],
                                         ["第一段", "第二段", "第三段"])
                        window.interrupt_reply()
                        self.assertTrue(network.requests[2][1].aborted)
                        network.requests[2][1].finished.emit()
                        self.assertEqual(played, ["第一段", "第二段"])
                        self.assertIsNone(window.custom_tts_ready_audio)
                finally:
                    window.close()

    def test_custom_speech_waits_for_late_prefetch_then_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            reference.write_bytes(b"RIFFmock-audio")
            settings = SpeechSettings("gpt_sovits", reference_audio=str(reference), reference_text="你好")
            with patch.object(gui, "SETTINGS", Path(directory) / "settings.json"), \
                    patch.object(gui, "load_speech_settings", return_value=settings):
                window = gui.MainWindow()
                network = FakeSpeechNetwork()
                window.custom_tts_network = network
                played = []

                def start_playback(_data):
                    played.append(window.current_tts_chunk)
                    window.custom_tts_playing = True
                    window._prefetch_custom_tts()

                try:
                    with patch.object(window, "_play_custom_audio", side_effect=start_playback):
                        window.custom_tts_active = True
                        window.tts_pending = True
                        window.tts_chunks = ["第一段", "第二段"]
                        window.set_interaction_state(InteractionState.SPEAKING)
                        window.speak_next_chunk()
                        network.requests[0][1].finished.emit()
                        self.assertEqual(played, ["第一段"])
                        window._custom_playback_finished(window.custom_playback_generation)
                        self.assertEqual(played, ["第一段"])
                        self.assertIn("等待下一段", window.status_label.text())
                        network.requests[1][1].finished.emit()
                        self.assertEqual(played, ["第一段", "第二段"])
                        window._custom_playback_finished(window.custom_playback_generation)
                        self.assertFalse(window.tts_pending)
                finally:
                    window.close()

    def test_failed_prefetch_falls_back_without_replaying_the_previous_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            reference.write_bytes(b"RIFFmock-audio")
            settings = SpeechSettings("gpt_sovits", reference_audio=str(reference), reference_text="你好")
            with patch.object(gui, "SETTINGS", Path(directory) / "settings.json"), \
                    patch.object(gui, "load_speech_settings", return_value=settings):
                window = gui.MainWindow()
                network = FakeSpeechNetwork()
                window.custom_tts_network = network
                fallback = FakeTts()
                window.tts = fallback
                try:
                    def start_playback(_data):
                        window.custom_tts_playing = True
                        window._prefetch_custom_tts()

                    with patch.object(window, "_play_custom_audio", side_effect=start_playback):
                        window.custom_tts_active = True
                        window.tts_pending = True
                        window.tts_chunks = ["第一段", "第二段", "第三段"]
                        window.set_interaction_state(InteractionState.SPEAKING)
                        window.speak_next_chunk()
                        self.assertEqual(network.transfer_timeouts, [25_000])
                        self.assertEqual(network.requests[0][1].status, 200)
                        network.requests[0][1].finished.emit()
                        self.assertEqual([item[0] for item in network.requests], ["第一段", "第二段"])
                        network.requests[1][1].status = 500
                        network.requests[1][1].body = b'{"message":"temporary error"}'
                        network.requests[1][1].finished.emit()
                        self.assertEqual(fallback.spoken, [])
                        window._custom_playback_finished(window.custom_playback_generation)
                        self.assertFalse(window.custom_tts_active)
                        self.assertEqual(fallback.spoken, ["第二段"])
                        self.assertEqual(window.tts_chunks, ["第三段"])
                finally:
                    window.close()

    def test_combined_voice_turn_waits_for_user_and_sends_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.speak_reply.setChecked(False)
            window.voice_turn_combo.setCurrentIndex(1)
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            FakeChatWorker.messages.clear()
            real_now = gui.time.monotonic()
            tick = [real_now]
            try:
                with patch.object(gui, "ChatWorker", FakeChatWorker), \
                     patch.object(gui, "load_key", return_value="test-only-key"), \
                     patch.object(gui.time, "monotonic", side_effect=lambda: tick[0]):
                    window.continuous_result("庄方宜，我今天", "zh")
                    self.assertEqual(FakeChatWorker.messages, [])
                    tick[0] += 0.6
                    window.continuous_result("遇到一件事情", "zh")
                    self.assertEqual(FakeChatWorker.messages, [])
                    self.assertTrue(window.voice_turn_manager.has_pending)
                    tick[0] += 1.5
                    window._finish_voice_turn_if_ready()
                self.assertEqual(FakeChatWorker.messages, ["我今天 遇到一件事情"])
                self.assertEqual(len(window.chat_history), 2)
                self.assertFalse(window.voice_turn_manager.has_pending)
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.voice_turn_timer.stop()
                window.close()

    def test_combined_voice_turn_not_sent_during_active_speech_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.voice_turn_combo.setCurrentIndex(1)
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            FakeChatWorker.messages.clear()
            try:
                with patch.object(gui, "ChatWorker", FakeChatWorker), \
                     patch.object(gui, "load_key", return_value="test-only-key"):
                    window.continuous_result("庄方宜，我还没讲完", "zh")
                    window.continuous_voice_started()
                    # Even when the deadline expires, active speech blocks dispatch.
                    with patch.object(gui.time, "monotonic",
                                      return_value=window.voice_turn_manager.deadline + 1):
                        window._finish_voice_turn_if_ready()
                    self.assertEqual(FakeChatWorker.messages, [])
                    window.stop_continuous()
                    self.assertFalse(window.voice_turn_manager.has_pending)
                    self.assertFalse(window.voice_turn_timer.isActive())
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.voice_turn_timer.stop()
                window.close()

    def test_voice_turn_default_is_legacy_and_setting_persists(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            try:
                self.assertEqual(window.voice_turn_combo.currentData(), "legacy")
                window.voice_turn_combo.setCurrentIndex(1)
                self.assertEqual(gui.MainWindow.load_wake_settings()["voice_turn_mode"],
                                 "combined")
            finally:
                window.voice_turn_timer.stop()
                window.close()

    def test_wake_sends_command_once_and_resumes_capture(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.speak_reply.setChecked(False)
            window.gate = ConversationGate("庄方宜", ("庄方怡",), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            FakeChatWorker.messages.clear()
            try:
                with patch.object(gui, "ChatWorker", FakeChatWorker), patch.object(gui, "load_key", return_value="test-only-key"):
                    window.continuous_result("普通讲话", "zh")
                    self.assertEqual(FakeChatWorker.messages, [])
                    window.continuous_result("庄方怡，你好", "zh")
                self.assertEqual(FakeChatWorker.messages, ["你好"])
                self.assertEqual(window.chat_history[-1]["content"], "你好，我在这里。")
                self.assertIn("你：庄方宜，你好", window.result_edit.toPlainText())
                self.assertEqual(window.continuous_stream.stops, 0)
                window.receive_continuous_audio(np.zeros((512, 1), dtype=np.float32), 512, None, None)
                self.assertEqual(len(window.continuous_worker.submitted), 1)
                self.assertEqual(window.continuous_stream.starts, 0)
                self.assertEqual(window.interaction_state, InteractionState.LISTENING)
                self.assertFalse(window.continuous_worker.paused)
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_microphone_stays_open_during_speech(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.tts = FakeTts()
            window.gate = ConversationGate("庄方宜", ("庄方怡",), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            try:
                with patch.object(gui, "ChatWorker", FakeChatWorker), patch.object(gui, "load_key", return_value="test-only-key"):
                    window.continuous_result("庄方宜，你好", "zh")
                self.assertEqual(window.tts.spoken, ["你好，我在这里。"])
                self.assertEqual(window.interaction_state, InteractionState.SPEAKING)
                self.assertEqual(window.continuous_stream.stops, 0)
                window.tts_state_changed(QTextToSpeech.State.Ready)
                QApplication.processEvents()
                self.assertEqual(window.interaction_state, InteractionState.LISTENING)
                self.assertEqual(window.continuous_stream.starts, 0)
                self.assertFalse(window.continuous_worker.paused)
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_speaker_echo_is_ignored_but_named_interruption_stops_tts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.tts = FakeTts()
            window.gate = ConversationGate("庄方宜", ("庄方怡",), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            FakeChatWorker.messages.clear()
            try:
                with patch.object(gui, "ChatWorker", FakeChatWorker), patch.object(gui, "load_key", return_value="test-only-key"):
                    window.continuous_result("庄方宜，你好", "zh")
                    self.assertEqual(window.interaction_state, InteractionState.SPEAKING)
                    window.continuous_result("你好，我在这里。", "zh")
                    self.assertEqual(FakeChatWorker.messages, ["你好"])
                    self.assertEqual(window.tts.stops, 0)
                    window.continuous_result("庄方宜，换个话题", "zh")
                self.assertEqual(FakeChatWorker.messages, ["你好", "换个话题"])
                self.assertEqual(window.tts.stops, 1)
                self.assertEqual(window.continuous_stream.stops, 0)
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_headphones_interrupt_on_voice_start(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.tts = FakeTts()
            window.barge_in_mode.setCurrentIndex(1)
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.SPEAKING)
            window.tts_pending = True
            window.tts_chunks = ["第二段"]
            try:
                window.continuous_voice_started()
                self.assertEqual(window.interaction_state, InteractionState.LISTENING)
                self.assertEqual(window.tts.stops, 1)
                self.assertEqual(window.tts_chunks, [])
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_new_utterance_supersedes_stale_model_reply(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.speak_reply.setChecked(False)
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            DeferredChatWorker.instances.clear()
            try:
                with patch.object(gui, "ChatWorker", DeferredChatWorker), patch.object(gui, "load_key", return_value="test-only-key"):
                    window.continuous_result("庄方宜，第一句", "zh")
                    self.assertEqual(window.interaction_state, InteractionState.THINKING)
                    window.continuous_result("庄方宜，第二句", "zh")
                    first, second = DeferredChatWorker.instances
                    first.replied.emit("旧回复")
                    first.finished.emit()
                    self.assertNotIn("旧回复", window.result_edit.toPlainText())
                    second.replied.emit("新回复")
                    second.finished.emit()
                self.assertEqual(window.chat_history[-2]["content"], "第二句")
                self.assertEqual(window.chat_history[-1]["content"], "新回复")
                self.assertEqual(window.interaction_state, InteractionState.LISTENING)
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_echo_started_during_playback_is_ignored_after_tts_finishes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.gate = ConversationGate("庄方宜", (), 30)
            window.gate.process("庄方宜")
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.spoken_answer = "你好，我在这里。"
            window.set_interaction_state(InteractionState.SPEAKING)
            FakeChatWorker.messages.clear()
            try:
                window.continuous_voice_started()
                window.set_interaction_state(InteractionState.LISTENING)
                window.echo_guard_until = 0
                window.continuous_result("你好，我在这里。", "zh")
                self.assertEqual(FakeChatWorker.messages, [])
                self.assertEqual(window.chat_history, [])
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_reply_does_not_speak_over_new_user_utterance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, "SETTINGS", Path(directory) / "settings.json"):
            window = gui.MainWindow()
            window.gate = ConversationGate("庄方宜", (), 30)
            window.continuous_worker = FakeCapture()
            window.continuous_stream = FakeStream()
            window.set_interaction_state(InteractionState.LISTENING)
            DeferredChatWorker.instances.clear()
            try:
                with patch.object(gui, "ChatWorker", DeferredChatWorker), patch.object(gui, "load_key", return_value="test-only-key"):
                    window.continuous_result("庄方宜，第一句", "zh")
                    window.continuous_voice_started()
                    worker = DeferredChatWorker.instances[-1]
                    worker.replied.emit("迟到的回复")
                    worker.finished.emit()
                self.assertEqual(window.interaction_state, InteractionState.LISTENING)
                self.assertNotIn("迟到的回复", window.result_edit.toPlainText())
                self.assertEqual(window.chat_history, [])
            finally:
                window.continuous_worker = None
                window.continuous_stream = None
                window.close()

    def test_speech_chunking_and_echo_comparison(self):
        self.assertEqual(speech_chunks("你好。今天怎么样？我在这里。", limit=10),
                         ["你好。今天怎么样？", "我在这里。"])
        self.assertTrue(likely_playback_echo("今天怎么样？", "你好。今天怎么样？我在这里。"))
        self.assertFalse(likely_playback_echo("庄方宜，换个话题", "你好。今天怎么样？我在这里。"))


if __name__ == "__main__":
    unittest.main()
