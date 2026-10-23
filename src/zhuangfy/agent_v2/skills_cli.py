"""Read-only skill manager CLI: discovery never executes plugin Python code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .skills import inspect_skills


def main() -> None:
    parser = argparse.ArgumentParser(description="List local ZhuangFY skill manifests (read-only)")
    parser.add_argument("--root", required=True, type=Path, help="Trusted skills directory")
    parser.add_argument("--config", required=True, type=Path, help="skills_settings.json")
    parser.add_argument("--json", action="store_true", help="Print machine-readable metadata")
    args = parser.parse_args()
    statuses = inspect_skills(args.root, args.config)
    if args.json:
        print(json.dumps([
            {"id": s.id, "name": s.name, "version": s.version,
             "enabled": s.enabled, "loaded": s.loaded, "error": s.error}
            for s in statuses], ensure_ascii=False, indent=2))
        return
    for s in statuses:
        state = ("enabled (activation only at runtime)" if s.enabled else "disabled")
        if s.error:
            state += " — " + s.error
        print(f"{s.id} {s.version} · {state}")
    if not statuses:
        print("No skills discovered")


if __name__ == "__main__":
    main()
