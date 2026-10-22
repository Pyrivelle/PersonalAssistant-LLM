# M3 — Curated Memory → Hybrid RAG

本阶段叠加 M0/M1/M2 的独立内核，不改变旧 `SenseVoice/memory.py` 和 QQ 成员资料。原始消息与证据来自 M1，已经核验的 `curated_memories` 来自 M2。

## 实现

- `CuratedIndexProjector`：M2 `curated_memories` 是唯一权威原文；将**当前 active** 记录向量化到 M0 的 `VectorIndex`（Qdrant Local），保留每个 `memory_id` 的模型版本与 SHA-256 内容摘要索引表。
- SQLite FTS5 `trigram` 提供中文短语召回，`Qt` 等不足三个字符的关键词使用作用域限定的 SQL 子串检索；所有 SQL 使用绑定参数。
- SQLite trigger 维护 FTS5 索引。历史记忆被替代或私聊因外键级联被擦除后不会再被返回；删除原始记录可触发 FTS 索引删除。
- `HybridMemoryRetriever`：FTS + Qdrant 两路召回、RRF 融合、去重和上限；向量命中必须通过 SQLite 验证 **agent/person、active、内容摘要、投影版本**，不能信任 Qdrant payload。
- `assemble_memory_context`：限制字符预算、标明记忆是低信任历史材料。最终调用者应把它当独立的数据消息而非 system 命令。

## 调用示例（伪运行环境）

```python
from pathlib import Path
from zhuangfy.memory import (
    MemoryDatabase, IdentityRepository, TimelineRepository, CuratorStore,
    FastEmbedProvider, QdrantLocalIndex, CuratedIndexProjector,
    HybridMemoryRetriever, assemble_memory_context,
)

root = Path("data/memory/m3")
db = MemoryDatabase(root / "memory.sqlite3")
people = IdentityRepository(db)
timeline = TimelineRepository(db, people)
curated = CuratorStore(db, people, timeline)
embedder = FastEmbedProvider()  # 首次明确调用时才加载权重
vectors = QdrantLocalIndex(root / "vectors", model_id=embedder.model_id, dimension=embedder.dimension)
projector = CuratedIndexProjector(db, people, curated, embedder, vectors)
projector.sync()
# 必须由可信渠道验证后传入对应的 person_id：
# hits = HybridMemoryRetriever(projector, curated, people).search(
#     agent_id="zhuangfy-main", person_id=verified_id, query="港口帧率")
# notes = assemble_memory_context(hits)
projector.close()
```

## 一致性与限制

- SQLite 是权威源，Qdrant 是可重建的**派生索引**。向量写入失败只影响语义检索；FTS5 仍可使用。
- `sync()` 是显式的投影同步操作。M4 需要负责每轮提炼后调度同步，单一 Qdrant Local 所有者负责串行。
- 已被删除或替代的记忆在同步前即不能通过查询层认证。投影状态表保留失效 `memory_id` 用于向量删除重试。
- 如果机器崩溃在“Qdrant 写成功而 SQLite 标记失败”之间，可能有不带投影记录的孤儿向量；其不参与 SQL 验证，因此无法在检索中泄露，但未来应提供一次完整 collection 重建/孤儿扫描操作。
- Embedding 模型变更切换 collection，当前 projector 只管理自己当前模型版本；旧 collection 的清理需要手动处理，不应静默删除别的模型索引。
- 版本只保证本地 SQLite 原文与向量索引匹配，不自动证明模型提炼的**事实语义**完全正确。
- 不支持未经授权的 `person_id` 自报；M4 的 API 必须从受信的渠道身份中解析当前人物。

## 测试

```bash
PYTHONPATH=src python -m unittest -v tests.unit.test_memory_hybrid_rag
```

覆盖中文双路径召回、相似项目跨人隔离、错误向量数据、过期事实、版本替代、私聊删除级联索引、向量不可用的 FTS 降级、短专名、授权撤销。
