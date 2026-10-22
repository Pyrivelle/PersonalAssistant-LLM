"""Incremental Silero VAD for the continuous listening exercise."""

from __future__ import annotations

import numpy as np
import sherpa_onnx

from .sensevoice import VAD_MODEL


SAMPLE_RATE = 16000
PREROLL_SAMPLES = int(0.25 * SAMPLE_RATE)
MAX_BUFFER_SAMPLES = 20 * SAMPLE_RATE


class StreamingVad:
    """Accept microphone frames and return completed speech segments."""

    def __init__(self, silence_seconds: float = 0.8) -> None:
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(VAD_MODEL)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = silence_seconds
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.max_speech_duration = 15
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        self.detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=21)
        self.window = config.silero_vad.window_size
        self.pending = np.empty(0, dtype=np.float32)
        self.raw = np.empty(0, dtype=np.float32)
        self.raw_start = 0
        self.total_samples = 0

    def accept(self, samples: np.ndarray) -> list[np.ndarray]:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not len(samples):
            return []
        self.raw = np.concatenate((self.raw, samples))
        self.total_samples += len(samples)
        self.pending = np.concatenate((self.pending, samples))
        completed: list[np.ndarray] = []
        while len(self.pending) >= self.window:
            self.detector.accept_waveform(self.pending[: self.window])
            self.pending = self.pending[self.window :]
            completed.extend(self._take_completed())
        if len(self.raw) > MAX_BUFFER_SAMPLES:
            trim = len(self.raw) - MAX_BUFFER_SAMPLES
            self.raw = self.raw[trim:]
            self.raw_start += trim
        return completed

    def finish(self) -> list[np.ndarray]:
        if len(self.pending):
            padded = np.pad(self.pending, (0, self.window - len(self.pending)))
            self.detector.accept_waveform(padded)
            self.pending = np.empty(0, dtype=np.float32)
        self.detector.flush()
        return self._take_completed()

    def is_speech_detected(self) -> bool:
        return self.detector.is_speech_detected()

    def _take_completed(self) -> list[np.ndarray]:
        completed: list[np.ndarray] = []
        while not self.detector.empty():
            segment = self.detector.front
            start = max(self.raw_start, segment.start - PREROLL_SAMPLES)
            end = min(self.total_samples, segment.start + len(segment.samples) + PREROLL_SAMPLES)
            if start < end:
                completed.append(self.raw[start - self.raw_start : end - self.raw_start].copy())
            else:
                completed.append(np.asarray(segment.samples, dtype=np.float32).copy())
            self.detector.pop()
        return completed
