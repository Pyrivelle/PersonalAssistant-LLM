"""Shared application name and portrait icon for the desktop windows."""

from PySide6.QtGui import QIcon

from _package_bootstrap import ensure_src_path

ensure_src_path()

from zhuangfy.paths import Paths


APP_NAME = "ZhuangFY"
ICON_PATH = Paths.current().images_root / "zhuangfy.png"


def app_icon() -> QIcon:
    return QIcon(str(ICON_PATH))
