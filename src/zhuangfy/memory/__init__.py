"""ZhuangFY memory package.

The new vector prototype is opt-in and does not replace existing Mem0 or QQ
user memories. Importing this package has no filesystem or network effects.
"""

from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityProof, IdentityRepository, Person
from .timeline import PrivateConversation, TimelineEntry, TimelineRepository
from .repository import MemoryEntry, SQLiteMemoryRepository
from .service import MemoryVectorService, RecallResult, RepairResult, StoreResult
from .vector import EmbeddingProvider, FastEmbedProvider, QdrantLocalIndex, VectorHit, VectorIndex

__all__ = [
    "MemoryDatabase", "ChannelIdentity", "IdentityProof", "IdentityRepository",
    "Person", "PrivateConversation", "TimelineEntry", "TimelineRepository",
    "EmbeddingProvider", "FastEmbedProvider", "MemoryEntry", "MemoryVectorService",
    "QdrantLocalIndex", "RecallResult", "RepairResult", "SQLiteMemoryRepository",
    "StoreResult", "VectorHit", "VectorIndex",
]
