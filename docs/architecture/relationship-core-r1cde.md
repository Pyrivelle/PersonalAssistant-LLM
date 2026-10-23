# R1-C / R1-D / R1-E：候选关系事件、真实接入与评测

## R1-C：分离模型判断与可信事实

RelationshipEvolution 分两个阶段。propose() 只能基于经授权、已验证身份的真实 Timeline 用户消息提出 event_kind、confidence 和 evidence_quote，不修改关系。apply() 从 SQLite 重新检查 person/agent、消息状态、原文引用、最低置信度 0.9、版本、冻结、亲近许可及冷却时间。模型不能直接写分数、授权亲昵称呼或执行工具。

事件种类：shared_topic（实质话题延续）、cooperative_exchange（已经投递回复以后收到用户后续消息）、affection_signal（明确的亲近邀请，但不能自动授予亲近许可）。事件只能提供有限、可回滚的推断，不是用户信任或客观历史的正式证明。

## R1-D：真实 QQ 关系快照与控制

本模块共享现有 MemoryDatabase / IdentityRepository / TimelineRepository，使用已验证 ChannelIdentity，不匹配昵称。在原有 MemoryClient、Timeline 已授权且当前私聊成功记录之后，QQ 每轮只读取一个 R1 关系快照，Planner 和 Replyer 使用同一个 tier 与 hint；不存在快照则使用 unassigned。原流式模型响应读取方式不变，任何 Planner 中间 token 均不会写入关系数据库。

客户端接口：/v1/relationship/snapshot、/v1/relationship/apply（提交候选，服务端重新验真）。仅管理员 token 可调用 /v1/admin/relationship/enroll（设置初始 L0-L3）和 /v1/admin/relationship/correct（显式修正、最高等级、亲近许可、昵称限制、冻结、单维度锁与关闭）。不要将管理员 token 提供给 QQ 或桌面客户端。

除了已经存在的 R1-A 和 Memory QQ 开关，启用实际事件候选的额外配置是 relationship_evolution_r1_enabled=true，默认 false。开启后每条已成功投递并记录于 Timeline 的 QQ 私聊最多触发一次额外模型候选调用（在失败情况下路由重试仍可能发生），模型/服务失败不阻断已投递的回复。自动成长只影响低幅度、多日积累的关系维度，不授予发信、工具或查询他人历史的能力。

完整的本地 /forget_me 将关系档案和事件表纳入 MemoryCore 的同一 SQLite 擦除事务。管理员 forget_person 删除对应 Person 时通过外键级联处理。不能覆盖既有外部备份与未集成的旧版记忆文件。

## R1-E：真实模型测试

原 E1.2 合成私聊测试增加关系参数，不读取真实私聊。可通过 relationship_tier 选择 unassigned / L0 / L1 / L2 / L3；evaluate_events 为 true 时额外记录模型提出的关系候选、原文引用及是否通过基础审查。候选不会在 E1.2 报告生成时入库。

PowerShell 示例：

    git fetch origin
    git switch feat/r1b-evidence-relationship-core
    $env:ZHUANGFY_E1_API_KEY = '测试密钥'
    python scripts/character_e12_private_trace.py --provider siliconflow --model 'deepseek-ai/DeepSeek-V4-Flash' --relationship-tier L1 --max-cases 3 --evaluate-events --allow-network
    Invoke-Item .\build\e12-private\report.html

也可以在 Actions / Character E1.2 - Private Planner + Replyer (manual) 中选择此 PR 分支、Flash 模型、L1 和 evaluate_events，先跑 3 项再跑 14 项。需要仓库 API Secret，真实模型测试产生 API 费用。

当前 HTML 显示 Planner 决策、Replyer 原始文本、R1 最终文本与关系候选；是否真正改善长期交流，需要进一步人工审核。离线测试覆盖了身份、跨用户隔离、候选依据、手动干预、冷却、实际投递、HTTP 与遗忘。

## 下一个独立工作：联网搜索 Skill

搜索的权限、联网返回值的可信度、页面提示注入、事实引用、预算/超时和工具执行回执，应单独设计。不能将搜索网页内容直接当用户记忆或关系成长证据；Issue #30 的记忆/执行回执区分仍为重要待办。

### 通过受信任的本地管理终端调整关系

管理员应单独设置 ZHUANGFY_MEMORY_ADMIN_CLI=1、ZHUANGFY_MEMORY_ADMIN_TOKEN 与 ZHUANGFY_MEMORY_URL，并启动已验证身份的 Memory Service；不要在聊天中发送这些凭据。管理程序只连接 loopback，不会运行 LLM。

例如以下命令中的 bot-id 和 qq-id 必须是已在身份服务中完成验证的真实渠道标识：

    python scripts/relationship_admin.py --platform qq --account bot-id --user qq-id status
    python scripts/relationship_admin.py --platform qq --account bot-id --user qq-id enroll --tier L1
    python scripts/relationship_admin.py --platform qq --account bot-id --user qq-id events
    python scripts/relationship_admin.py --platform qq --account bot-id --user qq-id correct --version 1 --set frozen=true
    python scripts/relationship_admin.py --platform qq --account bot-id --user qq-id correct --version 2 --set lock_closeness=true --set tier_ceiling=L1

删除画像需再次明确传入 erase --yes-erase；此操作不删除其它 Memory Core 数据。只有对应关系 Version 一致时人工纠正才会成功，避免旧指令覆盖新的自然成长。
