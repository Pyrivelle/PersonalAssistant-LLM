"""Local, user-controlled dashboard window dimensions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from zhuangfy.paths import Paths


SETTINGS_FILE = Paths.current().config_file("dashboard_settings.json")
PRESETS = ((1920, 1080), (1600, 900), (1536, 864), (1440, 900),
           (1366, 768), (1280, 720))


@dataclass(frozen=True)
class WindowPreferences:
    width: int = 1600
    height: int = 900

    def validated(self) -> "WindowPreferences":
        if (type(self.width) is not int or type(self.height) is not int
                or not 940 <= self.width <= 3840
                or not 620 <= self.height <= 2160):
            raise ValueError("窗口尺寸超出可用范围")
        return self


def load_preferences(path: Path = SETTINGS_FILE) -> WindowPreferences:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"width", "height"}:
            raise ValueError("窗口设置格式错误")
        return WindowPreferences(raw["width"], raw["height"]).validated()
    except (OSError, ValueError, TypeError):
        return WindowPreferences()


def save_preferences(value: WindowPreferences, path: Path = SETTINGS_FILE) -> None:
    value.validated()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps({"width": value.width, "height": value.height},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fit_to_screen(value: WindowPreferences, available_width: int,
                  available_height: int) -> tuple[int, int]:
    """Respect the current display without changing the saved preference."""
    value.validated()
    return (max(940, min(value.width, available_width)),
            max(620, min(value.height, available_height)))
