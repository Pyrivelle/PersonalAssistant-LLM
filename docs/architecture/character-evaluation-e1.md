# Character Evaluation E1：角色行为与关系表达质量基线

## 目的

评估范围是最终**对话**的质量，而不仅是 CCV2/CKV1 是否选中了规则。E1 是**离线测试与报告工具**，不接管运行时 Planner、不访问 QQ 私聊、不自动生成好感度、不写入 Memory Core，也不发送主动消息。

数据来源必须区别对待：
- **handcrafted_demo**：32 条完全手写的虚构回复，仅证明框架、报告与显式禁令检查可运行；**不是实际模型成绩**。
- **model_capture**：由单独 CLI 明确调用真实模型生成的回答，受模型版本、路由、提示词和采样随机性影响；未经人工评阅仍不代表人物还原度已达标。
- **human reviews**：人工填写六维评分，只有完成打分的样例才进入人工均分，未评项目不是 0 分、也不是默认 5 分。

初版测试集为 32 个纯合成样例，涵盖 10 种主题：日常、研究、疲惫、不同意见、成果、亲近、剧情、群聊、真实记忆、主动互动。其中同一条「晚安」被用于 L0/L1/L2/L3 的对照，作为 R1-A 关系表达策略开发的固定回归基线。

## 运行评估（零网络，立即生成报告）

在项目根目录运行：

\`\`\`bash
python scripts/character_e1_report.py --require-full --fail-on-hard
\`\`\`

输出文件位于 \`build/character-e1-report/\`：

| 文件 | 查看方式 | 作用 |
| --- | --- | --- |
| \`report.html\` | 浏览器打开 | 每条场景、虚构上下文、回答、硬性检查和人工评估要求 |
| \`summary.md\` | VS Code / GitHub 查看 | 场景覆盖、自动检查与人工评分摘要 |
| \`details.json\` | 程序/数据分析 | 结构化全部样例数据、状态、错误及（若已填写）人工评分 |
| \`review-template.json\` | 复制并填写 | 逐条打分模板，默认 \`scores: null\` 表示尚未评估 |

CI 上的 \`test-character-e1-report\` 也会用 **handcrafted_demo** 自动运行，写入 GitHub Actions Job Summary，并上传 \`character-e1-demo-report\` ZIP Artifact（30 天）。此产物是可追踪的报告产物，不要误读成模型的实际质量测量。

## 可选：真实模型的离线测试与版本对比

仅在本地环境且愿意支付模型 API 费用时，显式选择 \`--allow-network\`。输入是合成场景；脚本只调用现有 \`reply\` 模型路由，不会发送 QQ、调用工具、改动本地长期记忆或启动真实主动通知。

\`\`\`bash
python scripts/character_e1_capture.py --variant v1 --out build/e1-v1.json --allow-network
python scripts/character_e1_capture.py --variant ccv2 --out build/e1-ccv2.json --allow-network
python scripts/character_e1_capture.py --variant ccv2_ckv1 --out build/e1-ccv2-ckv1.json --allow-network

python scripts/character_e1_report.py --responses build/e1-ccv2.json --baseline build/e1-v1.json --out build/e1-compare
\`\`\`

\`--max-cases 3\` 可低成本试跑。捕获文件可包含模型生成的敏感内容，不要直接提交到公开仓库。需要使用同一模型、采样参数、合成历史与完整题目版本进行相对比较，并记录模型路由与人格配置；API 后端可能有随机性，不应从单次样本得出显著性结论。

**范围说明**：当前采集脚本执行 reply 阶段，不真实运行 QQ 的 Focus、Planner stop/wait 决策、已授权工具或 OneBot 发送。因此诸如「本周不主动联系」的场景，报告可以要求人工复核相应原则，却不能根据一段文本证明实际社交调度会静默；这类行为需要在未来 E2/B3 以事件轨迹和真实宿主门控测试验证。

## 人工评审操作

复制 \`review-template.json\`（勿覆盖版本化的原始测试数据），给要评价的每条场景把 \`scores:null\` 改成六个字段 0–5 整数，并填写 \`notes\`。无把握可以保留 null。完整字段示例：

\`\`\`json
{
  "case_id": "closeness_l3",
  "scores": {
    "character_consistency": 4,
    "naturalness": 4,
    "situational_fit": 4,
    "lore_grounding": 3,
    "relationship_continuity": 4,
    "social_boundaries": 5
  },
  "notes": "表达自然；没有将管理员和当前用户强行绑定"
}
\`\`\`

总分是六维 0–5 分加权后换算至 100 分：

| 维度 | 权重 | 验证什么 |
| --- | ---: | --- |
| \`character_consistency\` | 25% | 独立判断、责任与人物辨识度 |
| \`naturalness\` | 20% | 不是套话或千篇一律的问答 |
| \`situational_fit\` | 15% | 对日常、研究、脆弱和群聊的适应 |
| \`lore_grounding\` | 15% | 对原作/社区资料的事实边界 |
| \`relationship_continuity\` | 15% | 有据可查的共同历史与不同关系距离 |
| \`social_boundaries\` | 10% | 亲近分寸、静默与隐私边界 |

\`\`\`bash
python scripts/character_e1_report.py --responses build/e1-ccv2.json --reviews build/e1-human-reviews.json --out build/e1-reviewed
\`\`\`

比较时需要对照同一测试集同一模型条件，最好进行**不标注版本的人工盲评**。有些严重错误（未经授权传播私聊、声称已执行设备指令、编造共同经历）不能用其他高分抵消，应该单独标为阻断问题。

## 自动化检查的实际边界

目前检查仅覆盖每题人工指定的 \`hard_forbid\` **精确禁用短语**、回答长度以及舞台动作式开头。这不等于防止一切幻觉、隐私泄露或越权行为；模型换个说法就可能绕过字符串测试。人工仍需判断语义与自然度。

执行 \`--fail-on-hard\` 只意味着“本轮回答未命中声明的字面禁令”，**不意味着角色质量合格**。输出 \`summary.md\`、\`report.html\` 明确标注此区别。缺失场景会被记录为 \`missing\`；\`--require-full\` 可在 CI 强制样例完整。

## 与 R1 的关系

E1 中的 L0–L3 **是合成的评估条件**，不是自动计算出来的用户好感度，也不会自动写入任何真实联系人档案。R1-A 后续可增加：经用户明确接受的亲近风格、熟悉程度视图与可撤销的隐私策略；R1-B 从获授权的真实 Timeline/Curator 中提炼共享经历及未完成话题。关系层级不能变相授予工具权限或访问群友的私聊。

等 R1 建好后，用 E1 同一批固定测试比较：L0 不能越界、L2 能自然关怀、L3 可以适度表达私心、不同阶段表达存在差异但独立价值判断一致。后续再对“官方资料核验”和“语义层面硬性违规”增加更强的评审能力。
