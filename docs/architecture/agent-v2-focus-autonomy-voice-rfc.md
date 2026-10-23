# RFC: Focus 注意力、后台自主行为与语音轮次

**状态：F0 原型和架构设计。并非完整 Maisaka Focus，也未接管 QQ/语音。**

## 1. 后台自主的精确定义

后台常驻不等于持续调用 LLM；定时随机发言也不等于自主行为。ZhuangFY 的后台自主需要同时具备：**事件独立性、目标延续性、行动选择权、结果反馈和授权边界**。

| 等级 | 行为 | 初始策略 |
|---|---|---|
| L0 | 收到消息后响应 | 已有基础 |
| L1 | 到期任务、插件事件等唤醒 | 单项明确授权 |
| L2 | 持久目标、多步骤执行、工具结果反馈、重启恢复 | 有预算和撤销 |
| L3 | 根据社交场合决定主动参与或不参与 | 分平台/联系人/群授权 |
| L4 | 有范围的长期学习、计划和活动组织 | 长期目标，不默认开启 |

最重要的区别是 **自主行动不等于自主发消息**。她可以完成一次获授权的记忆整理而无需通知，或者在没有理由行动时安静等待。后台经历必须有真实任务/回执支持，不应由模型编造。

建议最小运行循环：

Event Source (chat/timer/plugin/tool result) → dedupe + permissions → Focus → Trigger Scheduler → Goal/Planner → authorized Tool Runtime → receipt + checkpoint → optional Replyer/Dispatcher → sleep until next event.

后台任务数据草案：goal_id, owner_scope, source_event_id, consent, status, next_wake_at, resource_budget, last_receipt, checkpoint_version, expires_at。需要幂等、过期/撤销、任务重启恢复、安静时段、对外发言预算、审批。权限分为 observe（只读）、draft（草案）、act（实际副作用），其中发主动私信、改动设备和对第三方发送内容额外授权。

## 2. Focus 多会话

参考 [Maisaka FocusModeManager](https://github.com/Mai-with-u/MaiBot/blob/main/src/maisaka/focus/manager.py) 和 [FocusGroupRuntime](https://github.com/Mai-with-u/MaiBot/blob/main/src/maisaka/focus/group_runtime.py)。Maisaka 能使多个聊天流共享一个前台 Planner，但不等于共享所有聊天数据。

本 PR 新增 A6 F0 的 **agent_v2/focus.py**：
- FocusGroup 明确列出允许共享注意力的会话，不自动将 QQ / 微信身份绑定；
- FocusSignal 只包含会话、事件 ID、时间、优先级、是否直接提及等**元数据**，没有消息正文和记忆；
- 一个 Focus 组最多一个活动 lease，支持定向消息优先、老化加权、公平轮转、队列限额、事件去重、取消和只读快照；
- 默认拒绝未授权事件，跨会话消息必须由原会话权限逻辑读取。

**本阶段尚未**将其接入 QQ 正式 Planner，也没有实现持久化 Focus 检查点、跨平台真实连接、共享工具回执路由或完整上游 FocusGroupRuntime。必须先验证多会话权限和排序，再用 feature flag 在 QQ 做 shadow/active 两阶段接入。

## 3. 语音轮次管理

目前 Qt/SenseVoice VAD 会输出单段 ASR 结果；多段短停顿可能形成多次 Planner 请求。目标是把 VAD 分段和人类对话轮次拆开：

Mic/VAD → final ASR segments → VoiceTurnManager (合批/端点/中断/等待上限) → UnifiedMessage → Event Scheduler / Focus → Planner → Replyer → TTS / barge-in。

原则：ASR partial 不逐字触发 Planner；短暂停顿可以合并，长停顿也有最大等待时限；识别到真实插话时取消旧输出，但不得假装云端请求已被取消；防扬声器回声与唤醒权限原逻辑仍有效。Qt UI 主线程不能因调度或工具调用阻塞。

## 4. 下一步开发里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| F0（本 PR） | 元数据 Focus 仲裁 + 本设计 | 隐私隔离、单活动 lease、公平性/去重/背压测试 |
| F1 | QQ Focus shadow → 可选 active | 不串上下文、不重复执行 Planner；可随时关闭 |
| F2 | 会话分组/持久 Checkpoint/微信适配 | 重启恢复，权限隔离 |
| V0 | 独立 VoiceTurnManager | 连续 ASR 合批、超时、打断测试 |
| V1 | 接入 Qt/SenseVoice | 不阻塞 GUI，迟到回复不播放 |
| B0 | 授权事件源 + Goal Store | 无新聊天也可执行只读到期任务、重启恢复 |
| B1 | 持续 Goal/Tool/反馈循环 | 限额/撤销/失败暂停、执行回执真实 |
| B2 | 场合感知的主动社交 | 群/联系人授权、安静时段、频率、安全可审计 |

指标：每事件 LLM 调用数、P95 回复延迟、语音分段合并正确率、重复执行次数、未授权工具执行数、私人信息跨聊天泄漏数（目标 0）、无用主动消息比例。

所有内部目标、情绪或活动状态都是**软件状态**，不是关于角色具有真实意识的声明。
