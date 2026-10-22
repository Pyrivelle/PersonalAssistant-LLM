"""Experimental conversation primitives for ZhuangFY Agent Runtime V2.

This package does not replace the legacy dialogue or memory services yet.
"""

from .messages import MessagePart, UnifiedMessage, from_qq_incoming
from .debounce import MessageDebouncer, WAIT_PRESETS

__all__ = ["MessagePart", "UnifiedMessage", "from_qq_incoming", "MessageDebouncer", "WAIT_PRESETS"]
