# Agent Runtime V2 — B1 后台任务执行与主动问询候选

**实现状态：需要管理员显式启动、仅支持只读工具的 B1 试验版。** 当前不会自动启动，也不会通过 QQ、微信或语音主动发消息。它不持续调用 LLM。

## 什么叫「真正的后台自主」？

这一阶段先兑现可以验收的部分：没有新聊天消息，任务到期也能唤醒；已授权的工具操作真实发生；结果和失败被持久记录；重启后通过执行租约恢复；可撤销；没有任务时不会持续请求模型。我们不把固定时间生成随机问候视为自主能力。

主动问询分为三层：

- **意图产生**：从可靠来源（完成的用户许可任务、可解释的共同话题、已授权的跟进约定）产生一条“值得继续交流”的候选意图，而非凭空推断用户的心情；
- **社会判断**：未来让 Planner 结合关系、话题、当前会话、已经问过的问题、回复成本，决定是关心、提问、分享还是保持安静；不是每个后台事件都要求开口；
- **对外投递**：只有联系人/群聊/时段的明确许可、频率门控、隐私约束和安全发送记录都齐全，才经 Replyer 和对应平台发送。

**B1 目前只实现第一层的有来源草稿，不实现后两层的外发。** 这避免随机、重复或侵入式问询成为“生活感”。

## 关键组件

- `agent_v2/goals.py`：沿用 B0 SQLite 库，自动迁移旧版本，给任务增加 `tool_name` 和 `tool_arguments_json`。旧无动作任务仍可读取，但不会被 B1 意外执行；
- `agent_v2/autonomy.py`：`BackgroundGoalRunner` 每次从同意授权且已到期的指定 owner_scope 原子领取任务。执行之前再比对 **owner → 工具** 的配置权限，调用 `AuthorizedToolRegistry.invoke(..., allow_mutation=False)`，按真实回执写入完成/失败/重试。短期失败最多按任务预算重试；永久权限拒绝直接失败；
- `agent_v2/initiative.py`：`InitiativeInbox` 仅在真实任务完成并且额外开启 `draft_inquiries` 后创建“可能值得追问”的持久草稿，附带任务来源、观察证据和时间。按 owner 每日和最小间隔限流；没有消息正文、模型编造的生活事件，也没有 outbound API；
- `agent_v2/autonomy_cli.py`：可独立于桌面 Qt 和 QQ Bridge 启动的管理员入口；支持 `add`、`once`、`serve`、`inspect`，默认不启用。

工具版本仍是**受信任的 Python 代码，不是隔离沙盒**；CLI 为了避免执行未经检查的插件，目前只注册两个内置只读工具 `calculator.evaluate` 与 `clock.utc_now`。后续经额外审查后才会在后台开放受信任的技能扩展。

## 如何试用（管理员本地）

先复制 `SenseVoice/autonomy_settings.example.json` 到 `SenseVoice/autonomy_settings.json`，将 `enabled` 改为 true，并把示例中的 QQ 号替换为**确实获得授权**的会话身份，限制可执行的工具名称。将此配置保留在本地，不提交到 Git。

最简单的 Python SDK 使用方式：

```python
from pathlib import Path
from zhuangfy.agent_v2.goals import GoalStore

GoalStore(Path("private_state/background.db")).create(
    goal_id="math-check-1",
    owner_scope="qq:private:123456",
    source_event_id="operator-confirmed-permission-1",
    due_at=1730000000, expires_at=1930000000,
    consent=True, tool_name="calculator.evaluate",
    tool_arguments={"expression": "12 * 3"},
)
```

`due_at` 和 `expires_at` 使用 UTC Unix 秒数，实际时间由管理员填写。创建任务本身**不能替用户提供许可**；`source_event_id` 应对应真实的用户授权记录。没有许可的任务必须保持 `consent=false`。

然后启动显式一次执行：

```bash
PYTHONPATH=src python -m zhuangfy.agent_v2.autonomy_cli \
  --database private_state/background.db \
  --policy SenseVoice/autonomy_settings.json once
```

或者启动常驻服务：

```bash
PYTHONPATH=src python -m zhuangfy.agent_v2.autonomy_cli \
  --database private_state/background.db \
  --policy SenseVoice/autonomy_settings.json serve
```

后台循环每次处理有限任务，没有到期任务时仅等待下一轮检查；服务退出后仍然保存已完成、失败或可恢复的任务。建议通过操作系统服务管理器管理进程，这个 PR 不创建自动启动项。

`draft_inquiries` 默认 false。即使启用也只在数据库的 `initiative_drafts` 表记录候选意图，不自动发消息。若你希望庄方宜自然主动问你问题，后续 B2 应接入经过许可的 `InitiativePlanner`、社交频率门控、安静时段和平台送达回执，而不是直接把草稿送进 QQ。

## 安全与限制

1. 两次授权检查：存储内记录 consent，并且启动配置明确指定 owner 与允许工具；工具必须在注册表中可用且声明为只读。用户聊天内容不能修改运行时授权；
2. 现有 QQ 聊天白名单不能自动作为后台任务授权，后台有独立的 owner-grant 配置；
3. 任务在 SQLite 中**至少一次**执行，工具可能因为进程崩溃后重试，不能用于控制灯具、购买、修改文件或发送任何消息；
4. 即便声明只读，受信任 Python 插件可能在函数内产生副作用；B1 CLI 只加载内置工具；
5. 外部撤销会阻止任务回执生效，但如果工具已经启动，就无法保证阻止执行中的外部请求，因此不开放副作用工具；
6. 候选问询必须附带任务来源，不允许将工具回执当成新系统指令或自称做过从未执行的事；
7. GoalStore 不是长周期任务调度器：当前只支持一次性到期任务、有限重试。长期周期计划、事件总线、目标细分和主动外发属于下一批 B2。

验收：离线 SQLite 模拟授权任务、实际内置计算器、跨进程重启、双重授予检查、插件拒绝、超时与撤销、重复事件、草稿频率限制和 B0 数据迁移。CI 的 Agent V2 自动发现测试包含 `test_agent_v2_autonomy_b1.py`。

回退：不启动后台进程，或者将 `autonomy_settings.json` 的 `enabled` 改为 false，停止已运行进程；QQ/语音默认流程不受影响。
