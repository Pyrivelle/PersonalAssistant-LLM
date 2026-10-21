"""Run the official GPT-SoVITS v2Pro dataset preparation and fine-tuning scripts.

Usage (from the repository's SenseVoice folder)::

    C:\\CProj\\Airi\\GPT-SoVITS\\.venv\\Scripts\\python.exe tools/train_zhuangfy_v2pro.py prepare
    C:\\CProj\\Airi\\GPT-SoVITS\\.venv\\Scripts\\python.exe tools/train_zhuangfy_v2pro.py train

All generated data and weights stay outside this Git repository.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
import imageio_ffmpeg


ROOT = Path(r"C:\CProj\Airi")
ENGINE = ROOT / "GPT-SoVITS"
DATA = ROOT / "zhaungfyvoice" / "prepared"
EXPERIMENT = ENGINE / "logs" / "zhuangfangyi_v2pro"
PRETRAINED = ENGINE / "GPT_SoVITS" / "pretrained_models"
NAME = "zhuangfangyi_v2pro"


def run(script: str, variables: dict[str, str], log_name: str, *,
        skip_if: Path | None = None, extra_args: tuple[str, ...] = ()) -> None:
    if skip_if and skip_if.exists() and skip_if.stat().st_size:
        print(f"Skip {script}: {skip_if} already exists", flush=True)
        return
    env = os.environ.copy()
    ffmpeg_dir = ENGINE / "tools" / "shimano_ffmpeg"
    ffmpeg_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_target = ffmpeg_dir / "ffmpeg.exe"
    if not ffmpeg_target.exists():
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), ffmpeg_target)
    env.update({
        "PATH": str(ffmpeg_dir) + os.pathsep + env.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join((str(ENGINE), str(ENGINE / "GPT_SoVITS"), str(ENGINE / "tools"))),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "version": "v2Pro",
        "i_part": "0",
        "all_parts": "1",
        "_CUDA_VISIBLE_DEVICES": "0",
        **variables,
    })
    EXPERIMENT.mkdir(parents=True, exist_ok=True)
    log_path = EXPERIMENT / log_name
    cmd = [sys.executable, "-u", "-s", script, *extra_args]
    print(f"Running {script}; log: {log_path}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(cmd, cwd=ENGINE, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"{script} failed (exit {result.returncode}); see {log_path}")
    print(f"Finished {script}", flush=True)


def require_count(folder: Path, pattern: str, minimum: int) -> None:
    count = sum(1 for _ in folder.glob(pattern))
    if count < minimum:
        raise RuntimeError(f"Only {count}/{minimum} files in {folder}; inspect the preparation log")


def prepare() -> None:
    count = sum(1 for line in (DATA / "training.list").read_text(encoding="utf-8").splitlines() if line)
    common = {
        "inp_text": str(DATA / "training.list"),
        "inp_wav_dir": str(DATA / "wav"),
        "exp_name": NAME,
        "opt_dir": str(EXPERIMENT),
        "is_half": "True",
    }
    run("GPT_SoVITS/prepare_datasets/1-get-text.py", {
        **common, "bert_pretrained_dir": str(PRETRAINED / "chinese-roberta-wwm-ext-large"),
    }, "prepare_text.log", skip_if=EXPERIMENT / "2-name2text.txt")
    require_count(EXPERIMENT / "3-bert", "*.pt", count)
    run("GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py", {
        **common, "cnhubert_base_dir": str(PRETRAINED / "chinese-hubert-base"),
    }, "prepare_hubert.log")
    require_count(EXPERIMENT / "4-cnhubert", "*.pt", count)
    run("GPT_SoVITS/prepare_datasets/2-get-sv.py", {
        **common, "cnhubert_base_dir": str(PRETRAINED / "chinese-hubert-base"),
        "sv_path": str(PRETRAINED / "sv" / "pretrained_eres2netv2w24s4ep4.ckpt"),
    }, "prepare_speaker.log")
    require_count(EXPERIMENT / "7-sv_cn", "*.pt", count)
    empty_semantic = EXPERIMENT / "6-name2semantic-0.tsv"
    if empty_semantic.exists() and empty_semantic.stat().st_size == 0:
        empty_semantic.unlink()
    run("GPT_SoVITS/prepare_datasets/3-get-semantic.py", {
        **common, "pretrained_s2G": str(PRETRAINED / "v2Pro" / "s2Gv2Pro.pth"),
        "s2config_path": "GPT_SoVITS/configs/s2v2Pro.json",
    }, "prepare_semantic.log", skip_if=EXPERIMENT / "6-name2semantic-0.tsv")
    semantic_lines = (EXPERIMENT / "6-name2semantic-0.tsv").read_text(encoding="utf-8").splitlines()
    if len(semantic_lines) < count:
        raise RuntimeError(f"Only {len(semantic_lines)}/{count} semantic rows")
    partial = EXPERIMENT / "2-name2text-0.txt"
    if partial.exists():
        (EXPERIMENT / "2-name2text.txt").write_text(partial.read_text(encoding="utf-8"), encoding="utf-8")
    partial = EXPERIMENT / "6-name2semantic-0.tsv"
    if partial.exists():
        contents = partial.read_text(encoding="utf-8")
        (EXPERIMENT / "6-name2semantic.tsv").write_text(
            "item_name\tsemantic_audio\n" + contents, encoding="utf-8"
        )
    print("Preparation complete", flush=True)


def train() -> None:
    required = (EXPERIMENT / "2-name2text.txt", EXPERIMENT / "6-name2semantic.tsv")
    if any(not path.exists() for path in required):
        raise RuntimeError("Run the prepare phase first")
    (EXPERIMENT / "logs_s2_v2Pro").mkdir(parents=True, exist_ok=True)
    (EXPERIMENT / "logs_s1_v2Pro").mkdir(parents=True, exist_ok=True)
    (ENGINE / "SoVITS_weights_v2Pro").mkdir(parents=True, exist_ok=True)
    (ENGINE / "GPT_weights_v2Pro").mkdir(parents=True, exist_ok=True)
    s2 = json.loads((ENGINE / "GPT_SoVITS" / "configs" / "s2v2Pro.json").read_text(encoding="utf-8"))
    s2["train"].update({
        "batch_size": 1,
        "epochs": 8,
        "pretrained_s2G": str(PRETRAINED / "v2Pro" / "s2Gv2Pro.pth"),
        "pretrained_s2D": str(PRETRAINED / "v2Pro" / "s2Dv2Pro.pth"),
        "if_save_latest": True,
        "if_save_every_weights": True,
        "save_every_epoch": 1,
        "gpu_numbers": "0",
        "text_low_lr_rate": 0.4,
        "grad_ckpt": False,
        "lora_rank": 32,
    })
    s2["model"]["version"] = "v2Pro"
    s2["data"]["exp_dir"] = str(EXPERIMENT)
    s2.update({
        "s2_ckpt_dir": str(EXPERIMENT),
        "save_weight_dir": str(ENGINE / "SoVITS_weights_v2Pro"),
        "name": NAME,
        "version": "v2Pro",
    })
    s2_path = EXPERIMENT / "shimano_s2_config.json"
    s2_path.write_text(json.dumps(s2, ensure_ascii=False, indent=2), encoding="utf-8")
    run("GPT_SoVITS/s2_train.py", {}, "train_sovits.log",
        extra_args=("--config", str(s2_path)))

    s1 = yaml.safe_load((ENGINE / "GPT_SoVITS" / "configs" / "s1longer-v2.yaml").read_text(encoding="utf-8"))
    s1["train"].update({
        "batch_size": 1,
        "epochs": 15,
        "save_every_n_epoch": 1,
        "if_save_latest": True,
        "if_save_every_weights": True,
        "if_dpo": False,
        "half_weights_save_dir": str(ENGINE / "GPT_weights_v2Pro"),
        "exp_name": NAME,
    })
    s1.update({
        "pretrained_s1": str(PRETRAINED / "s1v3.ckpt"),
        "train_semantic_path": str(EXPERIMENT / "6-name2semantic.tsv"),
        "train_phoneme_path": str(EXPERIMENT / "2-name2text.txt"),
        "output_dir": str(EXPERIMENT / "logs_s1_v2Pro"),
    })
    s1_path = EXPERIMENT / "shimano_s1_config.yaml"
    s1_path.write_text(yaml.safe_dump(s1, allow_unicode=True), encoding="utf-8")
    run("GPT_SoVITS/s1_train.py", {"hz": "25hz"}, "train_gpt.log",
        extra_args=("--config_file", str(s1_path)))
    print("Fine-tuning complete", flush=True)


def activate() -> None:
    sovits = list((ENGINE / "SoVITS_weights_v2Pro").glob(f"{NAME}*.pth"))
    gpt = list((ENGINE / "GPT_weights_v2Pro").glob(f"{NAME}*.ckpt"))
    if not sovits or not gpt:
        raise RuntimeError("Both trained SoVITS and GPT weights are required")
    sovits_weight = max(sovits, key=lambda item: item.stat().st_mtime)
    gpt_weight = max(gpt, key=lambda item: item.stat().st_mtime)
    config_path = ENGINE / "GPT_SoVITS" / "configs" / "shimano_v2pro.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["custom"].update({
        "version": "v2Pro",
        "vits_weights_path": str(sovits_weight),
        "t2s_weights_path": str(gpt_weight),
    })
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    reference_name = "chr_0030_zhuangfy_chrbark_weap_01.mp3"
    with (DATA / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        reference = next(row for row in csv.DictReader(stream) if row["filename"] == reference_name)
    wav = DATA / "wav" / reference_name.replace(".mp3", ".wav")
    settings = {
        "engine": "gpt_sovits",
        "endpoint": "http://127.0.0.1:9880",
        "reference_audio": str(wav),
        "reference_text": reference["transcript"],
    }
    settings_path = ROOT / "ZhuangFY" / "SenseVoice" / "gpt_sovits_settings.json"
    temporary = settings_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, settings_path)
    print(f"SoVITS: {sovits_weight}", flush=True)
    print(f"GPT: {gpt_weight}", flush=True)
    print(f"Reference audio: {wav}", flush=True)
    print(f"Settings: {settings_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "train", "activate", "all"))
    args = parser.parse_args()
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("train", "all"):
        train()
    if args.phase in ("activate", "all"):
        activate()
