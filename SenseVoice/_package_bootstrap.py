"""Keep old flat imports working while modules move into src/zhuangfy."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def ensure_src_path() -> None:
    source = str(Path(__file__).resolve().parents[1] / "src")
    if source not in sys.path:
        sys.path.insert(0, source)


def alias_module(old_name: str, canonical_name: str) -> None:
    ensure_src_path()
    sys.modules[old_name] = importlib.import_module(canonical_name)
