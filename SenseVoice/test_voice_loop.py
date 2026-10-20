import os
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import QApplication

import gui
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


class FakeStream:
    def __init__(self):
        self.stops = 0
        self.starts = 0

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1


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


class VoiceLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
