"""Shared application name and portrait icon for the desktop windows."""

from pathlib import Path

from PySide6.QtGui import QIcon


APP_NAME = "ZhuangFY"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "zhuangfy.png"


def app_icon() -> QIcon:
    return QIcon(str(ICON_PATH))
