"""Select and launch one of two independent QQ conversation engines.

This is a process-level switch, NOT an in-process Maisaka import or a shared
Persona/Memory/AssetStore bridge. The default remains original ZhuangFY.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "chat_engine_settings.json"
LOCK = ROOT / ".chat_engine_switch.lock"
MAIBOT_LAUNCHER = ROOT / "scripts" / "maisaka_native_experiment.py"
QQ_BRIDGE = ROOT / "SenseVoice" / "qq_bridge.py"
ENGINES = ("zhuangfy", "maibot")


def read_config(path: Path = CONFIG) -> dict[str, str]:
    if not path.is_file():
        return {"engine": "zhuangfy", "maibot_python": ""}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(raw, dict)
            or raw.get("engine") not in ENGINES
            or not isinstance(raw.get("maibot_python", ""), str)):
        raise ValueError("chat_engine_settings.json 配置错误")
    return {"engine": raw["engine"], "maibot_python": raw.get("maibot_python", "")}


def save_config(engine: str, *, maibot_python: str = "", path: Path = CONFIG) -> None:
    if engine not in ENGINES:
        raise ValueError("unknown engine")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Configuration stores no tokens or personal messages.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"engine": engine, "maibot_python": maibot_python},
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def maibot_interpreter(setting: str, root: Path = ROOT) -> Path:
    if setting:
        p = Path(setting).expanduser()
        return (root / p).resolve() if not p.is_absolute() else p.resolve()
    folder = root / "third_party" / "MaiBot" / ".venv"
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def build_command(engine: str, *, root: Path = ROOT, python: Path | None = None,
                  maibot_python: str = "", confirmed: bool = False) -> list[str]:
    python = python or Path(sys.executable)
    if engine == "zhuangfy":
        if not (root / "SenseVoice" / "qq_bridge.py").is_file():
            raise FileNotFoundError("ZhuangFY QQ Bridge 未找到")
        return [str(python), str(root / "SenseVoice" / "qq_bridge.py")]
    if engine == "maibot":
        exe = maibot_interpreter(maibot_python, root)
        if not exe.is_file():
            raise FileNotFoundError(f"MaiBot Python 环境不存在：{exe}；先按实验文档安装")
        cmd = [str(python), str(root / "scripts" / "maisaka_native_experiment.py"),
               "run", "--python", str(exe)]
        if confirmed:
            cmd.append("--confirm-exclusive-channel")
        return cmd
    raise ValueError("unknown engine")


@contextmanager
def exclusive_launcher(path: Path = LOCK) -> Iterator[None]:
    """Cross-platform best-effort lock for processes started by this switcher.

    Direct launches of either QQ Bridge or upstream MaiBot are outside this
    guard; callers still need to ensure exclusive channel ownership.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"\x00")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("另一个由切换器启动的引擎仍在运行，请先停止") from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("另一个由切换器启动的引擎仍在运行，请先停止") from exc
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="QQ 双引擎启动切换（独立进程，配置和记忆不共享）")
    parser.add_argument("action", nargs="?", choices=("run", "status", "set-default"),
                        default="run")
    parser.add_argument("--engine", choices=ENGINES,
                        help="本次使用哪个引擎（不修改默认配置）")
    parser.add_argument("--maibot-python", default=None,
                        help="MaiBot 独立 Python 环境路径")
    parser.add_argument("--confirm-exclusive-channel", action="store_true",
                        help="确认已停止以其他方式运行的同 QQ 账号实例")
    args = parser.parse_args(argv)
    try:
        current = read_config()
        engine = args.engine or current["engine"]
        mai_python = (args.maibot_python if args.maibot_python is not None
                      else current["maibot_python"])

        if args.action == "status":
            print(f"默认引擎: {current['engine']}\n本次选择: {engine}")
            print("说明: 切换的是启动的独立 QQ 进程，不共享 Persona/PersonID/Memory。")
            return 0
        if args.action == "set-default":
            if args.engine is None:
                parser.error("set-default 需要 --engine")
            save_config(engine, maibot_python=mai_python)
            print(f"默认 QQ 引擎已设为 {engine}；不会修改历史数据或正在运行的进程。")
            return 0
        if engine == "maibot" and not args.confirm_exclusive_channel:
            parser.error("使用 MaiBot 必须加 --confirm-exclusive-channel 并确保原 QQ Bridge 已停止")
        cmd = build_command(engine, maibot_python=mai_python,
                            confirmed=args.confirm_exclusive_channel)
        with exclusive_launcher():
            print(f"已选择 {engine}。按 Ctrl+C 停止后才能切换。", flush=True)
            print("注意：直接手工启动的进程不受此互斥锁管理。", flush=True)
            return subprocess.call(cmd, cwd=ROOT)
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
