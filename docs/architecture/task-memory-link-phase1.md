# #38 — Task–Memory Link 阶段一：可信任务来源关联

本阶段只建立持久、可验证的任务到原始聊天证据关联。它不自动创建目标，不做向量匹配，不修改 Curator，不因普通闲聊触发任务，也不会授予工具执行权限。

## 关键事实和身份约定

- GoalStore (src/zhuangfy/agent_v2/goals.py) 与 Memory Core 使用不同 SQLite 文件。GoalStore 的 source_event_id 可以是任意来源标识，绝不能默认为 Timeline entry_id。
- 阶段一要求在创建 GoalStore 任务时，显式使用真实 MemoryClient.receive(...) 返回的 entry_id 作为 source_event_id，而不是 OneBot 原始 message_id。
- 仅允许 QQ 私聊身份；使用无歧义 owner_scope 格式 qq:<bot_id>:private:<user_id>。旧版 qq:private:<id>、群聊和桌面暂不支持任务绑定。
- TaskMemoryLinks 只接受可信管理员绑定。模型、QQ 客户端不能提交任意 task_id 进行认证；绑定不赋予工具权限或主动发言权限。

## 数据与权限

- task_memory_tasks: task_id (PK)、person_id、agent_id、owner_scope、origin_entry_id (ON DELETE CASCADE)、created_at。
- task_memory_links: task_id、entry_id (FK)、relation、linked_at。第一阶段创建 origin 关联，为之后 follow_up 留接口。
- 绑定在一个 SQLite 写事务里验证渠道身份、Person、Bot、私聊对方、来源 user/received 状态与 Timeline 授权。
- 同一个 task_id、同一证据重复绑定幂等；试图用同一 task_id 绑定另一来源或人物一律拒绝。
- 撤销 Timeline 后禁止读取关联；删除历史原文或人物后任务关联被 FK 级联清除。独立 GoalStore 自身不被删除，跨库清理仍需下一阶段协调。
- 不复制原始聊天正文与向量；只有已验证参与者可读取相关来源 ID、原始消息 ID、时间及 relation。

## API

管理路由 POST /v1/admin/bind-goal-origin: identity、task_id、owner_scope、source_entry_id；必须使用管理员 token。

客户端路由 POST /v1/task-origin: identity、task_id；重新检查 Person、QQ owner_scope 和 Timeline 授权。不返回其他人的关联。

## 操作员 GoalStore CLI 的可选绑定

任务创建时使用 --source-event-id <真实Timeline-entry_id>；需要已有的 policy、被许可的工具与 --consent-confirmed。选择同时绑定则额外使用 --link-memory --memory-bot-id <botQQ> --memory-user-id <userQQ>。

仅可信本地操作员环境需设置 ZHUANGFY_MEMORY_TASK_LINK=1 及 ZHUANGFY_MEMORY_ADMIN_TOKEN，并保持 Memory Service 运行；禁止把管理 token 传给 QQ Bridge。

本地 bridge 会先 inspect GoalStore，确认 goal 存在、获准、非撤销/过期、owner_scope 匹配，再将来源交给 Memory Core 再次检查。

GoalStore 与 Memory Core 不是单个 SQLite 事务，故创建成功、关联调用失败时需要重试原 add 命令；创建操作是幂等的，不能在关联未确认时宣称成功。这里尚未实现 outbox 自动重试。

## #38 尚未完成的内容

下一步需要让 Planner/TaskManager 产出的新任务自动接入此可信桥接、候选检索 Timeline/Curator/Qdrant、区分语义候选和确认关联、记录进度与真实执行回执、在 UI 显示并更正任务关联、协调跨库删除。

离线回归命令：python -m unittest -v tests.integration.test_memory_http
