# ZhuangFY 长期记忆：向量索引 MVP（M0）

> 开发阶段。**尚未接入 QQ、语音窗口或实际 LLM 回复**；这不是旧 Mem0 记忆库的迁移或替换。

## 我们真正要实现什么

让**同一个庄方宜实例**长期认识多个不同的人，记录可验证的真实共同经历，并在未来合适的时候找回。QQ、桌面语音、未来微信只是联系渠道，不能直接成为人物本体。要分别解决：谁参与、何时发生、事实出处、当下是否仍有效、哪些人有权得知。

**向量只是相似性索引，不能自动回答身份、权限、事实有效期和共同经历是否真实的问题。**

## 这次采用的技术链

```text
人工批准的记忆条目（M0，无自动提取）
    │
    ▼
SQLiteMemoryRepository：原文、agent_id、person_id、来源 turn ID
    │
    ▼
EmbeddingProvider：将文字转换为指定维度的向量
    │
    ▼
QdrantLocalIndex：按模型 ID/维度隔离 collection、按人物过滤
    │
    ▼
MemoryVectorService.recall(person_id, query)
    │
    ▼
向量命中 → SQLite 按 agent_id + person_id 二次核验 → 原文
```

M0 已经实现的组件：

| 模块 | 职责 |
| --- | --- |
| `memory/repository.py` | SQLite 是权威数据；支持新增、读取、索引状态、删除清理 |
| `memory/vector.py` | 可替换 Embedding 协议、FastEmbed 本地中文模型、Qdrant Local |
| `memory/service.py` | `remember/recall/forget/repair_index`，访问范围二次核验 |
| `tests/unit/test_memory_vector.py` | 不联网的假向量检索和隔离、故障恢复测试 |
| `tests/integration/test_memory_qdrant.py` | Qdrant Local 的持久化、过滤、删除及模型隔离测试 |

可选中文 Embedding：`BAAI/bge-small-zh-v1.5`，512 维。权重第一次**明确使用**时由 FastEmbed 下载，导入模块不会下载；生产使用前仍需评测中文口语和技术专有名词检索效果。

## 本地运行

在仓库根目录、Python 3.12 环境执行：

```powershell
python -m pip install -e ".[memory-vector]"
python -m unittest -v tests.unit.test_memory_vector tests.integration.test_memory_qdrant
```

仅测试纯 Python 数据流，无需安装 Qdrant、FastEmbed：

```powershell
python -m pip install -e . --no-deps
python -m unittest -v tests.unit.test_memory_vector
```

最简示例（数据在 `data/` 下，与旧记忆隔离；请勿拿真实私人数据作原型测试）：

```python
from pathlib import Path
from uuid import uuid4
from zhuangfy.memory import (
    FastEmbedProvider, MemoryVectorService, QdrantLocalIndex, SQLiteMemoryRepository,
)

root = Path("data/memory/vector_mvp")
embedder = FastEmbedProvider()
index = QdrantLocalIndex(root / "qdrant", model_id=embedder.model_id,
                         dimension=embedder.dimension)
service = MemoryVectorService(
    SQLiteMemoryRepository(root / "memory.sqlite3"),
    index, embedder, agent_id="zhuangfy-main",
)

# M0 要求显式传入测试 person_id；生产环境必须先通过身份验证。
person_id = str(uuid4())
stored = service.remember(
    person_id=person_id,
    content="曾讨论 OSG 港口帧率优化方案",
    kind="episodic",
)
print(stored.indexed, stored.entry.memory_id)
for result in service.recall(person_id=person_id, query="港口性能问题"):
    print(result.score, result.entry.content)
service.close()
```

## 数据可靠性与安全边界

- **先保存 SQLite 后建立向量索引。** 索引暂时失败返回 `indexed=False`；记录留在 SQLite，`repair_index()` 可以补建。
- **先清空 SQLite 个人记忆原文，再尝试删向量。** 如果 Qdrant 不可用，将保留无原文的 tombstone 等待 `repair_index()`。这意味着索引及作用域元数据可能仍暂存，不能声称物理擦除已经全部完成。
- Qdrant 的 payload 只包含 UUID、Agent ID 和 Person ID，**不复制记忆原文**。检索结果仍要回 SQLite 二次核对人物、Agent 和记录状态。
- `person_id` 必须由后续 `IdentityService` 验证后传入；本版本没有认证层，**不能对公网开放**，不能直接授权 QQ 用户自行提供任意 person ID。
- SQLite/Qdrant 当前没有应用层加密。务必保护本地数据目录，不要上传个人聊天、密钥或向量库到 GitHub。
- **不要让两个进程同时打开同一 Qdrant Local 目录**。QQ Bridge 与桌面是不同进程；真正集成前需要单一 Memory Service 进程（或改用 Qdrant Server）。
- 更换 Embedding 模型 / 维度会切换 Qdrant collection，需要手动调用 `repair_index()` 重建新索引；旧 collection 不自动删除。未来需要为重新索引和过期索引制定清理机制。
- 现有 `SenseVoice/memory.py` / `member_memory.py` / `owner_memory.py` 继续使用原有数据。M0 不迁移、不删除、不主动收集聊天内容。
- `source_turn_ids` 当前只是预留引用，**尚未验证 Timeline 原始记录是否存在**。因此 M0 只能存明确批准的事实，不能自动声称“共同经历”。

## 接下来交给 Codex 的开发目标

**M1 — 人物身份 + Timeline**：建立 `persons / identities / conversations / timeline_entries` 的真实数据模型和迁移。人物的多个平台账号只有在显式验证后才能合并；昵称不能作为合并依据。用户输入获得授权后独立落库，模型回复区分 generated 和 confirmed-delivered。

**M2 — Sakura 风格 MemoryCurator**：从真实历史中**异步**提炼语义事实、共同经历、协作习惯；输出严格类型化操作 `add / update / supersede / ignore`，要求来源 turn ID、明确说话主体、发生时间和有效状态，程序逐条验证。失败可重试、写入幂等，不能把助手建议当成已发生行动。

**M3 — 混合 RAG 检索**：使用中文友好的 SQLite FTS5 与 Qdrant 组合召回、去重排序，结合人物权限、旧事件状态和时间有效性筛选；限制注入 Token，标记记忆为不可信参考，不允许历史文本作为新的系统指令。

**M4 — 跨进程服务与旧数据迁移**：提供只监听本机且有认证的 Memory Service；QQ/桌面统一调用。迁移旧 Mem0、QQ 成员记忆之前必须备份、逐项核对，不可默认合并、不能破坏旧数据。

**M5 — 管理界面与评测**：可浏览、查证、更正、遗忘和重建索引；至少覆盖不同人物同名、跨渠道身份、用户纠错、过期事实、记忆泄露和错误共同经历的合成测试。

## 参考方向

Sakura：真实 Timeline 与第一人称 Curator；MaiBot：人物画像和证据关联；EverMemOS：多人事件及个人视角；Graphiti：事实有效时间；Letta：Agent 长期身份和可编辑记忆；dsh-qqchat：后台反思重试；Mem0/Qdrant：可插拔检索组件。
