"""Run the *unmodified* upstream Maisaka engine in a separate instance.

The upstream git submodule contains the complete original Planner/Replyer/wait/
stop and image manager. This launcher is NOT a ZhuangFY channel integration.
Never point this experiment at ZhuangFY's production data directory.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

UPSTREAM_SHA = "062d45cf8bd4272ac57d7ec03fcf405e5a458211"
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "third_party" / "MaiBot"
INSTANCE = ROOT / ".maisaka-experiment"


def verify_source(source: Path = SOURCE, *, enforce_pin: bool = True) -> None:
    if not (source / "src" / "maisaka" / "reasoning_engine.py").is_file():
        raise RuntimeError(
            "完整 MaiBot 子模块尚未初始化；先执行 git submodule update --init third_party/MaiBot"
        )
    required = (
        "src/maisaka/reasoning_engine.py",
        "src/maisaka/runtime.py",
        "src/maisaka/chat_loop_service.py",
        "src/maisaka/builtin_tool/wait.py",
        "src/maisaka/builtin_tool/stop.py",
        "src/maisaka/builtin_tool/reply.py",
        "src/chat/image_system/image_manager.py",
        "prompts/zh-CN/image_description.prompt",
    )
    missing = [p for p in required if not (source / p).is_file()]
    if missing:
        raise RuntimeError("MaiBot 上游源码缺失：" + ", ".join(missing))
    if enforce_pin:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        if result.stdout.strip() != UPSTREAM_SHA:
            raise RuntimeError(
                f"MaiBot 上游版本漂移：{result.stdout.strip()}；预期 {UPSTREAM_SHA}"
            )


def safe_instance_path(root: Path, source: Path, instance: Path) -> Path:
    instance = instance.expanduser().resolve()
    # The default isolated instance intentionally lives under the repository.
    # Protect the repository root itself and specifically sensitive subtrees.
    if instance == root.resolve():
        raise ValueError("实验实例不能直接使用 ZhuangFY 仓库根目录")
    for forbidden in (source, root / "private_state",
                      root / "conversation_data", root / "project_data"):
        forbidden = forbidden.resolve()
        if instance == forbidden or forbidden in instance.parents:
            raise ValueError("实验实例不能与 ZhuangFY 源码、缓存或现有记忆目录重叠")
    return instance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="实验：以完整、原版 MaiBot 运行图片理解和 Planner–Reply 循环"
    )
    parser.add_argument("command", choices=("check", "init", "run"))
    parser.add_argument("--instance", type=Path, default=INSTANCE)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--confirm-exclusive-channel", action="store_true",
        help="确认不会与 ZhuangFY 同时连接相同 QQ/OneBot 身份；仅 run 必填",
    )
    args = parser.parse_args(argv)
    try:
        verify_source()
        target = safe_instance_path(ROOT, SOURCE, args.instance)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))

    if args.command == "check":
        print(f"MaiBot upstream OK: {UPSTREAM_SHA}\nIsolated instance: {target}")
        return 0
    if args.command == "run" and not args.confirm_exclusive_channel:
        parser.error(
            "run 必须确认 QQ/OneBot 实例独占；请先停止相同账号的 ZhuangFY QQ Bridge，"
            "然后添加 --confirm-exclusive-channel"
        )
    if args.command == "run":
        missing = [name for name in ("bot_config.toml", "model_config.toml")
                   if not (target / "config" / name).is_file()]
        if missing:
            parser.error("实例未初始化：先执行 init 并配置模型与通讯方式")

    target.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MAIBOT_INSTANCE_DIR"] = str(target)
    env["PYTHONPATH"] = str(SOURCE) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [str(args.python.expanduser().resolve()), "-m", "src.cli.tool", args.command,
           "--directory", str(target)]
    print("运行原版 MaiBot；仅使用隔离实例，不共享 ZhuangFY 的记忆与对话库。", flush=True)
    return subprocess.call(cmd, cwd=SOURCE, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
