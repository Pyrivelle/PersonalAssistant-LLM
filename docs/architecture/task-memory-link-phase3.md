# Issue #38 — 第三阶段：持续任务事件与状态演化

该阶段将先前已确认来源的任务与用户新的、真实发生的私聊报告关联；不是日程提醒、模型自动执行工具或自主观察现实世界。

## 核心模型

- TaskEventStore：新表 task_progress_events。每个事件绑定 task_id、person_id、source_entry_id；原始正文只保留在 Timeline，不复制在事件表中。事件时间拆分为 reported_at（聊天接收时间）与 occurred_at（尚无法证明的事件实际发生时间，默认 NULL）。
- 事件类型：started、progress、blocked、reported_completed、cancelled、correction。自动提取只处理强匹配、明确陈述；不生成工具已执行证据。
- 事件来源 user_report 的含义是“用户报告此事”，不是外部测试已经证实。
- review_state：accepted（强证据且唯一任务）、pending（含不确定词或多任务无法唯一对应）、rejected（经管理员否决）。
- TaskStateReducer 逻辑由 TaskEventStore.snapshot 实施：保留来源与按 Timeline sequence 排序的事件流，默认 proposed；仅 accepted 事件更新报告状态。经审核的 correction 使用新的、晚于原证据的用户原话，指向被取代的旧事件；不得原地改写历史。
- 管理员 review 可接受或拒绝 pending 候选，只能决定一次；若需要撤回已接受的进展，必须由明确更正的新消息建立新事件。
- 可信 QQ Bridge 接收到并去重的私聊先保存 Timeline，MemoryCore.receive 再用该真实 source_entry_id 执行**低成本确定性**事件提取。没有先前的可信 TaskMemoryLink 时不生成新的任务。入库同一来源、同一任务具备幂等约束。
- 任务源消息、后续消息、Bot ID、对方 QQ ID、Person ID、agent_id、user/received 状态都在写事务内核对；群聊、其他 QQ 人物、助手草稿与不受授权的 Timeline 不得改变任务进度。
- 关联匹配保守：无足够话题线索则不记录事件；多个相似项目只留待审核候选。这会牺牲部分自动召回率，避免错把“那个做完了”绑定到错误任务。

## API 与对话链路

- 客户端 /v1/task-state：凭受信渠道凭据按自己 task_id 检索经过来源验证的事件和报告状态；不是 GoalStore 真正的执行状态。
- 管理员 /v1/admin/task-event-review：明确批准或驳回 pending 事件。
- 管理员 /v1/admin/task-event-correct：使用真实且较晚的用户更正 Timeline entry、目标被更正 event_id 和新的报告状态追加更正事件。
- 原 /v1/task-recall 在候选持续任务里增加 reported_progress（state、status_basis、last_reported_at、最近三条事件），原 task_status 仍是 unverified。QQ 回复语境严格注明“用户报告的进展，不等于客观测试验证”，不会因此触发提醒或主动外发。
- Curator 是否调用及是否提炼为昂贵的长期记忆，仍由之前独立的 curation 权限决定；不改变默认的本地 Timeline 保存政策。
- 删除 Person、任务来源、原始证据后以 SQLite 外键级联删除状态事件；撤销 Timeline 会阻止读取、新事件关联与审核，避免遗忘后恢复历史。

## 已知边界 / 第四阶段

现阶段只处理事先明确存在的 GoalStore / TaskMemoryLinks 任务；聊天里新出现计划不会擅自创建任务。模糊“那件事”没有足够主题线索时，需要后续 Planner/用户澄清机制。发生时间、未来里程碑未实现可信结构化日期推断；未接 GitHub/本机工具回执，不声称完成客观验收。

下一阶段可增加人物任务事件审核 UI、重复项目关系消歧、Task 与 Episode 双轨时间线、可信工具回执、通用渠道身份适配。Issue #38 保持 Open，待持续任务端到端能力验收后才关闭。
