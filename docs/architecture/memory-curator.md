# M2：证据约束的第一人称记忆整理器

> 叠加在 M1 Timeline 之上。**此阶段不接入真实 LLM，也不在 QQ/桌面运行时自动处理聊天。**测试中全部使用假模型和虚构对话。

## 整体技术链

```text
M1 Timeline：已获授权的用户原话 received + 确认交付的机器人回复 delivered
    │
    ▼
CuratorStore.schedule()：排入有边界的反思任务（SQLite）
    │
    ▼
MemoryCurator.process_next()
    │  claim_next()，获取 Lease Token，读取当前 Person 的新对话
    │
    ▼
CuratorModel.extract(system_prompt, payload)  [独立可替换接口]
    │
    ▼
parse_and_validate()：不可相信 LLM 的 JSON
    │   - 用户原文 quote 精确匹配 + source entry ID 必须属于本次任务
    │   - episodic 还要有同一对话中 confirmed delivered 的助手回复
    │   - procedural 需要两次不同用户发言
    │   - supersede 的目标必须为当前同一 Agent、Person、Kind 的有效记忆
    │   - 不允许凭昵称合并人物 / 擅自删除 / 引用其他人私聊
    ▼
CuratorStore.complete()：SQLite 事务一次性写入、旧事实标记 superseded、
                          附带可查证的 Timeline evidence，作业标记 done
```

## 数据结构与边界

| 表/模块 | 作用 |
| --- | --- |
| `reflection_jobs` | 持久化排队、失败重试、租约和当前进度 |
| `curated_memories` | 当前语义事实、经历、协作规则；带事件状态和版本关联 |
| `curated_evidence` | 对应真实 Timeline entry 与用户原文引文 |
| `curator_store.py` | 排队、租约、事务写入、按 Person 查询及擦除 |
| `curator.py` | LLM 适配协议、第一人称整理提示词、严格结构/出处验证 |

记忆类型分为：
- `semantic`：用户自身明确表达的事实；
- `episodic`：含实际送达回复的双方真实对话经历；
- `procedural`：有至少两次不同用户发言为证据的协作习惯。

事件状态 `unspecified / ongoing / completed / outdated` 是**模型的候选判断**，并不是程序自动证明“某项现实行动确实完成”。模型必须遵守系统提示词，但即使引文逐字匹配，仍可能误解用户的反讽、转述或引用。后续需要人工可更正 UI、测试集和更精细的事实评估。

`supersede` 会将同一人物同类型的旧版本标为非活跃状态，同时保留历史版本及真实来源，不会通过直接覆盖使过去失去审计线索。LLM 不能发起不可逆的 `delete`，主动遗忘要走独立授权删除流程。

## 首个可运行离线实验

```python
from pathlib import Path
from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import IdentityRepository
from zhuangfy.memory.timeline import TimelineRepository
from zhuangfy.memory.curator_store import CuratorStore
from zhuangfy.memory.curator import MemoryCurator

db = MemoryDatabase(Path("data/memory/m2.sqlite3"))
identities = IdentityRepository(db)
timeline = TimelineRepository(db, identities)
curated = CuratorStore(db, identities, timeline)

# 先通过 M1 创建 Person、明确验证身份、配置双重授权，
# 再使用 Timeline 接受真实用户消息并标记真正送达的机器人回复。
# 之后调用 curated.schedule(conversation_id=..., person_id=...)
# 然后把一个遵守 CuratorModel.extract 协议的模型客户端传入：
# MemoryCurator(curated, authenticated_model_adapter).process_next()
```

实际 Adapter 的请求必须经过隐私允许的模型供应商；**不要默认把真实私聊传入云端 API**。M2 并没有自带密钥配置、网络请求、后台线程或主回复链路变更。计划由后续的应用服务承担调度与模型路由。

## 故障恢复

- 任务抢占采用 `claim_next()` + Lease Token，避免两个 Worker 误以为同时拥有任务。
- 失败只记录异常**类型**，不保存敏感原文或整个模型响应。
- 最多 3 次重试；租约过期可由另一个 Worker 重试。耗尽后由可信操作端调用 `retry_failed_job(job_id)` 明确重置，重新检查授权；不会自动无限重试。长期慢于默认 10 分钟的模型调用需要后续引入租约续期。
- 完整校验不过时，**整批回滚、不写入任何候选记忆**。
- 发送给 Curator 模型之前过滤含凭据特征的消息，既不转发，也不允许用它们作证据；注意已获授权的 M1 本地 Timeline 仍可能存有这些原文，后续需完善入库过滤和用户数据删除流程。
- 数据库事务内不执行云端模型请求或 Embedding。
- 记忆提炼获得 Timeline 与 Curation 两项授权；撤销授权后，待运行的作业不会发送历史消息给模型。
- 私聊 Timeline 的显式删除会级联删除相应记忆和队列；未来需要把删除事务扩展到 M0/M3 向量索引、缓存与备份。

## 重要限制：为什么还没直接接上向量库？

M0 的 SQLiteMemoryRepository 是供手工批准记忆使用的极简表；M2 要求人物身份、Timeline 证据、事件状态和版本链。当前 M2 把这些权威信息保存在新结构化表，**尚未将它们自动嵌入 M0 的 Qdrant**。下一步 M3 应定义“活跃 curated_memories → 可重建的向量索引”的投影，同一 `memory_id` 作关联，并在 supersede / erase 时同步失效旧索引，而不是创建第三套记忆原文。

## 运行测试

```bash
PYTHONPATH=src python -m unittest -v tests.unit.test_memory_identity_timeline tests.unit.test_memory_curator
```

测试覆盖未送达助手不可成为共同经历、引用不真实则整批拒绝、同名不同人隔离、跨人 supersede 被拒绝、旧事实更替、确认重复与 3 次失败重试、租约失效、授权撤销和历史擦除。真实语言模型语义正确率、中文 Embedding 质量和并发压力测试仍是后续验收项。
