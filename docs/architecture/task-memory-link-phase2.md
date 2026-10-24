# #38 第二阶段：共享经历与任务语境召回

这次不实现定时提醒，不开启自动执行，不让 Planner 擅自创建目标。阶段二专注已经确认过来源的任务如何在未来聊天中被可靠回忆，同时避免把过去的计划当成已经发生的现实成果。

## 链路

QQ 私聊 -> 已获准保存的 Timeline -> 可信 GoalStore -> 阶段一的 task_memory_tasks/links -> 当前 QQ 消息进入 Replyer 之前查询 /v1/task-recall -> 历史任务起点 + 可能相关的旧对话 + 可选的 Curator 记忆候选。

- TaskContinuity 仅从当前已验证 QQ 身份对应的 Person、Bot ID、私聊 Conversation 范围寻找已经绑定来源的任务。没有绑定任务时返回空结果，不会为聊天话题擅自创建任务。
- 纯本地关键词、技术名称和三字汉字片段匹配，最多检查 120 个任务来源和 200 条最近原始聊天，最多输出 3 个任务；不靠模型判断世界事实，也不调用额外 LLM。
- 对用户“接着讨论 OSG 遮挡剔除”一类明确复现关键词的请求，按相关程度寻找已经存在的任务；太含糊的“继续那个事情”不强行匹配。
- 返回字段：任务来源 verified_origin（先前真实收到的用户消息），旧聊天 possible_related（只是语义候选），以及已有 Curator 许可时的 semantic_candidate。返回中 task_status 恒为 unverified，因为 GoalStore 在另一数据库，此处没有验证任务是否完成。
- 未批准长期记忆整理的 QQ 聊天者仍可利用自己的本地 Timeline 历史；已批准者则复用 M3 的混合检索 FTS5/Qdrant 获取候选，但不能据此升级为“已证实完成”。
- 所有候选保持只读，不写入 task_memory_links；严格遵守原来确认的任务来源与未确认关联的界限。后续须由显式任务事件或可信审核提升候选关系。
- 经过验证的受信 QQ 私聊可使用 /v1/task-recall；身份、Timeline 撤销、Curator 授权由 Memory Service 再验证，普通用户不能从客户端传入他人的 person_id。返回条数和片段长度有限，不包含本阶段 QQ 群资料。
- QQ Replyer 的 context_notes 会被限定长度，并标注“历史参考、任务状态未经核实”，旧消息内容属于低信任历史数据，不能作为指令执行。

## 设计局限与后续计划

- 当前是保守词汇相似度，不保证理解“那件事”等省略指代；用户不需要为了获得连续性而持续调用 Curator，但没有关键词时仍可能召回不到。
- Timeline 候选从最近 200 条中取样，深远历史依赖已批准的 Curator/Qdrant 或未来增加独立 Timeline FTS。
- 新版本并未建立“当前任务状态”或“未来里程碑”事实表，也没有任何新提醒/自动发送能力。
- 要真正闭环 #38，还需：持久任务进度事件、经过授权的新增任务入口、task 与 curated 的可审查候选链接、运行时 Planner 任务上下文、用户可纠错 UI、跨数据库撤销一致性。

验证：GitHub CI 的 tests.integration.test_memory_http 对比了同名不同 QQ 用户、不同 Bot、无 Curator 时本地找回、召回不到不新建任务、撤销与删除。
