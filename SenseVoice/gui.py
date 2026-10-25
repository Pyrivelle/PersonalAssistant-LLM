"""Small Qt interface for the standalone SenseVoice exercise."""

from __future__ import annotations

import sys
import threading
import io
import json
import re
import time
import wave
from bisect import bisect_right
from pathlib import Path
from queue import Empty, Full, Queue

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QByteArray, QElapsedTimer, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtCore import QLocale
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QAudioOutput
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from continuous import StreamingVad
from toggle_switch import ToggleSwitch
from branding import APP_NAME, app_icon
from chat_worker import ChatWorker, MemoryWriteWorker, OwnerMemoryWriteWorker, RemoteMemoryWriteWorker
from owner_memory import load_link
from zhuangfy.memory.routing import legacy_memory_allowed
from activity_log import log_event
from emotion import EmotionState
from dialogue_context import DialogueContext, DialogueStore
from turn_planner import PlannerSettings
from interaction import InteractionState, likely_playback_echo, speech_chunks
from llm import load_key, load_settings
from gpt_sovits import VOICE_DATA, load_settings as load_speech_settings
from model_routes import can_attempt_stage
from recognize import ROOT, create_recognizer, decode_segment, read_wav, recognize
from wake import ConversationGate, DEFAULT_ALIASES, DEFAULT_NAME, TRAILING, parse_aliases
from zhuangfy.agent_v2.voice_turn import VoiceTurnManager, VoiceTurnConfig, VoiceTurn


MAX_SECONDS = 60
ONE_SHOT_WAIT_START_MS = 1200
ONE_SHOT_REPLY_READY_MS = 9000
ONE_SHOT_SPEECH_PREPARE_MS = 10500
ONE_SHOT_PLAY_MS = 12500
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
    custom_playback_finished = Signal(int)
    custom_playback_failed = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · SenseVoice")
        self.setWindowIcon(app_icon())
        self.resize(780, 800)
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
        self.voice_turn_manager = VoiceTurnManager(VoiceTurnConfig())
        self.voice_turn_timer = QTimer(self)
        self.voice_turn_timer.setSingleShot(True)
        self.voice_turn_timer.timeout.connect(self._finish_voice_turn_if_ready)
        self.gate: ConversationGate | None = None
        self.speech_started_at: float | None = None
        self.speech_started_during_playback = False
        self.interaction_state = InteractionState.IDLE
        self.context_store = None
        try:
            config = PlannerSettings.load(ROOT / "cognition_settings.json")
            if config.persist_desktop_context:
                self.context_store = DialogueStore(ROOT / "conversation_data" / "desktop_context.sqlite3")
        except Exception as error:
            print(f"语音上下文存储不可用：{type(error).__name__}")
        self.dialogue = self.context_store.load("desktop:voice") if self.context_store else DialogueContext()
        self.chat_history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        self.chat_worker: ChatWorker | None = None
        self.chat_workers: dict[int, ChatWorker] = {}
        self.memory_workers: set[MemoryWriteWorker] = set()
        self.owner_memory_workers: set[OwnerMemoryWriteWorker] = set()
        self.turn_serial = 0
        self.active_turn: int | None = None
        self.pending_message = ""
        self.last_failed_message = ""
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
        self.custom_tts_active = False
        self.custom_tts_request = None
        self.custom_tts_request_text = ""
        self.custom_tts_request_started_at = 0.0
        self.custom_tts_wait_timer = QTimer(self)
        self.custom_tts_wait_timer.setInterval(1000)
        self.custom_tts_wait_timer.timeout.connect(self._update_custom_tts_wait_status)
        self.custom_tts_ready_audio: bytes | None = None
        self.custom_tts_ready_text = ""
        self.custom_tts_playing = False
        self.custom_tts_prefetch_failed = False
        self.custom_playback_generation = 0
        self.custom_playback_stop: threading.Event | None = None
        self.custom_playback_finished.connect(self._custom_playback_finished)
        self.custom_playback_failed.connect(self._custom_playback_failed)
        self.one_shot_position_timer = QTimer(self)
        self.one_shot_position_timer.setInterval(100)
        self.one_shot_position_timer.timeout.connect(self._update_one_shot_position)
        self.one_shot_playback_started_at = 0.0
        self.custom_tts_network = QNetworkAccessManager(self)
        self.custom_tts_audio = QAudioOutput(self)
        self.one_shot_playing = False
        self.one_shot_boundaries: list[int] = []
        self.one_shot_segment = 0
        self.hide_on_close = False

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll)
        central = QWidget()
        central.setObjectName("root")
        scroll.setWidget(central)
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
        self.auto_reply = ToggleSwitch("唤醒后自动发送给模型")
        self.auto_reply.setChecked(saved["auto_reply"])
        form.addRow("对话", self.auto_reply)
        self.speak_reply = ToggleSwitch("用扬声器朗读模型回复")
        speech_available = self.tts is not None or load_speech_settings().engine == "gpt_sovits"
        self.speak_reply.setChecked(saved["speak_reply"] and speech_available)
        self.speak_reply.setEnabled(speech_available)
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
        self.voice_combo.setEnabled(self.tts is not None and self.speak_reply.isChecked()
                                    and load_speech_settings().engine == "system")
        self.voice_combo.currentIndexChanged.connect(self.select_voice)
        self.speak_reply.toggled.connect(self.refresh_speech_engine)
        self.voice_combo.hide()
        self.barge_in_mode = QComboBox()
        self.barge_in_mode.addItem("扬声器：说唤醒名后打断", "speakers")
        self.barge_in_mode.addItem("耳机：检测到说话立即打断", "headphones")
        mode_index = self.barge_in_mode.findData(saved["barge_in_mode"])
        if mode_index >= 0:
            self.barge_in_mode.setCurrentIndex(mode_index)
        self.barge_in_mode.currentIndexChanged.connect(self.save_wake_settings)
        form.addRow("语音打断", self.barge_in_mode)
        self.voice_turn_combo = QComboBox()
        self.voice_turn_combo.addItem("兼容模式：识别一句立即回复", "legacy")
        self.voice_turn_combo.addItem("连续对话：等待补充后合并回复（实验）", "combined")
        turn_mode = self.voice_turn_combo.findData(saved["voice_turn_mode"])
        self.voice_turn_combo.setCurrentIndex(max(0, turn_mode))
        self.voice_turn_combo.currentIndexChanged.connect(self.save_wake_settings)
        form.addRow("对话轮次", self.voice_turn_combo)
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

        trial = QFrame()
        trial.setObjectName("card")
        trial_layout = QVBoxLayout(trial)
        self.one_shot_enabled = ToggleSwitch("一次性固定语音 · 仅本次运行")
        self.one_shot_enabled.toggled.connect(self._one_shot_toggled)
        trial_layout.addWidget(self.one_shot_enabled)
        trial_row = QHBoxLayout()
        self.one_shot_trigger = QLineEdit("来到这里感觉如何")
        self.one_shot_trigger.setPlaceholderText("说出唤醒名后使用的准确触发短语")
        self.one_shot_trigger.setMaxLength(40)
        trial_row.addWidget(self.one_shot_trigger, 1)
        trial_row.addWidget(QPushButton("选择 WAV…", clicked=self.select_one_shot_audio))
        trial_layout.addLayout(trial_row)
        folder = VOICE_DATA.parent / "generated"
        choices = list(folder.glob("zhuangfangyi_here_*.wav")) if folder.is_dir() else []
        self.one_shot_audio_path = max(choices, key=lambda item: item.stat().st_mtime) if choices else None
        self.one_shot_audio_label = QLabel()
        self.one_shot_audio_label.setWordWrap(True)
        self._update_one_shot_audio_label()
        trial_layout.addWidget(self.one_shot_audio_label)
        trial_hint = QLabel("只在语音窗口中启用一次：模拟思考与分段播报，最后使用选定音频；不调用模型或写入记忆。")
        trial_hint.setWordWrap(True)
        trial_layout.addWidget(trial_hint)
        layout.addWidget(trial)

        self.interrupt_button = QPushButton("立即停止当前回复")
        self.interrupt_button.setEnabled(False)
        self.interrupt_button.clicked.connect(self.interrupt_reply)
        layout.addWidget(self.interrupt_button)
        self.retry_failed_button = QPushButton("重试上一条失败的语音文字")
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.clicked.connect(self.retry_failed_turn)
        layout.addWidget(self.retry_failed_button)

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
                    "barge_in_mode": "speakers", "voice_turn_mode": "legacy"}
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
                "voice_turn_mode": (data.get("voice_turn_mode")
                                    if data.get("voice_turn_mode") in ("legacy", "combined")
                                    else "legacy"),
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
                "barge_in_mode": self.barge_in_mode.currentData(),
                "voice_turn_mode": self.voice_turn_combo.currentData()}
        try:
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            self.status_label.setText(f"保存唤醒设置失败：{error}")

    @Slot()
    def select_voice(self) -> None:
        if self.tts is not None and self.voice_combo.currentData() is not None:
            self.tts.setVoice(self.voice_combo.currentData())

    def refresh_speech_engine(self, _checked: bool = False) -> None:
        custom = load_speech_settings().engine == "gpt_sovits"
        available = self.tts is not None or custom
        self.speak_reply.setEnabled(available)
        self.voice_combo.setEnabled(self.tts is not None and self.speak_reply.isChecked() and not custom)

    def play_test_phrase(self, text: str) -> bool:
        if (self.interaction_state not in (InteractionState.IDLE, InteractionState.LISTENING)
                or self.worker is not None or self.chat_worker is not None
                or self.speech_started_at is not None):
            return False
        self.custom_tts_active = load_speech_settings().engine == "gpt_sovits"
        if self.tts is None and not self.custom_tts_active:
            return False
        self.tts_chunks = [text]
        self.spoken_answer = text
        self.tts_pending = True
        self.set_interaction_state(InteractionState.SPEAKING)
        self.speak_next_chunk()
        return True

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
        self.voice_turn_timer.stop()
        self.voice_turn_manager.interrupt()
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
        if self.voice_turn_combo.currentData() == "combined":
            self.voice_turn_timer.stop()
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
            self._arm_voice_turn_timer()
            return
        log_event("语音", "识别", f"{language or '未知语言'} · {text}")
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
            self._arm_voice_turn_timer()
            self.update_wake_status()
            return
        self.gate.active_until = time.monotonic() + self.gate.timeout
        message = result.text[len(self.gate.name):].lstrip(TRAILING) if result.called_by_name else result.text
        if not message:
            self._arm_voice_turn_timer()
            self.status_label.setText(f"已唤醒 {self.gate.name} · 现在可以直接说话")
            return
        self.result_edit.appendPlainText(f"你：{result.text}")
        self.copy_button.setEnabled(True)
        if self.one_shot_enabled.isChecked() and self._one_shot_matches(message):
            self.play_one_shot_audio()
            return
        if self.auto_reply.isChecked():
            if self.voice_turn_combo.currentData() == "combined":
                now = time.monotonic()
                completed = self.voice_turn_manager.offer_final(
                    message, at=now, language=language,
                    authorized=True, final=True)
                if completed is not None:
                    self._dispatch_voice_turn(completed)
                self._arm_voice_turn_timer()
            else:
                self.begin_chat_turn(message)
        else:
            self.status_label.setText(f"对话中 · 已识别一句（{language or '语言未知'}）")

    def _arm_voice_turn_timer(self) -> None:
        self.voice_turn_timer.stop()
        if self.voice_turn_combo.currentData() != "combined":
            return
        deadline = self.voice_turn_manager.deadline
        if deadline is None or self.continuous_worker is None:
            return
        delay_ms = max(20, int((deadline - time.monotonic()) * 1000) + 15)
        self.voice_turn_timer.start(delay_ms)

    @Slot()
    def _finish_voice_turn_if_ready(self) -> None:
        if self.continuous_worker is None or self.voice_turn_combo.currentData() != "combined":
            return
        if self.speech_started_at is not None:
            self.voice_turn_timer.start(100)
            return
        completed = self.voice_turn_manager.take_ready(now=time.monotonic())
        if completed is not None:
            self._dispatch_voice_turn(completed)
        self._arm_voice_turn_timer()

    def _dispatch_voice_turn(self, turn: VoiceTurn) -> None:
        if not self.voice_turn_manager.accepts_output(turn):
            return
        if self.interaction_state == InteractionState.THINKING:
            self.interrupt_reply()
        if self.interaction_state == InteractionState.LISTENING:
            log_event("语音", "轮次合并",
                      f"包含 {turn.parts} 个识别片段；结束原因={turn.reason}")
            self.begin_chat_turn(turn.text)

    def _one_shot_matches(self, message: str) -> bool:
        def normalized(value: str) -> str:
            return re.sub(r"[\s，,。.!！?？：:、]+", "", value).casefold()
        trigger = normalized(self.one_shot_trigger.text())
        return bool(trigger) and normalized(message) == trigger

    def _update_one_shot_audio_label(self) -> None:
        path = self.one_shot_audio_path
        self.one_shot_audio_label.setText(
            f"预设语音：{path.name}" if path is not None else "尚未选择预设语音 WAV")
        self.one_shot_audio_label.setToolTip(str(path) if path is not None else "")

    def select_one_shot_audio(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择一次性固定语音", str(self.one_shot_audio_path or VOICE_DATA.parent),
            "WAV 音频 (*.wav)")
        if selected:
            self.one_shot_audio_path = Path(selected)
            self._update_one_shot_audio_label()
            if self.one_shot_enabled.isChecked():
                self._one_shot_toggled(True)

    def _one_shot_audio_bytes(self) -> bytes:
        path = self.one_shot_audio_path
        if path is None or not path.is_file() or path.suffix.lower() != ".wav":
            raise ValueError("请选择已有的 WAV 语音文件")
        if path.stat().st_size > 20_000_000:
            raise ValueError("预设语音文件不能超过 20 MB")
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE" or source.getnframes() <= 0:
                raise ValueError("预设语音必须是有效的 PCM WAV")
        return path.read_bytes()

    def _one_shot_toggled(self, enabled: bool) -> None:
        if not enabled:
            return
        try:
            if not self.one_shot_trigger.text().strip():
                raise ValueError("请先填写触发短语")
            self._one_shot_audio_bytes()
        except (OSError, ValueError, wave.Error) as error:
            self.one_shot_enabled.setChecked(False)
            self.status_label.setText(f"无法启用一次性语音：{error}")
            return

    def play_one_shot_audio(self) -> None:
        # Disarm before the staged demo. No model, history, or memory request.
        self.one_shot_enabled.setChecked(False)
        try:
            audio = self._one_shot_audio_bytes()
        except (OSError, ValueError, wave.Error) as error:
            self.status_label.setText(f"一次性语音播放失败：{error}")
            return
        path = self.one_shot_audio_path
        transcript_path = path.with_suffix(".txt")
        try:
            transcript = transcript_path.read_text(encoding="utf-8").strip()[:2000] if transcript_path.is_file() else ""
        except OSError:
            transcript = ""
        self.turn_serial += 1
        turn = self.turn_serial
        self.active_turn = turn
        self.pending_message = self.one_shot_trigger.text().strip()
        self.last_failed_message = ""
        self.retry_failed_button.setEnabled(False)
        self.set_interaction_state(InteractionState.THINKING)
        self.status_label.setText("正在理解你的问题…")
        log_event("语音", "预设测试", f"已识别临时触发短语 · {path.name} · 模拟思考，不调用模型")
        QTimer.singleShot(ONE_SHOT_WAIT_START_MS, lambda t=turn: self._one_shot_waiting(t))
        QTimer.singleShot(ONE_SHOT_REPLY_READY_MS, lambda t=turn: self._one_shot_thinking(t))
        QTimer.singleShot(ONE_SHOT_SPEECH_PREPARE_MS, lambda t=turn: self._one_shot_preparing(t))
        QTimer.singleShot(ONE_SHOT_PLAY_MS, lambda t=turn, data=audio, words=transcript, file=path:
                          self._one_shot_start_playback(t, data, words, file))

    def _one_shot_waiting(self, turn: int) -> None:
        if turn == self.active_turn and self.interaction_state == InteractionState.THINKING:
            self.status_label.setText("正在等待模型回复…")

    def _one_shot_thinking(self, turn: int) -> None:
        if turn == self.active_turn and self.interaction_state == InteractionState.THINKING:
            self.status_label.setText("正在组织回复…")

    def _one_shot_preparing(self, turn: int) -> None:
        if turn == self.active_turn and self.interaction_state == InteractionState.THINKING:
            self.status_label.setText("正在生成专属语音…")

    def _one_shot_segment_boundaries(self, path: Path) -> list[int]:
        metadata = path.with_suffix(".segments.json")
        try:
            segments = json.loads(metadata.read_text(encoding="utf-8"))["segments"]
            if not isinstance(segments, list) or not 1 <= len(segments) <= 50:
                return []
            elapsed = 0
            boundaries = []
            for segment in segments[:-1]:
                duration = segment["duration_ms"]
                pause = segment["pause_after_ms"]
                if (type(duration) is not int or type(pause) is not int
                        or not 100 <= duration <= 180_000 or not 0 <= pause <= 10_000):
                    return []
                elapsed += duration + pause
                boundaries.append(elapsed)
            return boundaries
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def _one_shot_start_playback(self, turn: int, audio: bytes,
                                 transcript: str, path: Path) -> None:
        if turn != self.active_turn or self.interaction_state != InteractionState.THINKING:
            return
        if self.speech_started_at is not None:
            self.active_turn = None
            self.pending_message = ""
            self.set_interaction_state(InteractionState.LISTENING)
            self.status_label.setText("你正在说话 · 已忽略刚收到的旧回复")
            return
        self.active_turn = None
        self.pending_message = ""
        self.spoken_answer = transcript
        self.result_edit.appendPlainText(f"庄方宜：{transcript or path.name}\n")
        self.one_shot_boundaries = self._one_shot_segment_boundaries(path)
        log_event("语音", "预设播放", f"开始播放 {len(self.one_shot_boundaries) + 1} 段 · {path.name}")
        self.one_shot_segment = 0
        self.one_shot_playing = True
        self.custom_tts_active = True
        self.tts_pending = True
        self.tts_chunks.clear()
        self.set_interaction_state(InteractionState.SPEAKING)
        self._play_custom_audio(audio)
        self._one_shot_position_changed(0)

    def _one_shot_position_changed(self, position: int) -> None:
        if not self.one_shot_playing or self.interaction_state != InteractionState.SPEAKING:
            return
        segment = bisect_right(self.one_shot_boundaries, position) + 1
        if segment != self.one_shot_segment:
            self.one_shot_segment = segment
            self.status_label.setText(
                f"庄方宜正在播放第 {segment}/{len(self.one_shot_boundaries) + 1} 段语音")

    def _update_one_shot_position(self) -> None:
        self._one_shot_position_changed(int((time.monotonic() - self.one_shot_playback_started_at) * 1000))

    def begin_chat_turn(self, message: str) -> None:
        if self.continuous_worker is None or self.interaction_state != InteractionState.LISTENING:
            return
        settings = load_settings()
        key = load_key(settings.provider)
        try:
            if not can_attempt_stage("reply", settings, key):
                self.status_label.setText("回复阶段没有可用的 API 密钥或本地服务，请配置模型路由")
                return
        except (OSError, ValueError, TypeError) as error:
            self.status_label.setText(f"模型路由配置有误：{error}")
            return
        self.turn_serial += 1
        turn = self.turn_serial
        self.active_turn = turn
        self.pending_message = message
        self.last_failed_message = ""
        self.retry_failed_button.setEnabled(False)
        self.speech_started_at = None
        self.set_interaction_state(InteractionState.THINKING)
        from zhuangfy.core.recent_context import desktop_history
        worker = ChatWorker(settings, key, desktop_history(self.chat_history), message, self,
                            emotion=self.dialogue.emotion, context_notes=self.dialogue.prompt(), source="语音")
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
        memory_enabled = bool(worker and worker.settings.memory_enabled
                              and legacy_memory_allowed("desktop"))
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
        if self.context_store:
            try:
                self.context_store.save("desktop:voice", self.dialogue)
            except Exception as error:
                print(f"语音上下文保存失败：{type(error).__name__}")
        self.active_turn = None
        self.chat_worker = None
        self.pending_message = ""
        self.result_edit.appendPlainText(f"庄方宜：{reply}\n")
        log_event("语音", "回复", reply,
                  turn_id=getattr(worker, "turn_id", ""), channel="语音")
        if worker is not None and not getattr(worker, "memory_command", False):
            try:
                from zhuangfy.core.recent_context import record_desktop_turn
                record_desktop_turn("voice", answered_message, reply,
                                    getattr(worker, "turn_id", f"voice:{turn}"))
            except (OSError, ValueError) as error:
                log_event("短期上下文", "写入失败", type(error).__name__)
        # UI acceptance/display is the delivery evidence for desktop text.
        # A discarded/stale LLM reply never reaches this write path.
        if (worker is not None and not getattr(worker, "memory_command", False)
                and getattr(worker, "remote_memory_ref", None) is not None
                and getattr(worker, "remote_client", None) is not None):
            remote_writer = RemoteMemoryWriteWorker(
                worker.remote_client, worker.remote_identity, worker.remote_memory_ref,
                worker.turn_id, reply, self)
            self.memory_workers.add(remote_writer)
            remote_writer.failed.connect(
                lambda warning: self.result_edit.appendPlainText(f"系统：{warning}\n"))
            remote_writer.finished.connect(lambda w=remote_writer: self.memory_write_finished(w))
            remote_writer.start()
        if memory_enabled and not getattr(worker, "memory_command", False):
            writer = MemoryWriteWorker(answered_message, reply, self)
            self.memory_workers.add(writer)
            writer.failed.connect(lambda warning: self.result_edit.appendPlainText(f"系统：{warning}\n"))
            writer.finished.connect(lambda w=writer: self.memory_write_finished(w))
            writer.start()
        if (worker and not getattr(worker, "memory_command", False)
                and legacy_memory_allowed("desktop")):
            owner = load_link()
            if owner is not None:
                writer = OwnerMemoryWriteWorker(owner, answered_message, reply,
                                                worker.settings, worker.key, self)
                self.owner_memory_workers.add(writer)
                writer.failed.connect(lambda warning: self.result_edit.appendPlainText(f"系统：{warning}\n"))
                writer.finished.connect(lambda w=writer: self.owner_memory_write_finished(w))
                writer.start()
        speech_settings = load_speech_settings()
        self.custom_tts_active = speech_settings.engine == "gpt_sovits"
        if self.speak_reply.isChecked() and (self.tts is not None or self.custom_tts_active):
            self.tts_chunks = speech_chunks(reply, limit=42 if self.barge_in_mode.currentData() == "speakers" else 90)
            log_event("语音", "播报", f"准备播放 {len(self.tts_chunks)} 段语音")
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
            if self.custom_tts_active:
                self._request_custom_tts(self.current_tts_chunk)
                self.status_label.setText("正在生成专属语音…")
            elif self.tts is not None:
                self.tts.say(self.current_tts_chunk)
                QTimer.singleShot(150, self.check_tts_state)
            else:
                raise RuntimeError("没有可用的语音引擎")
        except Exception as error:
            self.tts_pending = False
            self.result_edit.appendPlainText(f"系统：语音播报失败：{error}\n")
            self.finish_chat_turn()

    def _request_custom_tts(self, chunk: str) -> None:
        settings = load_speech_settings()
        request = QNetworkRequest(QUrl(settings.endpoint.rstrip("/") + "/tts"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(25_000)
        reply = self.custom_tts_network.post(
            request, QByteArray(json.dumps(settings.payload(chunk), ensure_ascii=False).encode("utf-8")))
        self.custom_tts_request = reply
        self.custom_tts_request_text = chunk
        self.custom_tts_request_started_at = time.monotonic()
        self.custom_tts_wait_timer.start()
        log_event("语音", "合成请求", f"{len(chunk)} 字")
        reply.finished.connect(lambda r=reply: self.custom_tts_received(r))

    def _update_custom_tts_wait_status(self) -> None:
        if (self.custom_tts_request is not None and not self.custom_tts_playing
                and self.tts_pending and self.interaction_state == InteractionState.SPEAKING):
            waited = int(time.monotonic() - self.custom_tts_request_started_at)
            self.status_label.setText(f"正在等待专属语音 · {waited} 秒")

    def _prefetch_custom_tts(self) -> None:
        if not self.custom_tts_active or not self.tts_chunks or self.custom_tts_request is not None:
            return
        chunk = self.tts_chunks.pop(0)
        try:
            self._request_custom_tts(chunk)
        except Exception:
            self.tts_chunks.insert(0, chunk)
            self.custom_tts_prefetch_failed = True

    def custom_tts_received(self, reply) -> None:
        if reply is not self.custom_tts_request:
            reply.deleteLater()
            return
        self.custom_tts_request = None
        self.custom_tts_wait_timer.stop()
        chunk = self.custom_tts_request_text
        self.custom_tts_request_text = ""
        elapsed = time.monotonic() - self.custom_tts_request_started_at
        self.custom_tts_request_started_at = 0.0
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        data = bytes(reply.readAll())
        error = reply.error()
        error_text = reply.errorString() if hasattr(reply, "errorString") else ""
        reply.deleteLater()
        if not self.tts_pending or self.interaction_state != InteractionState.SPEAKING:
            return
        if error != QNetworkReply.NetworkError.NoError or status != 200 or len(data) > 20_000_000 or not data.startswith(b"RIFF"):
            detail = data[:300].decode("utf-8", errors="replace").strip() if status != 200 else error_text
            log_event("语音", "合成失败", f"{elapsed:.1f} 秒 · HTTP {status or '无响应'} · {detail[:240]}")
            if self.custom_tts_playing:
                self.tts_chunks.insert(0, chunk)
                self.custom_tts_prefetch_failed = True
            else:
                self.current_tts_chunk = chunk
                self._custom_tts_fallback("专属语音服务暂不可用，已切换系统语音")
            return
        log_event("语音", "合成完成", f"{len(chunk)} 字 · {elapsed:.1f} 秒")
        if self.custom_tts_playing:
            self.custom_tts_ready_audio = data
            self.custom_tts_ready_text = chunk
        else:
            self.current_tts_chunk = chunk
            self._play_custom_audio(data)

    def _play_custom_audio(self, data: bytes) -> None:
        self._play_custom_audio_portaudio(data)

    def _play_custom_audio_portaudio(self, data: bytes) -> None:
        self.custom_playback_generation += 1
        generation = self.custom_playback_generation
        stop = threading.Event()
        self.custom_playback_stop = stop
        volume = self.custom_tts_audio.volume()
        self.custom_tts_playing = True
        if self.one_shot_playing:
            self.one_shot_playback_started_at = time.monotonic()
            self.one_shot_position_timer.start()
        else:
            self.status_label.setText(f"庄方宜正在播报 · 剩余 {len(self.tts_chunks)} 段")

        def play() -> None:
            try:
                with wave.open(io.BytesIO(data), "rb") as source:
                    if (source.getcomptype() != "NONE" or source.getsampwidth() != 2
                            or source.getnchannels() not in (1, 2)
                            or not 8000 <= source.getframerate() <= 96000):
                        raise ValueError("语音文件不是受支持的 PCM WAV")
                    with sd.RawOutputStream(samplerate=source.getframerate(),
                                            channels=source.getnchannels(), dtype="int16") as output:
                        while not stop.is_set():
                            block = source.readframes(4096)
                            if not block:
                                break
                            if volume <= 0:
                                block = bytes(len(block))
                            elif volume < 1:
                                samples = np.frombuffer(block, dtype="<i2").astype(np.float32)
                                block = np.clip(samples * volume, -32768, 32767).astype("<i2").tobytes()
                            output.write(block)
                if not stop.is_set():
                    self.custom_playback_finished.emit(generation)
            except Exception as error:
                if not stop.is_set():
                    self.custom_playback_failed.emit(generation, str(error))

        threading.Thread(target=play, name="zhuangfy-tts-playback", daemon=True).start()
        self._prefetch_custom_tts()

    @Slot(int)
    def _custom_playback_finished(self, generation: int) -> None:
        if generation == self.custom_playback_generation and self.custom_tts_playing:
            self.custom_playback_stop = None
            if self.one_shot_playing:
                self.one_shot_position_timer.stop()
                log_event("语音", "预设完成", "一次性固定语音播放完成")
            self._advance_custom_tts()

    @Slot(int, str)
    def _custom_playback_failed(self, generation: int, error: str) -> None:
        if generation != self.custom_playback_generation or not self.custom_tts_playing:
            return
        log_event("语音", "播放失败", error)
        if self.one_shot_playing:
            self._stop_custom_audio()
            self.result_edit.appendPlainText("系统：预设语音文件无法播放\n")
            self.finish_chat_turn()
        else:
            self._custom_tts_fallback("专属语音播放失败，已切换系统语音")

    def _advance_custom_tts(self) -> None:
        self.custom_tts_playing = False
        if self.custom_tts_ready_audio is not None:
            data = self.custom_tts_ready_audio
            self.current_tts_chunk = self.custom_tts_ready_text
            self.custom_tts_ready_audio = None
            self.custom_tts_ready_text = ""
            self._play_custom_audio(data)
        elif self.custom_tts_prefetch_failed:
            self._custom_tts_fallback("专属语音服务暂不可用，已切换系统语音", replay_current=False)
        elif self.custom_tts_request is not None:
            self.status_label.setText("正在等待下一段专属语音…")
        elif self.tts_chunks:
            self.speak_next_chunk()
        else:
            self.finish_chat_turn()

    def _custom_tts_fallback(self, message: str, replay_current: bool = True) -> None:
        self.custom_tts_active = False
        self._stop_custom_audio()
        self.result_edit.appendPlainText(f"系统：{message}\n")
        if self.tts is None:
            self.tts_pending = False
            self.finish_chat_turn()
            return
        if replay_current:
            self.tts.say(self.current_tts_chunk)
            QTimer.singleShot(150, self.check_tts_state)
        else:
            self.speak_next_chunk()

    def _advance_tts(self) -> None:
        if not self.tts_advance_pending:
            self.tts_advance_pending = True
            delay = 1100 if self.barge_in_mode.currentData() == "speakers" and self.tts_chunks else 0
            QTimer.singleShot(delay, self.speak_next_chunk)

    def _stop_custom_audio(self) -> None:
        if self.custom_playback_stop is not None:
            self.custom_playback_stop.set()
            self.custom_playback_stop = None
        self.custom_playback_generation += 1
        self.one_shot_position_timer.stop()
        self.one_shot_playing = False
        self.one_shot_boundaries = []
        self.one_shot_segment = 0
        reply = self.custom_tts_request
        self.custom_tts_request = None
        self.custom_tts_wait_timer.stop()
        self.custom_tts_request_text = ""
        self.custom_tts_request_started_at = 0.0
        self.custom_tts_ready_audio = None
        self.custom_tts_ready_text = ""
        self.custom_tts_playing = False
        self.custom_tts_prefetch_failed = False
        if reply is not None:
            reply.abort()

    @Slot()
    def retry_failed_turn(self) -> None:
        if (self.last_failed_message and self.continuous_worker is not None
                and self.interaction_state == InteractionState.LISTENING):
            self.begin_chat_turn(self.last_failed_message)

    def chat_failed(self, turn: int, error: str) -> None:
        if turn != self.active_turn:
            return
        # Keep the ASR transcript so the user does not need to speak again.
        self.last_failed_message = self.pending_message
        self.retry_failed_button.setEnabled(bool(self.last_failed_message))
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

    def owner_memory_write_finished(self, worker: OwnerMemoryWriteWorker) -> None:
        self.owner_memory_workers.discard(worker)
        worker.deleteLater()

    @Slot()
    def check_tts_state(self) -> None:
        if not self.custom_tts_active and self.tts_pending and self.interaction_state == InteractionState.SPEAKING and self.tts is not None and self.tts.state() in (QTextToSpeech.State.Ready, QTextToSpeech.State.Error):
            self.tts_state_changed(self.tts.state())

    @Slot(QTextToSpeech.State)
    def tts_state_changed(self, state) -> None:
        if self.custom_tts_active or not self.tts_pending or self.interaction_state != InteractionState.SPEAKING:
            return
        if state in (QTextToSpeech.State.Ready, QTextToSpeech.State.Error):
            if state == QTextToSpeech.State.Error and hasattr(self, "status_label"):
                self.tts_pending = False
                self.result_edit.appendPlainText("系统：语音播报失败；文字回复仍可查看\n")
                self.status_label.setText("语音播报失败；文字回复仍可查看")
                self.finish_chat_turn()
            else:
                self._advance_tts()

    @Slot()
    def interrupt_reply(self) -> None:
        if self.interaction_state in (InteractionState.THINKING, InteractionState.SPEAKING):
            self.voice_turn_timer.stop()
            self.voice_turn_manager.interrupt()
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
            self._stop_custom_audio()
        else:
            return
        self.set_interaction_state(InteractionState.LISTENING)
        if self.gate is not None:
            self.gate.touch()
        self.status_label.setText("当前回复已打断 · 正在听你说话")

    def finish_chat_turn(self) -> None:
        if self.interaction_state == InteractionState.SPEAKING:
            self.echo_guard_until = time.monotonic() + 0.8
        self.one_shot_playing = False
        self.one_shot_boundaries = []
        self.one_shot_segment = 0
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
        self.voice_turn_timer.stop()
        self.voice_turn_manager.interrupt()
        self.active_turn = None
        self.chat_worker = None
        self.tts_pending = False
        self.tts_advance_pending = False
        self.tts_chunks.clear()
        self.pending_message = ""
        self.speech_started_during_playback = False
        if self.tts is not None:
            self.tts.stop()
        self._stop_custom_audio()
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
        self.voice_turn_timer.stop()
        self.voice_turn_manager.interrupt()
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
        log_event("语音", "识别", f"{language or '未知语言'} · {text or '未识别到文字'}")
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
        self.last_failed_message = ""
        self.retry_failed_button.setEnabled(False)
        self.dialogue = DialogueContext()
        self.chat_history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        if self.context_store:
            try:
                self.context_store.delete("desktop:voice")
            except Exception as error:
                print(f"语音上下文清理失败：{type(error).__name__}")
        self.result_edit.clear()
        self.copy_button.setEnabled(False)
        self.status_label.setText("对话历史已清空")

    def closeEvent(self, event) -> None:
        if self.hide_on_close:
            event.ignore()
            self.hide()
            return
        self.one_shot_enabled.setChecked(False)
        self.wake_timer.stop()
        self.active_turn = None
        self.tts_pending = False
        if self.tts is not None:
            self.tts.stop()
        self._stop_custom_audio()
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
        for worker in tuple(self.owner_memory_workers):
            if worker.isRunning():
                worker.wait()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
