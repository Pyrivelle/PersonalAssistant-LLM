# E1.2：私人对话 Planner + Replyer 分阶段诊断

本测试集使用 14 个完全合成的 QQ 私聊场景，不包含武陵、游戏设定问答或任何真实聊天记录。与原 E1 32 场景分离，原评估入口和测试数据不变。

## 测试过程

使用同一选定云模型执行真实 `plan_turn_v2`，保存经过校验后的 `PlanDecision`。由宿主 `choose_action` 确定 `reply/wait/stop`，不授予工具权限。仅当动作是 `reply` 时，将 Planner 的 `plan.prompt()` 与 CCV2/R1 约束传给真实 `invoke_stage("reply")`，记录原始回复，然后调用 `tidy_r1_reply` 得到最终文本。

HTML 和 JSON 显示：输入、合成历史、Planner 结构化决定、宿主动作、Replyer 指导、原始回复、R1 清理后回复及简单括号提示。Planner 结构化决定不是隐藏的模型推理内容。括号提示只是启发式筛查，技术括号等假阳性需要人工区分。

该测试模拟 P+R 回复路径，并不启动真实 QQ 桥接、Focus、多轮等待、消息发送、持久记忆或工具执行。因此 `stop` / `wait` 的模拟结果不能替代端到端投递测试。

## GitHub Actions

合并包含 `.github/workflows/character-e12-private-pr.yml` 的 PR 到默认分支后，在 **Actions → Character E1.2 - Private Planner + Replyer (manual) → Run workflow** 选择模型、服务商和 3/7/14 个场景。使用已有仓库 Secret `ZHUANGFY_E1_API_KEY`。模型调用会收费（通常每个回答场景需要 Planner + Replyer 两次调用）；默认只运行 3 个场景。报告 Artifact 名称为 `character-e12-private-pr-report`，保留 14 天。

## 本地运行

确保在仓库根目录使用 Python 3.12，准备模型所需依赖并在当前终端中配置 `ZHUANGFY_E1_API_KEY`（不要提交密钥）。

```powershell
$env:ZHUANGFY_E1_API_KEY = '你的测试接口密钥'
python scripts/character_e12_private_trace.py --provider siliconflow --model '你的完整模型ID' --max-cases 3 --allow-network
```

生成的本地报告在 `build/e12-private/report.html`、`trace.json`、`summary.md`。建议先运行 3 个场景确认模型调用正常，再运行 14 个。跨版本对照应固定模型 ID、提示词版本、测试数据和环境，不要将 Pro 与 Flash 的差异误判为改造效果。生产环境是否启用 R1 是独立配置，本测试显式模拟启用 R1。

## 数据安全

不要把真实私聊内容复制进公开测试集，不要把含隐私的真实模型报告提交到仓库。所有模型错误仅记录异常类名，避免 API 客户端错误携带敏感文本。本工具只产生本地输出与 Actions Artifact，不给任何人发送 QQ 消息。
