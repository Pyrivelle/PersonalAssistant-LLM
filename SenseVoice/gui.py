"""Small Qt interface for the standalone SenseVoice exercise."""

from __future__ import annotations

import sys
import threading
import json
import time
from queue import Empty, Full, Queue

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QElapsedTimer, QThread, QTimer, Signal, Slot
from PySide6.QtCore import QLocale
from PySide6.QtGui import QFont
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from continuous import StreamingVad
from chat_worker import ChatWorker, MemoryWriteWorker
from emotion import EmotionState
from dialogue_context import DialogueContext
from interaction import InteractionState, likely_playback_echo, speech_chunks
from llm import load_key, load_settings
from recognize import ROOT, create_recognizer, decode_segment, read_wav, recognize
from wake import ConversationGate, DEFAULT_ALIASES, DEFAULT_NAME, TRAILING, parse_aliases


MAX_SECONDS = 60
SAMPLE = ROOT / "samples" / "zh.wav"
SETTINGS = ROOT / "settings.json"
LANGUAGES = (
    ("中文", "zh"),
    ("自动识别", "auto"),
    ("粤语", "yue"),
    ("英语", "en"),
    ("日语", "ja"),
    ("韩语", "ko"),
)


class RecognitionWorker(QThread):
    result_ready = Signal(str, str)
    failed = Signal(str)

    def __init__(self, samples: np.ndarray, language: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.samples = samples
        self.language = language

    def run(self) -> None:
        try:
            self.result_ready.emit(*recognize(self.samples, self.language))
        except Exception as error:
            self.failed.emit(str(error))


class ContinuousWorker(QThread):
    ready = Signal()
    voice_started = Signal()
    voice_ended = Signal()
    result_ready = Signal(str, str)
    failed = Signal(str)

    def __init__(self, language: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.language = language
        self.frames: Queue[np.ndarray] = Queue(maxsize=256)
        self.stop_requested = threading.Event()
        self.paused = threading.Event()
        self.reset_requested = threading.Event()
        self.reset_done = threading.Event()

    def submit(self, samples: np.ndarray) -> None:
        if self.stop_requested.is_set() or self.paused.is_set():
            return
        try:
            self.frames.put_nowait(samples)
        except Full:
            self.failed.emit("音频处理跟不上录音速度，监听已停止")
            self.request_stop()

    def request_stop(self) -> None:
        self.stop_requested.set()

    def pause(self) -> None:
        self.paused.set()
        self.reset_requested.set()
        self.reset_done.clear()

    def resume(self) -> None:
        self.reset_requested.set()
        self.reset_done.clear()
        self.paused.clear()
        self.reset_done.wait(timeout=1)

    def run(self) -> None:
        try:
            vad = StreamingVad(silence_seconds=0.8)
            engine = create_recognizer(self.language)
            if self.stop_requested.is_set():
                return
            self.ready.emit()
            speaking = False
            while not self.stop_requested.is_set() or not self.frames.empty():
                if self.paused.is_set():
                    self._discard_frames()
                    if self.stop_requested.wait(0.05):
                        break
                    continue
                if self.reset_requested.is_set():
                    self._discard_frames()
                    vad = StreamingVad(silence_seconds=0.8)
                    speaking = False
                    self.reset_requested.clear()
                    self.reset_done.set()
                try:
                    samples = self.frames.get(timeout=0.1)
                except Empty:
                    continue
                if self.paused.is_set():
                    continue
                completed = vad.accept(samples)
                detected = vad.is_speech_detected()
                if detected and not speaking:
                    self.voice_started.emit()
                if completed:
                    self.voice_ended.emit()
                speaking = detected
                for segment in completed:
                    self._decode(engine, segment)
            if not self.paused.is_set():
                for segment in vad.finish():
                    self._decode(engine, segment)
        except Exception as error:
            self.failed.emit(str(error))

    def _decode(self, engine, samples: np.ndarray) -> None:
        text, detected = decode_segment(engine, samples)
        self.result_ready.emit(text, self.language if self.language != "auto" else detected)

    def _discard_frames(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except Empty:
                break


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Shimano · SenseVoice")
        self.resize(670, 800)
        self.setMinimumSize(550, 720)
        self.stream: sd.InputStream | None = None
        self.continuous_stream: sd.InputStream | None = None
        self.audio_chunks: list[np.ndarray] = []
        self.audio_error = ""
        self.worker: RecognitionWorker | None = None
        self.continuous_worker: ContinuousWorker | None = None
        self.elapsed = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_recording_time)
        self.wake_timer = QTimer(self)
        self.wake_timer.setInterval(500)
        self.wake_timer.timeout.connect(self.update_wake_status)
        self.gate: ConversationGate | None = None
        self.speech_started_at: float | None = None
        self.speech_started_during_playback = False
        self.interaction_state = InteractionState.IDLE
        self.dialogue = DialogueContext()
        self.chat_history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        self.chat_worker: ChatWorker | None = None
        self.chat_workers: dict[int, ChatWorker] = {}
        self.memory_workers: set[MemoryWriteWorker] = set()
        self.turn_serial = 0
        self.active_turn: int | None = None
        self.pending_message = ""
        self.conversation_busy = False
        self.tts_pending = False
        self.tts_chunks: list[str] = []
        self.spoken_answer = ""
        self.current_tts_chunk = ""
        self.tts_advance_pending = False
        self.echo_guard_until = 0.0
        self.llm_window = None
        self.memory_window = None
        engines = QTextToSpeech.availableEngines()
        self.tts = QTextToSpeech("winrt" if "winrt" in engines else "sapi", self) if engines else None
        if self.tts is not None:
            self.tts.setLocale(QLocale("zh_CN"))
            self.tts.stateChanged.connect(self.tts_state_changed)

        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        title = QLabel("SenseVoice 语音识别")
        title.setObjectName("title")
        subtitle = QLabel("语音在本机识别；唤醒后可将文字发送给模型，并朗读回复。")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        settings = QFrame()
        settings.setObjectName("card")
        form = QFormLayout(settings)
        form.setContentsMargins(18, 17, 18, 17)
        form.setSpacing(12)

        self.device_combo = QComboBox()
        self.device_combo.setObjectName("deviceCombo")
        self.device_combo.setMinimumContentsLength(34)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.clicked.connect(self.refresh_devices)
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        form.addRow("麦克风", device_row)

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageCombo")
        for label, code in LANGUAGES:
            self.language_combo.addItem(label, code)
        form.addRow("识别语言", self.language_combo)

        saved = self.load_wake_settings()
        self.wake_name = QLineEdit(saved["name"])
        self.wake_name.setObjectName("wakeName")
        self.wake_name.setMaxLength(20)
        self.wake_name.setPlaceholderText("例如：庄方宜")
        form.addRow("唤醒称呼", self.wake_name)

        self.wake_aliases = QLineEdit(saved["aliases"])
        self.wake_aliases.setObjectName("wakeAliases")
        self.wake_aliases.setPlaceholderText("用逗号分隔，例如：庄天师，小庄")
        form.addRow("识别别名", self.wake_aliases)

        self.wake_timeout = QSpinBox()
        self.wake_timeout.setObjectName("wakeTimeout")
        self.wake_timeout.setRange(10, 180)
        self.wake_timeout.setValue(saved["timeout"])
        self.wake_timeout.setSuffix(" 秒")
        form.addRow("对话保持", self.wake_timeout)
        self.auto_reply = QCheckBox("唤醒后自动发送给模型")
        self.auto_reply.setChecked(saved["auto_reply"])
        form.addRow("对话", self.auto_reply)
        self.speak_reply = QCheckBox("用扬声器朗读模型回复")
        self.speak_reply.setChecked(saved["speak_reply"] and self.tts is not None)
        self.speak_reply.setEnabled(self.tts is not None)
        form.addRow("语音输出", self.speak_reply)
        self.voice_combo = QComboBox()
        if self.tts is not None:
            for voice in self.tts.availableVoices():
                self.voice_combo.addItem(voice.name(), voice)
            preferred = self.voice_combo.findText(saved["voice_name"])
            if preferred < 0:
                preferred = self.voice_combo.findText("Microsoft Yaoyao")
            if preferred >= 0:
                self.voice_combo.setCurrentIndex(preferred)
                self.tts.setVoice(self.voice_combo.currentData())
        self.voice_combo.setEnabled(self.tts is not None and self.speak_reply.isChecked())
        self.voice_combo.currentIndexChanged.connect(self.select_voice)
        self.speak_reply.toggled.connect(self.voice_combo.setEnabled)
        form.addRow("发音人", self.voice_combo)
        self.barge_in_mode = QComboBox()
        self.barge_in_mode.addItem("扬声器：说唤醒名后打断", "speakers")
        self.barge_in_mode.addItem("耳机：检测到说话立即打断", "headphones")
        mode_index = self.barge_in_mode.findData(saved["barge_in_mode"])
        if mode_index >= 0:
            self.barge_in_mode.setCurrentIndex(mode_index)
        self.barge_in_mode.currentIndexChanged.connect(self.save_wake_settings)
        form.addRow("语音打断", self.barge_in_mode)
        self.model_button = QPushButton("模型设置与文字测试…")
        self.model_button.clicked.connect(self.open_model_settings)
        form.addRow("模型", self.model_button)
        self.memory_button = QPushButton("查看与管理长期记忆…")
        self.memory_button.clicked.connect(self.open_memory_manager)
        form.addRow("记忆", self.memory_button)
        self.wake_name.editingFinished.connect(self.save_wake_settings)
        self.wake_aliases.editingFinished.connect(self.save_wake_settings)
        self.wake_timeout.valueChanged.connect(self.save_wake_settings)
        self.auto_reply.toggled.connect(self.save_wake_settings)
        self.speak_reply.toggled.connect(self.save_wake_settings)
        self.voice_combo.currentIndexChanged.connect(self.save_wake_settings)
        layout.addWidget(settings)

        actions = QHBoxLayout()
        self.record_button = QPushButton("●  开始录音")
        self.record_button.setObjectName("recordButton")
        self.record_button.clicked.connect(self.toggle_recording)
        self.sample_button = QPushButton("试用示例")
        self.sample_button.setObjectName("sampleButton")
        self.sample_button.clicked.connect(self.try_sample)
        actions.addWidget(self.record_button, 1)
        actions.addWidget(self.sample_button)
        layout.addLayout(actions)

        self.continuous_button = QPushButton("◎  开启唤醒监听")
        self.continuous_button.setObjectName("continuousButton")
        self.continuous_button.clicked.connect(self.toggle_continuous)
        layout.addWidget(self.continuous_button)

        self.interrupt_button = QPushButton("立即停止当前回复")
        self.interrupt_button.setEnabled(False)
        self.interrupt_button.clicked.connect(self.interrupt_reply)
        layout.addWidget(self.interrupt_button)

        self.state_label = QLabel("状态：Idle")
        layout.addWidget(self.state_label)

        self.status_label = QLabel("就绪 · 录音最长 60 秒")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        result_header = QHBoxLayout()
        result_header.addWidget(QLabel("识别结果"))
        result_header.addStretch()
        self.copy_button = QPushButton("复制文字")
        self.copy_button.setObjectName("copyButton")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_result)
        result_header.addWidget(self.copy_button)
        self.clear_history_button = QPushButton("清空对话")
        self.clear_history_button.clicked.connect(self.clear_chat_history)
        result_header.addWidget(self.clear_history_button)
        layout.addLayout(result_header)

        self.result_edit = QPlainTextEdit()
        self.result_edit.setObjectName("resultEdit")
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("说出唤醒称呼后，对话文字会显示在这里")
        layout.addWidget(self.result_edit, 1)

        self.setStyleSheet("""
            QWidget { color: #243041; font-size: 14px; }
            QWidget#root { background: #f6f8fb; }
            QLabel { background: transparent; }
            QLabel#title { font-size: 23px; font-weight: 700; }
            QLabel#subtitle, QLabel#statusLabel { color: #617186; }
            QFrame#card { background: white; border: 1px solid #dce3ec; border-radius: 12px; }
            QComboBox, QPlainTextEdit, QLineEdit, QSpinBox { background: white; border: 1px solid #cfd9e5; border-radius: 8px; padding: 8px; }
            QComboBox, QLineEdit, QSpinBox { min-height: 24px; }
            QPlainTextEdit { font-size: 16px; }
            QPushButton { background: white; border: 1px solid #cbd6e2; border-radius: 8px; padding: 9px 15px; }
            QPushButton:hover { background: #edf4fb; }
            QPushButton:disabled { color: #9daab9; background: #f2f4f6; }
            QPushButton#recordButton { background: #2468c5; border-color: #2468c5; color: white; font-weight: 600; }
            QPushButton#recordButton:hover { background: #1d57a6; }
            QPushButton#continuousButton { border-color: #2468c5; color: #2468c5; font-weight: 600; }
            QPushButton#continuousButton:hover { background: #e8f2ff; }
        """)
        self.refresh_devices()

    @staticmethod
    def load_wake_settings() -> dict:
        defaults = {"name": DEFAULT_NAME, "aliases": DEFAULT_ALIASES, "timeout": 30,
                    "auto_reply": True, "speak_reply": True, "voice_name": "Microsoft Yaoyao",
                    "barge_in_mode": "speakers"}
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
            return {
                "name": str(data.get("name") or DEFAULT_NAME),
                "aliases": str(data.get("aliases", DEFAULT_ALIASES)),
                "timeout": min(180, max(10, int(data.get("timeout", 30)))),
                "auto_reply": bool(data.get("auto_reply", True)),
                "speak_reply": bool(data.get("speak_reply", True)),
                "voice_name": str(data.get("voice_name", "Microsoft Yaoyao")),
                "barge_in_mode": str(data.get("barge_in_mode", "speakers")),
            }
        except (OSError, ValueError, TypeError, AttributeError):
            return defaults

    @Slot()
    def save_wake_settings(self) -> None:
        name = self.wake_name.text().strip() or DEFAULT_NAME
        if name != self.wake_name.text():
            self.wake_name.setText(name)
        data = {"name": name, "aliases": self.wake_aliases.text().strip(), "timeout": self.wake_timeout.value(),
                "auto_reply": self.auto_reply.isChecked(), "speak_reply": self.speak_reply.isChecked(),
                "voice_name": self.voice_combo.currentText(),
                "barge_in_mode": self.barge_in_mode.currentData()}
        try:
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            self.status_label.setText(f"保存唤醒设置失败：{error}")

    @Slot()
    def select_voice(self) -> None:
        if self.tts is not None and self.voice_combo.currentData() is not None:
            self.tts.setVoice(self.voice_combo.currentData())

    def set_interaction_state(self, state: InteractionState) -> None:
        self.interaction_state = state
        self.conversation_busy = state in (InteractionState.THINKING, InteractionState.SPEAKING)
        self.state_label.setText(f"状态：{state.value}")
        self.interrupt_button.setEnabled(self.conversation_busy)

    @Slot()
    def open_model_settings(self) -> None:
        from llm_gui import ChatWindow

        if self.llm_window is None:
            self.llm_window = ChatWindow()
        self.llm_window.show()
        self.llm_window.raise_()
        self.llm_window.activateWindow()

    @Slot()
    def open_memory_manager(self) -> None:
        from memory_gui import MemoryDialog

        if self.memory_window is None:
            self.memory_window = MemoryDialog(self)
        self.memory_window.show()
        self.memory_window.raise_()
        self.memory_window.activateWindow()

    @Slot()
    def refresh_devices(self) -> None:
        previous = self.device_combo.currentData()
        self.device_combo.clear()
        self.device_combo.addItem("系统默认麦克风", None)
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            for index, device in enumerate(devices):
                if device["max_input_channels"] > 0:
                    host = hostapis[device["hostapi"]]["name"]
                    self.device_combo.addItem(f"{device['name']} · {host}", index)
            if previous is not None:
                choice = self.device_combo.findData(previous)
                if choice >= 0:
                    self.device_combo.setCurrentIndex(choice)
            self.status_label.setText("就绪 · 录音最长 60 秒")
        except Exception as error:
            self.status_label.setText(f"读取麦克风失败：{error}")

    @Slot()
    def toggle_recording(self) -> None:
        if self.stream is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        device = self.device_combo.currentData()
        try:
            sd.check_input_settings(device=device, channels=1, samplerate=16000, dtype="float32")
            self.audio_chunks = []
            self.audio_error = ""
            stream = sd.InputStream(
                device=device,
                samplerate=16000,
                channels=1,
                dtype="float32",
                callback=self.receive_audio,
            )
            stream.start()
            self.stream = stream
        except Exception as error:
            if "stream" in locals():
                stream.close()
            self.status_label.setText(f"无法打开麦克风：{error}")
            return

        self.result_edit.clear()
        self.copy_button.setEnabled(False)
        self.record_button.setText("■  停止并识别")
        self.device_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.sample_button.setEnabled(False)
        self.continuous_button.setEnabled(False)
        self.elapsed.start()
        self.timer.start()
        self.update_recording_time()

    def receive_audio(self, input_data, _frames, _time, status) -> None:
        if status:
            self.audio_error = str(status)
        self.audio_chunks.append(input_data[:, 0].copy())

    @Slot()
    def update_recording_time(self) -> None:
        seconds = self.elapsed.elapsed() / 1000
        self.status_label.setText(f"正在录音 · {seconds:.1f} / {MAX_SECONDS} 秒")
        if seconds >= MAX_SECONDS:
            self.stop_recording()

    def stop_recording(self) -> None:
        self.timer.stop()
        stream = self.stream
        self.stream = None
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception as error:
            self.status_label.setText(f"录音失败：{error}")
            self.set_idle()
            return
        if self.audio_error:
            self.status_label.setText(f"录音设备报告错误：{self.audio_error}")
            self.set_idle()
            return
        if not self.audio_chunks:
            self.status_label.setText("没有录到音频，请检查麦克风")
            self.set_idle()
            return
        samples = np.concatenate(self.audio_chunks)[: MAX_SECONDS * 16000]
        self.audio_chunks = []
        self.start_recognition(samples)

    @Slot()
    def toggle_continuous(self) -> None:
        if self.continuous_worker is None:
            self.start_continuous()
        else:
            self.stop_continuous()

    def start_continuous(self) -> None:
        self.save_wake_settings()
        self.gate = ConversationGate(
            self.wake_name.text(), parse_aliases(self.wake_aliases.text()), self.wake_timeout.value()
        )
        self.speech_started_at = None
        self.speech_started_during_playback = False
        self.result_edit.clear()
        self.copy_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.sample_button.setEnabled(False)
        self.device_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.wake_name.setEnabled(False)
        self.wake_aliases.setEnabled(False)
        self.wake_timeout.setEnabled(False)
        self.continuous_button.setText("■  停止持续监听")
        self.status_label.setText("正在加载 VAD 和识别模型…")
        self.continuous_worker = ContinuousWorker(self.language_combo.currentData(), self)
        self.continuous_worker.ready.connect(self.continuous_ready)
        self.continuous_worker.voice_started.connect(self.continuous_voice_started)
        self.continuous_worker.voice_ended.connect(self.continuous_voice_ended)
        self.continuous_worker.result_ready.connect(self.continuous_result)
        self.continuous_worker.failed.connect(self.continuous_failed)
        self.continuous_worker.finished.connect(self.continuous_finished)
        self.continuous_worker.start()

    @Slot()
    def continuous_ready(self) -> None:
        worker = self.continuous_worker
        if worker is None or worker.stop_requested.is_set():
            return
        device = self.device_combo.currentData()
        try:
            sd.check_input_settings(device=device, channels=1, samplerate=16000, dtype="float32")
            stream = sd.InputStream(
                device=device,
                samplerate=16000,
                channels=1,
                dtype="float32",
                blocksize=512,
                callback=self.receive_continuous_audio,
            )
            stream.start()
            self.continuous_stream = stream
        except Exception as error:
            if "stream" in locals():
                stream.close()
            self.status_label.setText(f"无法打开麦克风：{error}")
            worker.request_stop()
            return
        self.wake_timer.start()
        self.set_interaction_state(InteractionState.LISTENING)
        self.update_wake_status()

    def receive_continuous_audio(self, input_data, _frames, _time, status) -> None:
        worker = self.continuous_worker
        if worker is None:
            return
        if status:
            worker.failed.emit(f"录音设备报告错误：{status}")
            worker.request_stop()
            return
        worker.submit(input_data[:, 0].copy())

    @Slot()
    def continuous_voice_started(self) -> None:
        if self.continuous_worker is None:
            return
        self.speech_started_at = time.monotonic()
        self.speech_started_during_playback = (
            self.interaction_state == InteractionState.SPEAKING
            or self.speech_started_at < self.echo_guard_until
        )
        if self.interaction_state == InteractionState.SPEAKING:
            if self.barge_in_mode.currentData() == "headphones":
                self.interrupt_reply()
                self.status_label.setText("已打断播报 · 正在听你说话")
            else:
                self.status_label.setText("听到声音 · 请说唤醒名以打断播报")
        elif self.interaction_state == InteractionState.THINKING:
            self.status_label.setText("听到补充 · 正在收集这一句")
        else:
            self.status_label.setText("检测到说话 · 正在收集这一句")

    @Slot()
    def continuous_voice_ended(self) -> None:
        if self.continuous_worker is not None:
            self.status_label.setText("说话结束 · 正在识别")

    @Slot(str, str)
    def continuous_result(self, text: str, language: str) -> None:
        if self.gate is None or self.continuous_worker is None or self.continuous_worker.stop_requested.is_set():
            return
        if not text.strip():
            self.speech_started_at = None
            self.speech_started_during_playback = False
            return
        started_during_playback = self.speech_started_during_playback
        self.speech_started_during_playback = False
        if self.interaction_state == InteractionState.SPEAKING or started_during_playback:
            if likely_playback_echo(text, self.spoken_answer):
                self.speech_started_at = None
                self.status_label.setText("已过滤扬声器回声 · 继续播报")
                return
            if self.barge_in_mode.currentData() == "speakers":
                # During loudspeaker playback, require the name before acting on
                # ASR output; a bare VAD event may just be our own speaker.
                leading = text.lstrip(" \t\r\n，,。.!！?？：:、")
                if not any(leading.startswith(alias) and not leading[len(alias):].startswith("丝") for alias in self.gate.aliases):
                    self.speech_started_at = None
                    return
            if self.interaction_state == InteractionState.SPEAKING:
                self.interrupt_reply()
        elif self.interaction_state == InteractionState.THINKING:
            self.interrupt_reply()
        elif time.monotonic() < self.echo_guard_until and likely_playback_echo(text, self.spoken_answer):
            self.speech_started_at = None
            return
        result = self.gate.process(text, now=self.speech_started_at)
        self.speech_started_at = None
        if result.event == "ignored":
            self.update_wake_status()
            return
        self.gate.active_until = time.monotonic() + self.gate.timeout
        message = result.text[len(self.gate.name):].lstrip(TRAILING) if result.called_by_name else result.text
        if not message:
            self.status_label.setText(f"已唤醒 {self.gate.name} · 现在可以直接说话")
            return
        self.result_edit.appendPlainText(f"你：{result.text}")
        self.copy_button.setEnabled(True)
        if self.auto_reply.isChecked():
            self.begin_chat_turn(message)
        else:
            self.status_label.setText(f"对话中 · 已识别一句（{language or '语言未知'}）")

    def begin_chat_turn(self, message: str) -> None:
        if self.continuous_worker is None or self.interaction_state != InteractionState.LISTENING:
            return
        settings = load_settings()
        key = load_key(settings.provider)
        if settings.provider == "siliconflow" and not key:
            self.status_label.setText("请先在“模型设置与文字测试”中保存 API Key")
            return
        self.turn_serial += 1
        turn = self.turn_serial
        self.active_turn = turn
        self.pending_message = message
        self.speech_started_at = None
        self.set_interaction_state(InteractionState.THINKING)
        worker = ChatWorker(settings, key, list(self.chat_history), message, self,
                            emotion=self.dialogue.emotion, context_notes=self.dialogue.prompt())
        self.chat_worker = worker
        self.chat_workers[turn] = worker
        worker.replied.connect(lambda reply, turn=turn: self.chat_replied(turn, reply))
        worker.failed.connect(lambda error, turn=turn: self.chat_failed(turn, error))
        worker.memory_warning.connect(lambda warning: self.result_edit.appendPlainText(f"系统：{warning}\n"))
        worker.finished.connect(lambda turn=turn: self.chat_finished(turn))
        self.status_label.setText("已将识别文字发送给模型 · 等待回复")
        worker.start()

    def chat_replied(self, turn: int, reply: str) -> None:
        if turn != self.active_turn or self.interaction_state != InteractionState.THINKING:
            return
        if self.speech_started_at is not None:
            # A new utterance began before the network reply arrived. Let the
            # recognizer finish it instead of speaking over the user.
            self.active_turn = None
            self.chat_worker = None
            self.pending_message = ""
            self.set_interaction_state(InteractionState.LISTENING)
            self.status_label.setText("你正在说话 · 已忽略刚收到的旧回复")
            return
        worker = self.chat_workers.get(turn)
        memory_enabled = bool(worker and worker.settings.memory_enabled)
        answered_message = self.pending_message
        if worker and not getattr(worker, "memory_command", False):
            self.dialogue.commit(self.pending_message, reply,
                                 getattr(worker, "emotion_candidate", None))
        else:
            self.dialogue.history.extend(({"role": "user", "content": self.pending_message},
                                          {"role": "assistant", "content": reply}))
            self.dialogue.history = self.dialogue.history[-12:]
        self.chat_history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        self.active_turn = None
        self.chat_worker = None
        self.pending_message = ""
        self.result_edit.appendPlainText(f"庄方宜：{reply}\n")
        if memory_enabled and not getattr(worker, "memory_command", False):
            writer = MemoryWriteWorker(answered_message, reply, self)
            self.memory_workers.add(writer)
            writer.failed.connect(lambda warning: self.result_edit.appendPlainText(f"系统：{warning}\n"))
            writer.finished.connect(lambda w=writer: self.memory_write_finished(w))
            writer.start()
        if self.speak_reply.isChecked() and self.tts is not None:
            self.tts_chunks = speech_chunks(reply, limit=42 if self.barge_in_mode.currentData() == "speakers" else 90)
            self.spoken_answer = reply
            if self.tts_chunks:
                self.tts_pending = True
                self.set_interaction_state(InteractionState.SPEAKING)
                self.speak_next_chunk()
            else:
                self.finish_chat_turn()
        else:
            self.status_label.setText("模型回复完成")
            self.finish_chat_turn()

    def speak_next_chunk(self) -> None:
        if not self.tts_pending or self.interaction_state != InteractionState.SPEAKING:
            return
        if self.speech_started_at is not None:
            QTimer.singleShot(100, self.speak_next_chunk)
            return
        self.tts_advance_pending = False
        if not self.tts_chunks:
            self.tts_pending = False
            self.finish_chat_turn()
            return
        self.current_tts_chunk = self.tts_chunks.pop(0)
        self.status_label.setText(f"庄方宜正在分段说话 · 剩余 {len(self.tts_chunks)} 段")
        try:
            self.tts.say(self.current_tts_chunk)
            QTimer.singleShot(150, self.check_tts_state)
        except Exception as error:
            self.tts_pending = False
            self.result_edit.appendPlainText(f"系统：语音播报失败：{error}\n")
            self.finish_chat_turn()

    def chat_failed(self, turn: int, error: str) -> None:
        if turn != self.active_turn:
            return
        self.active_turn = None
        self.chat_worker = None
        self.pending_message = ""
        self.result_edit.appendPlainText(f"系统：模型请求失败：{error}\n")
        self.status_label.setText(f"模型请求失败：{error}")
        self.finish_chat_turn()

    def chat_finished(self, turn: int) -> None:
        worker = self.chat_workers.pop(turn, None)
        if worker is not None:
            worker.deleteLater()
        if turn == self.active_turn:
            self.active_turn = None
            self.chat_worker = None
            self.pending_message = ""
            self.finish_chat_turn()

    def memory_write_finished(self, worker: MemoryWriteWorker) -> None:
        self.memory_workers.discard(worker)
        worker.deleteLater()

    @Slot()
    def check_tts_state(self) -> None:
        if self.tts_pending and self.interaction_state == InteractionState.SPEAKING and self.tts is not None and self.tts.state() in (QTextToSpeech.State.Ready, QTextToSpeech.State.Error):
            self.tts_state_changed(self.tts.state())

    @Slot(QTextToSpeech.State)
    def tts_state_changed(self, state) -> None:
        if not self.tts_pending or self.interaction_state != InteractionState.SPEAKING:
            return
        if state in (QTextToSpeech.State.Ready, QTextToSpeech.State.Error):
            if state == QTextToSpeech.State.Error and hasattr(self, "status_label"):
                self.tts_pending = False
                self.result_edit.appendPlainText("系统：语音播报失败；文字回复仍可查看\n")
                self.status_label.setText("语音播报失败；文字回复仍可查看")
                self.finish_chat_turn()
            else:
                if not self.tts_advance_pending:
                    self.tts_advance_pending = True
                    # A short gap lets the 0.8 s VAD silence timer close a
                    # segment before the next sentence feeds speaker echo.
                    delay = 1100 if self.barge_in_mode.currentData() == "speakers" and self.tts_chunks else 0
                    QTimer.singleShot(delay, self.speak_next_chunk)

    @Slot()
    def interrupt_reply(self) -> None:
        if self.interaction_state == InteractionState.THINKING:
            # urllib may still be waiting for the old reply. Its turn token makes
            # that reply stale, so it cannot enter history or start playback.
            self.active_turn = None
            self.chat_worker = None
            self.pending_message = ""
        elif self.interaction_state == InteractionState.SPEAKING:
            self.tts_pending = False
            self.tts_advance_pending = False
            self.tts_chunks.clear()
            self.current_tts_chunk = ""
            self.echo_guard_until = time.monotonic() + 0.8
            if self.tts is not None:
                self.tts.stop()
        else:
            return
        self.set_interaction_state(InteractionState.LISTENING)
        if self.gate is not None:
            self.gate.touch()
        self.status_label.setText("当前回复已打断 · 正在听你说话")

    def finish_chat_turn(self) -> None:
        if self.interaction_state == InteractionState.SPEAKING:
            self.echo_guard_until = time.monotonic() + 0.8
        self.tts_pending = False
        self.tts_advance_pending = False
        self.tts_chunks.clear()
        self.current_tts_chunk = ""
        self.pending_message = ""
        if self.continuous_worker is None or self.continuous_worker.stop_requested.is_set():
            self.set_interaction_state(InteractionState.IDLE)
            return
        if self.gate is not None:
            self.gate.active_until = time.monotonic() + self.gate.timeout
        self.set_interaction_state(InteractionState.LISTENING)
        self.update_wake_status()

    @Slot()
    def update_wake_status(self) -> None:
        if self.continuous_worker is None or self.gate is None:
            return
        if self.speech_started_at is not None or self.conversation_busy:
            return
        remaining = self.gate.remaining()
        if remaining:
            self.status_label.setText(f"对话中 · {remaining} 秒无对话后返回待唤醒")
        else:
            self.status_label.setText(f"待唤醒 · 请说“{self.gate.name}”")

    @Slot(str)
    def continuous_failed(self, message: str) -> None:
        self.stop_continuous()
        self.status_label.setText(f"持续监听失败：{message}")

    def stop_continuous(self) -> None:
        self.wake_timer.stop()
        self.active_turn = None
        self.chat_worker = None
        self.tts_pending = False
        self.tts_advance_pending = False
        self.tts_chunks.clear()
        self.pending_message = ""
        self.speech_started_during_playback = False
        if self.tts is not None:
            self.tts.stop()
        self.set_interaction_state(InteractionState.IDLE)
        self.continuous_button.setEnabled(False)
        stream = self.continuous_stream
        self.continuous_stream = None
        stop_error = ""
        if stream is not None:
            try:
                stream.stop()
            except Exception as error:
                stop_error = str(error)
            finally:
                stream.close()
        if self.continuous_worker is not None:
            self.continuous_worker.request_stop()
        self.status_label.setText(f"麦克风停止出错：{stop_error}" if stop_error else "正在停止监听并处理最后一句…")

    @Slot()
    def continuous_finished(self) -> None:
        self.wake_timer.stop()
        self.set_interaction_state(InteractionState.IDLE)
        self.gate = None
        self.speech_started_at = None
        self.speech_started_during_playback = False
        worker = self.continuous_worker
        self.continuous_worker = None
        if self.continuous_stream is not None:
            self.continuous_stream.stop()
            self.continuous_stream.close()
            self.continuous_stream = None
        if worker is not None:
            worker.deleteLater()
        self.continuous_button.setText("◎  开启唤醒监听")
        self.continuous_button.setEnabled(True)
        self.set_idle()
        if not self.status_label.text().startswith("持续监听失败"):
            self.status_label.setText("监听已停止")

    @Slot()
    def try_sample(self) -> None:
        try:
            samples = read_wav(SAMPLE)
        except Exception as error:
            self.status_label.setText(f"无法读取示例：{error}")
            return
        self.result_edit.clear()
        self.copy_button.setEnabled(False)
        self.start_recognition(samples)

    def start_recognition(self, samples: np.ndarray) -> None:
        if self.worker is not None:
            return
        self.status_label.setText("正在识别，请稍候…")
        self.record_button.setEnabled(False)
        self.sample_button.setEnabled(False)
        self.device_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.continuous_button.setEnabled(False)
        self.worker = RecognitionWorker(samples, self.language_combo.currentData(), self)
        self.worker.result_ready.connect(self.show_result)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.recognition_finished)
        self.worker.start()

    @Slot(str, str)
    def show_result(self, text: str, language: str) -> None:
        self.result_edit.setPlainText(text)
        self.copy_button.setEnabled(bool(text))
        self.status_label.setText(f"识别完成 · {language or '语言未知'}" if text else "已处理音频，但没有识别到文字")

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.status_label.setText(f"识别失败：{message}")

    @Slot()
    def recognition_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.set_idle()

    def set_idle(self) -> None:
        self.record_button.setText("●  开始录音")
        self.record_button.setEnabled(True)
        self.sample_button.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.wake_name.setEnabled(True)
        self.wake_aliases.setEnabled(True)
        self.wake_timeout.setEnabled(True)
        if self.continuous_worker is None:
            self.continuous_button.setEnabled(True)

    @Slot()
    def copy_result(self) -> None:
        QApplication.clipboard().setText(self.result_edit.toPlainText())
        self.status_label.setText("文字已复制")

    @Slot()
    def clear_chat_history(self) -> None:
        if self.conversation_busy:
            self.status_label.setText("请等当前回复结束后再清空对话")
            return
        self.dialogue = DialogueContext()
        self.chat_history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        self.result_edit.clear()
        self.copy_button.setEnabled(False)
        self.status_label.setText("对话历史已清空")

    def closeEvent(self, event) -> None:
        self.wake_timer.stop()
        self.active_turn = None
        self.tts_pending = False
        if self.tts is not None:
            self.tts.stop()
        self.save_wake_settings()
        if self.stream is not None:
            self.timer.stop()
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()
        if self.continuous_stream is not None:
            self.continuous_stream.stop()
            self.continuous_stream.close()
            self.continuous_stream = None
        if self.continuous_worker is not None and self.continuous_worker.isRunning():
            self.continuous_worker.request_stop()
            self.continuous_worker.wait()
        for worker in tuple(self.chat_workers.values()):
            if worker.isRunning():
                worker.wait()
        for worker in tuple(self.memory_workers):
            if worker.isRunning():
                worker.wait()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Shimano SenseVoice")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
