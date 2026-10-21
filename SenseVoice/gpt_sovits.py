"""Local GPT-SoVITS v2Pro speech settings and request validation."""

from __future__ import annotations

import json
import os
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from llm import ROOT
from zhuangfy.paths import Paths


SETTINGS_FILE = Paths.current().config_file("gpt_sovits_settings.json")
ENGINE_ROOT = Paths.current().gpt_sovits_root
API_PORT = 9880
MODEL_CONFIG = ENGINE_ROOT / "GPT_SoVITS" / "configs" / "shimano_v2pro.yaml"
VOICE_DATA = Paths.current().voice_training_root / "prepared"


@dataclass(frozen=True)
class VoiceModel:
    model_id: str
    label: str
    gpt_weight: Path
    sovits_weight: Path
    reference_audio: str = ""
    reference_text: str = ""


def _training_epoch(path: Path, kind: str) -> tuple[str, int] | None:
    pattern = r"(.+)-e(\d+)\.ckpt" if kind == "gpt" else r"(.+)_e(\d+)_s\d+\.pth"
    match = re.fullmatch(pattern, path.name)
    return (match.group(1), int(match.group(2))) if match else None


def available_models(engine_root: Path = ENGINE_ROOT, voice_data: Path = VOICE_DATA) -> list[VoiceModel]:
    models = []
    pretrained = engine_root / "GPT_SoVITS" / "pretrained_models"
    base_gpt = pretrained / "s1v3.ckpt"
    base_sovits = pretrained / "v2Pro" / "s2Gv2Pro.pth"
    if base_gpt.is_file() and base_sovits.is_file():
        models.append(VoiceModel("v2pro_base", "官方 v2Pro 基础模型", base_gpt, base_sovits))
    pairs: dict[str, dict[str, tuple[int, Path]]] = {}
    for kind, folder, extension in (
        ("gpt", engine_root / "GPT_weights_v2Pro", "*.ckpt"),
        ("sovits", engine_root / "SoVITS_weights_v2Pro", "*.pth"),
    ):
        for path in folder.glob(extension):
            parsed = _training_epoch(path, kind)
            if parsed:
                name, epoch = parsed
                current = pairs.setdefault(name, {}).get(kind)
                if current is None or epoch > current[0]:
                    pairs[name][kind] = (epoch, path)
    for name, pair in sorted(pairs.items()):
        if "gpt" not in pair or "sovits" not in pair:
            continue
        audio, transcript = "", ""
        if name == "zhuangfangyi_v2pro":
            filename = "chr_0030_zhuangfy_chrbark_weap_01.wav"
            path = voice_data / "wav" / filename
            manifest = voice_data / "manifest.csv"
            if path.is_file() and manifest.is_file():
                with manifest.open(encoding="utf-8-sig", newline="") as stream:
                    row = next((row for row in csv.DictReader(stream)
                                if row["filename"] == filename.replace(".wav", ".mp3")), None)
                if row:
                    audio, transcript = str(path), row["transcript"]
        gpt_epoch, gpt_path = pair["gpt"]
        sovits_epoch, sovits_path = pair["sovits"]
        label = (f"庄方宜 · 已训练（GPT {gpt_epoch} / SoVITS {sovits_epoch} 轮）"
                 if name == "zhuangfangyi_v2pro" else
                 f"{name}（GPT {gpt_epoch} / SoVITS {sovits_epoch} 轮）")
        models.append(VoiceModel(name, label, gpt_path, sovits_path, audio, transcript))
    return models


def save_model_config(model: VoiceModel, path: Path = MODEL_CONFIG) -> None:
    if not model.gpt_weight.is_file() or not model.sovits_weight.is_file():
        raise ValueError("所选模型的 GPT 或 SoVITS 权重文件不存在")
    source = path.read_text(encoding="utf-8")
    head, marker, tail = source.partition("\nv1:")
    if not marker or not head.startswith("custom:"):
        raise ValueError("GPT-SoVITS 配置格式无法识别")
    for key, value in (("t2s_weights_path", model.gpt_weight),
                       ("vits_weights_path", model.sovits_weight)):
        replacement = f"  {key}: {json.dumps(str(value))}"
        updated, count = re.subn(rf"(?m)^  {key}:.*$", lambda _match: replacement, head)
        if count != 1:
            raise ValueError(f"GPT-SoVITS 配置缺少 {key}")
        head = updated
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(head + marker + tail, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SpeechSettings:
    engine: str = "system"
    endpoint: str = "http://127.0.0.1:9880"
    reference_audio: str = ""
    reference_text: str = ""
    model_id: str = ""

    def validated(self) -> "SpeechSettings":
        if self.engine not in {"system", "gpt_sovits"}:
            raise ValueError("未知的语音引擎")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != API_PORT:
            raise ValueError("GPT-SoVITS 只允许连接本机 9880 端口")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("语音服务地址格式无效")
        if len(self.reference_audio) > 1000 or len(self.reference_text) > 500:
            raise ValueError("参考音频路径或台词过长")
        if len(self.model_id) > 100 or (self.model_id and not re.fullmatch(r"[A-Za-z0-9_-]+", self.model_id)):
            raise ValueError("语音模型 ID 无效")
        if self.engine == "gpt_sovits":
            path = Path(self.reference_audio)
            if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in {".wav", ".mp3", ".flac"}:
                raise ValueError("请选择本机已有的 WAV、MP3 或 FLAC 参考音频")
            if not self.reference_text.strip():
                raise ValueError("请填写参考音频中的准确台词")
        return self

    def payload(self, text: str) -> dict:
        self.validated()
        if self.engine != "gpt_sovits" or not text.strip():
            raise ValueError("无法生成空白语音请求")
        return {
            "text": text.strip(),
            "text_lang": "zh",
            "ref_audio_path": self.reference_audio,
            "prompt_lang": "zh",
            "prompt_text": self.reference_text.strip(),
            "text_split_method": "cut0",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
        }


def load_settings(path: Path = SETTINGS_FILE) -> SpeechSettings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return SpeechSettings()
        return SpeechSettings(str(raw.get("engine", "system")),
                              str(raw.get("endpoint", "http://127.0.0.1:9880")),
                              str(raw.get("reference_audio", "")),
                              str(raw.get("reference_text", "")),
                              str(raw.get("model_id", ""))).validated()
    except (OSError, ValueError, TypeError):
        return SpeechSettings()


def save_settings(settings: SpeechSettings, path: Path = SETTINGS_FILE) -> None:
    settings.validated()
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(settings.__dict__, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
