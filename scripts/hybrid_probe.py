"""Local smoke check for the *transport only* hybrid Maisaka worker.

Exit 0 means the subprocess handshake works. It does not mean original
Maisaka Planner, Vision, or QQ delivery are wired into ZhuangFY.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zhuangfy.engine_fusion.ipc_transport import HybridWorkerClient


async def _probe() -> int:
    client = await HybridWorkerClient.launch(timeout=5)
    try:
        print("Hybrid IPC transport: OK" if client.transport_ready
              else "Hybrid IPC transport: FAILED")
        print("Maisaka native runtime: " + (
            "READY" if client.native_ready else "NOT CONNECTED (expected in this PR stage)"
        ))
        print("QQ engine selection: hybrid remains disabled until native ready")
        return 0 if client.transport_ready and not client.native_ready else 1
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_probe()))
    except Exception as exc:
        print(f"Hybrid IPC transport probe failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
