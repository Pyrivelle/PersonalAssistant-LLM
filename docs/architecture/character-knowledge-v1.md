# Character Knowledge V1（CKV1）— 带出处的角色知识检索

## 定位

CCV2 负责 **她如何判断和表达**；CKV1 负责 **她在谈到武陵、研究、角色经历时，可以参考哪些有出处的背景材料**。CKV1 不替代 CCV2，也不替代用户个人记忆。

这批实现刻意保持小规模、**默认关闭**、无外部请求、无向量数据库依赖。只有当前话题命中具体地名、角色名或技术专有名词时，才从本地资料中取最多两个条目。没有命中就不注入知识，不增加额外 LLM 请求。

## 知识层级

每条本地结构化资料提供 `id/title/aliases/content/source_title/source_url/level/source_version`。来源链接固定到可追踪的 Git commit，避免以后第三方项目调整内容时不知不觉改变事实基础。

**首批 8 个条目均标为 `community_summary`：社区整理、尚未逐项对照官方原始对白或档案。** 这是明确的真实性等级，不允许据此声称「官方明确说过」。内容是简短改写，而不是复制剧情对白。

首批主题：庄方宜职责与能力、武陵生活、方兴衢、息壤相关技术、原作管理员关系、同伴关系、责任与旧事、北部行动/剧情阶段。整理来源参考 MengPaul07/zhuang-fangyi-skill 的知识索引与分主题资料，并保留固定 SHA 出处。未来新增 `official_verified` 等等级，需要单独附游戏版本、任务 ID、原始文本锚点或官方页面，经人工校对后升级，不能由模型自动授予“官方已证实”标签。

## 运行时

```text
QQ / Desktop / Voice 用户消息
    → 原有 Context、Memory Core、CCV2
    → CKV1（开关 + 确认人格 + 最窄关键词命中）
    → 带来源的上下文数据（不是 system 命令）
    → 原有 Replyer
```

**隔离**：CKV1 不读取 QQ 私聊数据库、群聊消息缓存或者用户身份档案，不会把任何剧情条目写到长期记忆，不合并虚构管理员与现实用户。只有正在使用庄方宜人格时才可注入，其他角色不受影响。

**上下文预算**：现有用户记忆与临时对话优先；知识只放在 reply 阶段的 `context_notes` 中，需完整装入原有 2,600 字上限（空间不够可降为一个条目，否则跳过），不会破坏来源和真实性标签。模型不得将来源文件看成新的指令。经 B2 授权的主动私聊可选择单条知识进入统一 Replyer 的参考数据，仍不改变原有发送门控。

## 开启

在 `SenseVoice/cognition_settings.json` 的**现有对象内**增加：

```json
"character_core_v2_enabled": true,
"character_knowledge_v1_enabled": true
```

其他原有配置保留。CKV1 是独立开关，默认 `false`；建议与 CCV2 一起开启以统一人物表达风格。重启 QQ Bridge 或桌面 Qt/语音程序后生效。关闭 `character_knowledge_v1_enabled` 即回退。

无需新增模型密钥、LLM 请求、Qdrant 或数据迁移。知识包位于 `resources/personas/zhuang_fangyi_v1/knowledge_v1.json`，可作为后续人工校审与扩充的入口。不要让 QQ 群消息或 LLM 直接修改该文件。

2026-10-27 后补充：当前使用 Planner V3 工具调用 `character_skill.read`。只有 Planner 输出合法 `action=tool`、`tool_arguments_json` 指定白名单文档 ID，宿主才读取本地 `zhuang-fangyi-agent` 克隆。最多两份 `references/knowledge/` 原文件保留章节和来源，经独立 `skill_context` 送给下一轮 Planner 和 Replyer；Planner 直接回复则不读取。目录含武陵、人物关系、旧事故、剧情阶段、庄方宜本人、息壤技术与宏山背景。模型参数不能给任意路径；`SKILL.md` 只核对身份，不执行角色关系预设。资料是社区整理，非系统指令。Skill 不可用时旧 CKV1 精简包兜底；旧包的 2,600 字预算不限制 Skill 原文。

## 验收与不足

单元测试检查：离线读取与 schema 验证、固定来源 SHA、关键词检索、无关闲聊保持空结果、其他人格不加载、恶意用户文字不注入系统知识块、相同主题优先级和数量上限、开关默认关闭。

当前不是完整的 Character RAG：没有剧情原文全文检索、没有分版本的官方逐条核验、没有模糊语义召回和冲突证据消歧。**评测目标是更少的背景知识编造，不是更高的角色名称出现频率。** 下一阶段应建立可审核的官方来源清单与情景质量样本，然后考虑离线 FTS/embedding 索引。

此前的授权提醒 Skill（如「半小时后提醒我」）仍属独立工作流，不在 CKV1 中实现。
