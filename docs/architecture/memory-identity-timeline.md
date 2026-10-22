# M1：人物身份与真实交互 Timeline

> 设计与实现入口：`src/zhuangfy/memory/identity.py`、`timeline.py`。M1 在 PR #4 的向量基础上单独推进，尚未接入 QQ / Qt 运行时。

## 身份模型

- `person_id` 是 UUID，表示被庄方宜认识的同一人物；`display_name` 仅供展示，**不能用来关联账号**。
- `ChannelIdentity(platform, channel_account_id, external_user_id)` 表示账号。QQ 的 `channel_account_id` 可用 Bot QQ ID，避免不同 Bot/账号作用域被混合。
- `IdentityRepository.link_verified(person_id, identity, proof)` 要求经过已授权外部流程核验的 `IdentityProof`，只存不含秘密的验证参考。**此 API 是仓库内部的信任边界，不提供真实的登录认证，也不能直接把用户自报的 Proof 当成可靠证据。**
- 同一账号不得静默指向两个 Person；跨端合并必须有独立的验证记录，昵称相同不等于同一人。

## 数据表

```text
persons             统一人物
identities          已验证的渠道账号
memory_consents     timeline / curation 两类授权，默认未授权
private_conversations  单一人物与同一个 Agent 的私聊
timeline_entries    带来源、时间、送达状态的原始轮次记录
```

**这一阶段只允许私聊 Timeline。** 群聊需要单独的群成员授权、公共事件与私有记忆可见性策略，不应把全部群聊历史直接当成任意群友的私人记忆。

## 消息状态

```text
入站用户消息 --(已验证账号 + timeline 授权)--> received
LLM 生成的回复 -------------------------------> generated
外部发送器确认完整送达 ----------------------> delivered
已生成但未发送/被放弃 ----------------------> discarded
```

只有 `received` 的真实用户原话和 `delivered` 的实际回复能交给 M2 Curator。用户消息先保存（获同意情况下），**回复生成与投递不能作为先决条件**。每个消息源必须传入稳定 `source_message_id`；同一 ID 重放必须幂等，同 ID 内容变化要报错。

`curated_source_entries()` 除校验该人物是否拥有私聊外，还要求 `timeline` 与 `curation` 两项同意。撤销同意会禁止新的采集或整理，但不会自动删除已经保存的数据；`erase_private_history()` 明确执行私聊记录擦除。以后需要在一个原子化的“忘记某个人”流程中同步清除向量和已经提炼的记忆。

## 用法简例

```python
from pathlib import Path
from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import (
    IdentityRepository, ChannelIdentity, IdentityProof,
)
from zhuangfy.memory.timeline import TimelineRepository

db = MemoryDatabase(Path("data/memory/m1.sqlite3"))
identity = IdentityRepository(db)
timeline = TimelineRepository(db, identity)
person = identity.create_person("经过确认的测试人物")
claim = ChannelIdentity("desktop", "local", "test-person")
# 仅在可信本地界面/真实账号验证后才可以执行：
identity.link_verified(
    person.person_id, claim,
    IdentityProof("trusted_admin", "local-approval-audit-id"),
)
identity.set_consent(person.person_id, "timeline", True)
identity.set_consent(person.person_id, "curation", True)
session = timeline.open_private(
    agent_id="zhuangfy-main", identity=claim,
    external_conversation_id="private:test-person",
)
user = timeline.receive(
    conversation_id=session.conversation_id,
    identity=claim,
    source_message_id="input-001",
    content="我正在研究 OSG 港口帧率优化",
)
draft = timeline.generate_reply(
    conversation_id=session.conversation_id,
    in_reply_to=user.entry_id,
    source_message_id="reply-001",
    content="之前说过的场景还在排查吗？",
)
# 只有外部交付层收到真实成功确认后才能标记 delivered：
timeline.set_reply_state(
    conversation_id=session.conversation_id,
    entry_id=draft.entry_id, new_state="delivered",
)
```

## 重要限制及后续集成

- 目前没有 QQ 登录态或声纹认证接入，也没有验证挑战流程；**项目中只有结构化身份管理，不代表任何外部账号已完成认证**。
- 目前没有 Timeline UI、加密备份、过期自动清理、群聊支持；本地 SQLite 明文，测试使用虚构数据。
- 当前 QQ 多气泡需确认全部气泡送达才标记 `delivered`；超时不明不能擅自改成送达。
- M1 使用独立 SQLite，可在 M2 用同一数据库建立候选记忆与反思任务；原来的 Mem0/Qdrant 数据仍保持原样。
- `MemoryDatabase` 不应该在事务期间执行 LLM、Embedding 或网络请求，避免长时间锁定数据库。

## 验证

```bash
PYTHONPATH=src python -m unittest -v tests.unit.test_memory_identity_timeline
```

覆盖跨端关联、同昵称不合并、QQ 多 Bot 作用域、默认未授权、消息去重、实际送达状态、私聊隔离和主动删除。
