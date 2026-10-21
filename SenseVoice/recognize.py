"""Local SenseVoice speech recognition and audio decoding."""

from __future__ import annotations

import argparse
import re
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

from _package_bootstrap import ensure_src_path

ensure_src_path()

from zhuangfy.paths import Paths


ROOT = Path(__file__).resolve().parent
MODEL = Paths.current().models_root / "model.int8.onnx"
TOKENS = Paths.current().models_root / "tokens.txt"
VAD_MODEL = Paths.current().models_root / "silero_vad.onnx"
TAGS = re.compile(r"<\|[^|<>]*\|>")


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as audio:
        if (
            audio.getframerate() != 16000
            or audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getcomptype() != "NONE"
        ):
            raise ValueError("音频须为 16 kHz、单声道、16-bit PCM WAV")
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    return samples.astype(np.float32) / 32768.0


def list_devices() -> None:
    import sounddevice as sd

    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            default = "（默认）" if index == sd.default.device[0] else ""
            print(f"{index}: {device['name']} {default}")


def record(seconds: float, device: int | None) -> np.ndarray:
    import sounddevice as sd

    info = sd.query_devices(device, "input")
    sample_rate = 16000
    sd.check_input_settings(device=device, channels=1, samplerate=sample_rate, dtype="float32")
    print(f"正在录音 {seconds:g} 秒：{info['name']}，请开始说话…", flush=True)
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return audio[:, 0]


def speech_spans(samples: np.ndarray) -> list[tuple[int, int]]:
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(VAD_MODEL)
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.25
    config.silero_vad.min_speech_duration = 0.15
    config.silero_vad.max_speech_duration = 15
    config.sample_rate = 16000
    config.num_threads = 1
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=65)
    window = config.silero_vad.window_size
    for offset in range(0, len(samples), window):
        chunk = samples[offset : offset + window]
        if len(chunk) < window:
            chunk = np.pad(chunk, (0, window - len(chunk)))
        vad.accept_waveform(chunk)
    vad.flush()

    spans: list[tuple[int, int]] = []
    while not vad.empty():
        segment = vad.front
        start = max(0, segment.start - 4000)
        end = min(len(samples), segment.start + len(segment.samples) + 4000)
        if spans and start - spans[-1][1] <= 16000 and end - spans[-1][0] <= 15 * 16000:
            spans[-1] = (spans[-1][0], end)
        else:
            if spans and start < spans[-1][1]:
                boundary = (start + spans[-1][1]) // 2
                spans[-1] = (spans[-1][0], boundary)
                start = boundary
            spans.append((start, end))
        vad.pop()
    return spans


def recognize(samples: np.ndarray, language: str) -> tuple[str, str]:
    if not 1 <= len(samples) <= 60 * 16000:
        raise ValueError("音频长度须在 0 到 60 秒之间")
    spans = speech_spans(samples)
    if not spans:
        raise ValueError("没有检测到人声；请检查麦克风和录音设备")

    engine = create_recognizer(language)
    texts: list[str] = []
    detected = ""
    for start, end in spans:
        text, detected_language = decode_segment(engine, samples[start:end])
        if text:
            texts.append(text)
        detected = detected_language or detected
    return " ".join(texts), language if language != "auto" else detected


def create_recognizer(language: str):
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(MODEL),
        tokens=str(TOKENS),
        num_threads=2,
        use_itn=True,
        language="" if language == "auto" else language,
        provider="cpu",
    )


def decode_segment(engine, samples: np.ndarray) -> tuple[str, str]:
    stream = engine.create_stream()
    stream.accept_waveform(16000, samples)
    engine.decode_stream(stream)
    text = TAGS.sub("", stream.result.text).strip()
    detected = str(getattr(stream.result, "lang", "") or "").replace("<|", "").replace("|>", "")
    return text, detected


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 SenseVoice 语音识别")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--list-devices", action="store_true", help="列出麦克风设备")
    source.add_argument("--record", action="store_true", help="从麦克风录音")
    source.add_argument("--file", type=Path, help="识别 16 kHz 单声道 PCM WAV 文件")
    parser.add_argument("--seconds", type=float, default=6, help="录音秒数，默认 6")
    parser.add_argument("--device", type=int, help="麦克风设备编号，默认使用系统设备")
    parser.add_argument("--language", choices=("auto", "zh", "yue", "en", "ja", "ko"), default="zh")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return
    if args.record and not 0 < args.seconds <= 60:
        parser.error("--seconds 须大于 0 且不超过 60")
    for path in (MODEL, TOKENS, VAD_MODEL):
        if not path.is_file():
            parser.error(f"缺少模型文件：{path}")

    samples = record(args.seconds, args.device) if args.record else read_wav(args.file)
    text, language = recognize(samples, args.language)
    print(f"识别结果：{text}")
    print(f"语言：{language or '未知'}")


if __name__ == "__main__":
    main()
