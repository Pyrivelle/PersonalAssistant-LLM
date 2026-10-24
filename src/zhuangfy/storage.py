"""Relocate local user data without moving program code or external engines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .paths import Paths


LEGACY_DIRECTORIES = ("conversation_data", "memory_data", "private_state",
                      "logs", "models", "samples")
LEGACY_FILES = {
    ".env.local", "llm_settings.json", "settings.json", "dashboard_settings.json",
    "qq_settings.json", "cognition_settings.json", "gpt_sovits_settings.json",
    "model_routes.json", "model_registry.json", "api_profiles.json",
    "owner_link.json", "qq_identity.json", "proactive_settings.json",
    "autonomy_settings.json", "skills_settings.json",
}
POINTER_FILE = "storage_location.json"
RESULT_FILE = "storage_migration_result.json"


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _files(root: Path):
    if root.exists():
        yield from (item for item in root.rglob("*") if item.is_file())


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _copy_checked(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
        for original in _files(source):
            copied = destination / original.relative_to(source)
            if original.stat().st_size != copied.stat().st_size or _digest(original) != _digest(copied):
                raise IOError(f"Storage copy verification failed: {original.name}")
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if source.stat().st_size != destination.stat().st_size or _digest(source) != _digest(destination):
            raise IOError(f"Storage copy verification failed: {source.name}")


def legacy_items(root: Path) -> list[Path]:
    items = [root / name for name in LEGACY_DIRECTORIES if (root / name).exists()]
    names = set(LEGACY_FILES)
    for pattern in ("*.json", ".env*", "*.db", "*.sqlite3"):
        names.update(path.name for path in root.glob(pattern)
                     if not path.name.endswith(".example.json")
                     and path.name != ".env.example")
    items.extend(root / name for name in sorted(names) if (root / name).is_file())
    return items


def estimated_bytes(project_root: Path, target: Path) -> int:
    _, _, (legacy, data) = validate_target(project_root, target)
    return (sum(item.stat().st_size for item in _files(data))
            + sum(file.stat().st_size for source in legacy_items(legacy)
                  for file in (_files(source) if source.is_dir() else [source])))


def validate_target(project_root: Path, target: Path) -> tuple[Paths, Path, tuple[Path, Path]]:
    project_root = project_root.resolve()
    paths = Paths(project_root, {})
    target = target.expanduser().resolve()
    sources = (paths.data_root.resolve(), paths.project_data_root.resolve())
    if (_within(target, project_root) or _within(project_root, target)
            or any(_within(target, source) or _within(source, target) for source in sources)
            or target == Path(target.anchor)):
        raise ValueError("Choose an empty folder outside the project and current storage")
    if target.exists() and any(target.iterdir()):
        raise ValueError("The selected storage folder must be empty")
    if not target.parent.is_dir():
        raise ValueError("The selected folder's parent does not exist")
    return paths, target, sources


def migrate(project_root: Path, target: Path) -> Path:
    """Copy and verify user data, then switch paths; retain sources for rollback.

    Call only while the dashboard, memory service and background writers are stopped.
    """
    paths, target, sources = validate_target(project_root, target)
    project_root = paths.project_root.resolve()
    staging = target.parent / f".{target.name}.zhuangfy-migration-{os.getpid()}"
    if staging.exists():
        raise FileExistsError("Migration staging folder already exists")
    try:
        staging.mkdir()
        if sources[1].exists():
            _copy_checked(sources[1], staging / "data")
        else:
            (staging / "data").mkdir()
        legacy = staging / "legacy"
        legacy.mkdir()
        for source in legacy_items(sources[0]):
            _copy_checked(source, legacy / source.name)
        if target.exists():
            target.rmdir()  # Previously checked empty; never recursive.
        os.replace(staging, target)
        pointer = project_root / POINTER_FILE
        pending = pointer.with_suffix(".tmp")
        pending.write_text(json.dumps({"version": 1, "path": str(target)}, ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")
        os.replace(pending, pointer)
        return target
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _wait_for_process(pid: int) -> None:
    if os.name != "nt":
        raise RuntimeError("Automatic UI migration is currently Windows-only")
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        if kernel32.GetLastError() == 87:  # ERROR_INVALID_PARAMETER: already exited.
            return
        raise OSError("Could not wait for the dashboard to exit")
    try:
        if kernel32.WaitForSingleObject(handle, 60000) != 0:
            raise TimeoutError("Dashboard did not exit before migration")
    finally:
        kernel32.CloseHandle(handle)


def _stop_local_writers(project_root: Path) -> None:
    # The UI has exited and stopped its QProcess children. The Memory Service
    # and autonomy worker were launched separately; stop only this checkout's.
    escaped = str(project_root).replace("'", "''")
    script = ("$root='" + escaped + "'; "
              "$items=Get-CimInstance Win32_Process | Where-Object { "
              "$_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -like \"*$root*\" "
              "-and $_.CommandLine -match 'zhuangfy.memory.http_server|zhuangfy.agent_v2.autonomy_cli' }; "
              "foreach($item in $items){Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue}; "
              "Start-Sleep -Milliseconds 400; "
              "$remaining=Get-CimInstance Win32_Process | Where-Object { "
              "$_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -like \"*$root*\" "
              "-and $_.CommandLine -match 'zhuangfy.memory.http_server|zhuangfy.agent_v2.autonomy_cli' }; "
              "if($remaining){exit 1}")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                   check=True, capture_output=True, timeout=30)
    time.sleep(1)


def _restart(project_root: Path) -> None:
    script = project_root / "scripts" / "windows" / "start-memory-enabled.ps1"
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(script)], cwd=project_root,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     close_fds=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--wait-pid", type=int, required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    result = project / RESULT_FILE
    try:
        _wait_for_process(args.wait_pid)
        _stop_local_writers(project)
        destination = migrate(project, args.target)
        outcome = {"ok": True, "path": str(destination)}
    except Exception as exc:
        outcome = {"ok": False, "error": str(exc)}
    result.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
    _restart(project)


if __name__ == "__main__":
    main()
