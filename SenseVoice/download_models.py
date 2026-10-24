"""Fetch pinned speech models without adding their large files to Git."""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
from pathlib import Path

from _package_bootstrap import ensure_src_path

ensure_src_path()

from zhuangfy.paths import Paths


ROOT = Path(__file__).resolve().parent
MODEL_REVISION = "2365baeacb507f821a0c8120fcee3d484dba7a07"
MODEL_BASE = (
    "https://huggingface.co/csukuangfj/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/"
    f"{MODEL_REVISION}"
)
FILES = {
    "model.int8.onnx": (
        f"{MODEL_BASE}/model.int8.onnx",
        "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
    ),
    "tokens.txt": (
        f"{MODEL_BASE}/tokens.txt",
        "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
    ),
    "silero_vad.onnx": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    ),
}
SAMPLE = (
    f"{MODEL_BASE}/test_wavs/zh.wav",
    "b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(path: Path, url: str, expected: str | None) -> None:
    if path.exists() and (expected is None or sha256(path) == expected):
        print(f"已存在：{path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".download")
    try:
        print(f"下载：{path.relative_to(ROOT)}")
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if expected and sha256(temporary) != expected:
            raise ValueError(f"校验失败：{path.name}；请检查下载源或网络代理")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 ZhuangFY 使用的本地语音模型")
    parser.add_argument("--sample", action="store_true", help="同时下载 GUI 的中文示例音频")
    args = parser.parse_args()
    for name, (url, digest) in FILES.items():
        fetch(Paths.current().models_root / name, url, digest)
    if args.sample:
        fetch(Paths.current().data_root / "samples" / "zh.wav", *SAMPLE)
    print("语音资源已就绪")


if __name__ == "__main__":
    main()
