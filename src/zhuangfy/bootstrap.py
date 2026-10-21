"""Package entry point while the desktop implementation is migrated in stages."""

from __future__ import annotations

import sys

from .paths import Paths


def enable_legacy_imports(paths: Paths | None = None) -> None:
    legacy_root = (paths or Paths.current()).legacy_root
    if not legacy_root.is_dir():
        raise RuntimeError(f"Legacy application directory is missing: {legacy_root}")
    legacy = str(legacy_root)
    if legacy not in sys.path:
        sys.path.insert(0, legacy)


def main() -> None:
    enable_legacy_imports()
    from dashboard import main as dashboard_main

    dashboard_main()
