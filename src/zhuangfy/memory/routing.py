"""Select one memory backend per channel at process startup.

The new service is opt-in through the channel environment flag. Once selected,
legacy stores must stay inactive even if the service is temporarily offline;
silently falling back would split one person's history across two databases.
"""

from __future__ import annotations

import os


def new_memory_selected(channel: str) -> bool:
    if channel not in {"qq", "desktop"}:
        raise ValueError("Unknown memory channel")
    return os.environ.get(f"ZHUANGFY_MEMORY_{channel.upper()}", "").strip() == "1"


def legacy_memory_allowed(channel: str) -> bool:
    return not new_memory_selected(channel)
