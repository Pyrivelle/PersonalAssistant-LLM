# R1-B Relationship Core：证据驱动 + 人工纠正（独立后端）

R1-A 的 L0–L3 仍是表达参考。R1-B 首先落地持久化关系状态，不修改正在开发的流式 Planner、QQ、桌面或语音管线，也不默认开启关系追踪。本次不会让机器人自动把陌生用户升为恋人。

## 状态与初始选择

RelationshipCore(database, identities) 与现有 MemoryDatabase、IdentityRepository、TimelineRepository 使用同一个 SQLite 数据库。每个档案按 (agent_id, verified person_id) 隔离。身份必须来自现有已验证 ChannelIdentity；昵称相同或用户声称同一身份不是证据。

仅由认证过的用户控制面显式调用 enroll(agent_id, identity, initial_tier, allow_affection)，关系跟踪才会创建。起始 L0/L1/L2/L3 预置保守的熟悉度、信任和亲近度；选择 L3 需要明确允许亲近表达（allow_affection=True），且单独的 tier_ceiling 可以限制表达等级。未注册、关闭时 snapshot 返回 None。初始化的等级是**人为提供的大体起点**，不是对真实相处时长的断言。

RelationshipSnapshot 包含版本、三个维度、推导等级、表达许可/禁用称呼/语气偏好、自动成长和冻结状态。prompt_hint() 只产生短的宿主可信**表达建议**；不会说过去共同经历、不会授予 QQ 发信/Skill/Tool/主动联系权限。Planner 和 Replyer 的权限门控仍须在宿主中执行。

## 可解释的自主判断

用户授权自动成长后，可信的事件识别/验证服务可以使用 observe(...) 提交有限事件，必须指向**同一个人、同一个 agent 的真实 Timeline entry_id**。在一个 SQLite 写事务内再次确认 timeline consent、身份、来源角色和状态。缺少证据、未发送草稿、跨用户事件均不生效。

- shared_topic：已接收的用户消息，熟悉度 +2，至少 24 小时间隔。
- cooperative_exchange：某次已投递的助手回复之后，用户确实再次发来消息，该后续用户消息作为证据；信任 +2，至少七天间隔。只是谨慎的**互动信号**，不能据此断言用户信任或授予权限；后续可要求用户明确反馈进一步提高语义可靠性。
- affection_signal：已接收消息，由可信事件判断为亲近信号，亲近度 +1，至少七天间隔，且必须已允许亲近表达。

同一证据及事件类型不可重复计分；单事件至多几分，不会因聊天条数不停提升。数值与时间窗口只是第一版可调整的保守**算法参数**，不是心理量表。模型判断属于候选推断，不能作为既成事实；用户可以纠正它。事件日志只存证据 ID、维度变化及变更类型，不存原始私聊全文。

## 人工控制（均由已认证的 UI/宿主调用）

- correct(..., expected_version=...)：显式更正熟悉/信任/亲近度、tier_ceiling、亲近表达、称呼、语气、自动成长、冻结和启停；乐观版本控制避免覆盖晚到的更正。人工更正记录字段前后值，便于审计。
- frozen=True：立即暂停全部自动成长，不抹去既有状态。
- lock_familiarity / lock_trust / lock_closeness：可只冻结某一个维度，其他维度仍可依据证据独立变化。
- auto_growth=False：只保留人工设定，停用观察事件。
- allow_affection=False：禁止系统使用亲密关系表现；即便已有高亲近度也不能显示为 L3。
- tier_ceiling 可限制最高等级；若本人只允许自然朋友式交流，高熟悉/信任不会绕过。
- enabled=False：正常读取不到该关系档案；不等于擦除历史。
- erase(...)：删除这个 agent/person 的关系画像和关联关系事件记录，允许重新明确加入。

关系状态既不能赋予身份也不能替代用户真实隐私同意。尤其不能用 allow_affection 作为发送主动消息的授权。

## 与记忆事实 / Issue #30 的关系

- Timeline 的 received 只代表**有一条用户说过的话**，不证明其叙述为客观事实。
- Timeline 的 delivered 只证明某条助手回复**成功投递**，不代表工具动作成功。
- Relationship Event 是针对有证据的互动的**可纠正推断**，不等于“长期记忆已写入”。
- 真正的工具执行回执和 memory 持久化回执仍由对应子系统独立核验；参见 Issue #30。

必须在后续生产集成前，把现有 forget_me / Timeline 擦除操作和 RelationshipCore.erase 统一在认证服务层编排，确保私人历史清除后不会残留关联画像。本 PR 未改变现有删除入口；因此它不应立即启用生产追踪。

## 流式 Planner 与下阶段接入

本次**不修改** 2026-10-23 已推送的 Stream planner model responses，也不把部分 Planner token 写入关系核心。将来使用：消息到达 → 验证身份和 opt-in → 一次读取 snapshot(version) → 完整的 Planner 决策/Replyer 回复 → Timeline 投递结果 → 异步证据验证 → observe(...) 更新。快照可以传入已经存在的 R1-A 行为指导 tier，但不能直接把模型的推理片段当作关系事件。

离线验证：PYTHONPATH=src python -m unittest -v tests.unit.test_relationship_core_r1b。不请求 API，不读取真实私聊。R1-C 再扩展结构化候选事件提取、语义反证/纠错、UI 绑定、跨渠道主体控制和 E1.3 真实模型对照实验。
