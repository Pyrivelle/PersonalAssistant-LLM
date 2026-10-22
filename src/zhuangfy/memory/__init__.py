"""ZhuangFY memory package.

The new vector prototype is opt-in and does not replace existing Mem0 or QQ
user memories. Importing this package has no filesystem or network effects.
"""

from .repository import MemoryEntry, SQLiteMemoryRepository
from .service import MemoryVectorService, RecallResult, RepairResult, StoreResult
from .vector import EmbeddingProvider, FastEmbedProvider, QdrantLocalIndex, VectorHit, VectorIndex

__all__ = [
    "EmbeddingProvider", "FastEmbedProvider", "MemoryEntry", "MemoryVectorService",
    "QdrantLocalIndex", "RecallResult", "RepairResult", "SQLiteMemoryRepository",
    "StoreResult", "VectorHit", "VectorIndex",
]
