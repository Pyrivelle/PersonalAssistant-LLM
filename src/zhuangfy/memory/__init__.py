"""ZhuangFY memory package.

The new vector prototype is opt-in and does not replace existing Mem0 or QQ
user memories. Importing this package has no filesystem or network effects.
"""

from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityProof, IdentityRepository, Person
from .timeline import PrivateConversation, TimelineEntry, TimelineRepository
from .curator import CuratorModel, CuratorRunResult, MemoryCurator, parse_and_validate
from .curator_store import CuratedMemory, CuratorOperation, CuratorStore, Evidence, ReflectionJob
from .admin_store import MemoryAdminStore
from .evaluation import MemoryEvalCase, MemoryEvalReport, evaluate_memory_retrieval
from .client import MemoryChannel, MemoryClient, MemoryClientError
from .coordinator import MemoryCore
from .projector import CuratedIndexProjector, SyncResult
from .retrieval import HybridMemoryRetriever, MemoryHit
from .context import assemble_memory_context
from .repository import MemoryEntry, SQLiteMemoryRepository
from .service import MemoryVectorService, RecallResult, RepairResult, StoreResult
from .vector import EmbeddingProvider, FastEmbedProvider, QdrantLocalIndex, VectorHit, VectorIndex

__all__ = [
    "MemoryAdminStore", "MemoryEvalCase", "MemoryEvalReport",
    "evaluate_memory_retrieval", "MemoryChannel", "MemoryClient", "MemoryClientError", "MemoryCore",
    "CuratedIndexProjector", "SyncResult", "HybridMemoryRetriever", "MemoryHit",
    "assemble_memory_context", "CuratorModel", "CuratorRunResult", "MemoryCurator", "CuratedMemory",
    "CuratorOperation", "CuratorStore", "Evidence", "ReflectionJob", "parse_and_validate",
    "MemoryDatabase", "ChannelIdentity", "IdentityProof", "IdentityRepository",
    "Person", "PrivateConversation", "TimelineEntry", "TimelineRepository",
    "EmbeddingProvider", "FastEmbedProvider", "MemoryEntry", "MemoryVectorService",
    "QdrantLocalIndex", "RecallResult", "RepairResult", "SQLiteMemoryRepository",
    "StoreResult", "VectorHit", "VectorIndex",
]
