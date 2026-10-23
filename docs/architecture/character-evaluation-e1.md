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

# 两份回答都已有各自人工盲评后，另可指定两份评分：
python scripts/character_e1_report.py --responses build/e1-ccv2.json --baseline build/e1-v1.json --reviews build/e1-ccv2-reviewed.json --baseline-reviews build/e1-v1-reviewed.json --out build/e1-rated-compare
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


## 手动触发 GitHub Actions 真实模型评测（云端）

工作流：\`.github/workflows/character-e1-real.yml\`。只响应 \`workflow_dispatch\`，**不会因普通 push / PR 消耗模型费用**。该工作流必须进入 GitHub 仓库**默认分支 main**，才会出现在 Actions 的手动运行列表里；仅在未合并的功能分支上提交尚无法在界面直接点击 Run workflow。

### 一次性配置（仓库所有者操作）

1. 从 ZhuangFY 本地模型设置界面确认当前调用的**服务商**和**完整 Model ID**。GitHub 云端 Runner 无法自动读取你自己电脑里 \`SenseVoice/llm_settings.json\` 或 \`.env.local\`。
2. 在 GitHub 仓库打开 \`Settings → Secrets and variables → Actions → New repository secret\`，新建唯一名称为 **\`ZHUANGFY_E1_API_KEY\`** 的 Secret，值为对应云服务商的 API Key。**不要贴入 issue、PR、聊天消息、工作流参数或仓库文件。**
3. 合并包含该 workflow 的 PR 后，在 \`Actions → Character E1 - Real Model (manual) → Run workflow\` 中选择分支 \`main\`，并填写：
   - \`provider\`：\`siliconflow\`、\`deepseek\`、\`kimi_cn\`、\`kimi_global\` 或 \`hachimi\`（请选择与 Secret 匹配的服务商；这批未接入任意 Custom Base URL）；
   - \`model\`：来自 ZhuangFY 实际配置的**完整模型 ID**；
   - \`variant\`：\`v1\`、\`ccv2\` 或 \`ccv2_ckv1\`；
   - \`cases\`：建议首次选 **3**，通过后再选 **10** 或 **32**。
4. 运行结束后进入对应 Actions Run 的 **Summary**，页面下方 **Artifacts → character-e1-real-report** 下载 ZIP，解压并打开 \`report.html\`。包含 \`model-capture.json\`、\`details.json\`、\`summary.md\` 和人工打分模板；部分模型请求失败时也会尝试上传已经捕获的结果。

### 可比性和安全限制

- 当前手动流程调用的是**真实的云端 Replyer 模型**，并非手写示例；不过仍然不执行生产 QQ/Planner 的完整工具和消息投递链。对主动静默、提醒是否真的执行等，应在后续事件轨迹级别测试。
- GitHub Actions 云端运行时统一加载本仓库版本化的 \`resources/personas/zhuang_fangyi_v1/prompt.md\`，并按所选版本注入 V1/CCV2/CKV1 规则；**不会自动带入本地自行编辑的私人人格 Prompt**。若希望完全复刻本地自定义人格，需要另行设计专用的受控配置传输，不应把私密聊天或凭据提交到仓库。
- 工作流使用固定的官方 API 域名与选定的 Model ID；不会读取 \`model_routes.json\` 的其他候选，更不会自动跨模型 fallback。由此可避免名义上测试模型 A，实际回退到模型 B。
- \`ZHUANGFY_E1_API_KEY\` 只在工作流环境中读取，不写入输出报告、磁盘配置或 Actions Summary。使用普通云服务商需要有效的、可能计费的 API 密钥；仓库人员可能有权限下载结果 Artifact，请坚持仅用**合成测试场景**。
- 报告不自动生成“模型还原度得分”。\`hard_failures\` 只检测题目声明的禁用字面串；自然程度与人物性格需通过 \`review-template.json\` 完成独立人工评阅。
- 每次真实评测要注意产生的模型调用成本与时间。工作流只手动执行，含 3/10/32 三种明确上限，最大运行时限 65 分钟。保存的报告 Artifact 默认保留 14 天。



## E1.1：长推理模型超时、逐题恢复与备用 API

2026-10-23 实际运行中，DSV4 Pro 在第 4 个案例发生两次单次 25 秒超时，使 32 题任务中断。E1.1 为评测**独立**设置超时（默认 75 秒，可选 45 / 75 / 105 秒），不更改 QQ 的普通回复时限。

- **失败隔离**：一题 API 失败记录为 \`api_failed\`、\`timeout/network/rate_limited/auth/service_unavailable/invalid_reply\` 等固定代码；继续后续题目。即使一题都没有成功，仍会持久保存 \`model-capture.json\`，生成反映实际失败情况的报告。存在 API 失败则采集步骤返回非 0，但 \`always()\` 报告、Artifact 步骤仍执行。这表示评测没有完整成功，不是模型人格质量不合格。
- **断点续跑**：Actions 手动运行增加 \`resume_run_id\`（默认 \`0\`）。填之前 \`Character E1 - Real Model (manual)\` 测试的 GitHub **Run ID**（不是 Artifact ID），工作流自动取其 \`character-e1-real-report\` 中的 \`model-capture.json\`，复用已经成功的案例，仅运行未成功和未执行的案例。允许从 3 题扩展到 32 题。更改模型、服务商、角色 Prompt、评估集、人格版本或备用服务配置时**拒绝复用**，防止不同基线混合。历史的 3/32 捕获也可以恢复（如果元数据满足校验）。
- **备用 API 可选，默认关闭**：在 GitHub Actions Secret 中单独添加 \`ZHUANGFY_E1_BACKUP_API_KEY\`（仅在希望启用备用时需要）。然后在手动运行中填写 \`backup_provider\` 与 \`backup_model\`；当主 API 的请求阶段因**超时、网络失败或 429 限流**失败时，才尝试备用 API。认证失败不调用备用。两个 Secret 都不会被输出到结果文件或日志。
- **真实性与可比性**：每条捕获回答现在显式记录 \`origin: primary|backup\`、\`provider\` 和 \`model\`。一份报告可能混合不同模型回答，**混合报告不应直接当作主模型在 32 题上的角色质量成绩**，而需要分别人工评阅、或者关闭备用模式补测主模型。E1.1 不自动将备用模型分数计算为主模型分数。
- **真实回答原样保留**：例如开头 \`（看着窗外……）\` 现在会被 E1 标记为 \`stage_direction_opening\`，以便后续优化角色 Prompt/显示处理；E1 不悄悄修改原始回答，也不会仅凭动作旁白存在就宣称整体人格失败。
- **警告修复**：报告 Markdown 中错误的反斜杠反引号转义已清理，不再触发该项 \`SyntaxWarning\`。

### 推荐第一次验证

GitHub Actions → \`Character E1 - Real Model (manual)\` → Run workflow：

- \`provider: siliconflow\`，\`model\` 填**硅基流动当前实际的完整模型 ID**；
- \`variant: ccv2_ckv1\`、\`cases: 3\`，\`reply_timeout: 75\`，\`backup_provider: none\`，\`resume_run_id: 0\`；
- 小规模运行成功后选择 \`cases: 32\`；
- 如果新的运行也有超时或不足 32 题，下次按同样模型配置填写该次 \`Run ID\` 到 \`resume_run_id\`，即可接着测试；如果你更换了主模型或新启用备用模型，必须新开一次评测，不从旧结果续跑。

只需在 GitHub Secrets 中保存 API Key；**不要把密钥发进 ChatGPT 或 PR**。GitHub 托管的 Runner 需要能够访问该供应商的 HTTPS API。备用服务商现阶段仍限定已经支持的固定官方端点，不支持任意用户输入网址，以降低 API Key 泄露风险。

