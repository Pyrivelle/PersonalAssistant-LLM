# Character Core V2 (CCV2) — 稳定人格、行为决策与统一表达

## 本阶段目标

CCV2 的目标不是「更长的人设提示词」，而是**同一角色面对不同场合时仍作出具有辨识度的判断**，再由 Replyer 以自然语言表达。这一批不自动替换用户已经编辑过的 system prompt，不修改任何角色真实记忆或剧情设定，也不扩大主动消息发送权限。

当前运行路径：

```text
现有 System Persona (用户可编辑)
        + CCV2 stable values / situation cues
                    ↓
         Planner V2（为什么说/是否等）
                    ↓
         Replyer（怎样自然地说）
                    ↓
           QQ 或桌面/语音
```

对于主动消息：

```text
B1 真实目标或单独许可的 daily_checkin
  → B2 host timing & recipient permission
  → Initiative Planner (ask/share/defer/silence)
  → CCV2 common Replyer (新增，开启 CCV2 时)
  → 最后一次发送权限/时机校验 → QQ ACK
```

**核心区别：** CCV2 不允许模型自己发消息、扩大社交权限或改变 tool grants。「有主见」意味着有能力作出符合人物价值观的判断，并不意味着绕过用户的边界。

## 模块

- `src/zhuangfy/persona/character_core_v2.py`：CharacterBehavior 数据模型，稳定价值观和复合场景路由。一次最多选两个情境规则，规则内容由代码维护，不把用户消息直接拼进系统策略。可区分技术+压力、承担+低落、成果、角色设定、亲近与日常生活；多人群聊额外避免引用私聊关系。
- `src/zhuangfy/agent_v2/planner.py`：可选接受 CCV2 行为准则，**作用于行为决策**，而非仅在回复尾部修饰语气。
- `src/zhuangfy/agent_v2/replyer.py` + QQ / Qt Replyer：继续使用现有人格包、历史和计划指导，并追加同一份 CCV2 对应场景准则。CCV2 开启时不叠加老的 V1 单场景正则提示，防止两套策略互相冲突。
- `src/zhuangfy/agent_v2/character_delivery.py`：仅当 CCV2 开启且当前身份确为庄方宜时，已授权的主动发送草稿进一步调用 **原本用于普通答复的 `reply` 模型路由**。Replyer 不会自己决定投递；生成失败时本次不发送，避免缺失人物风格的草稿直接越过新策略。
- 该 V2 策略借鉴原有 [MengPaul07/zhuang-fangyi-skill](https://github.com/MengPaul07/zhuang-fangyi-skill) 的场景与认知边界，但规则为本项目重新撰写的简短行为原则，不复制完整剧情和原作台词。

## 开启方式

在已有本地 `SenseVoice/cognition_settings.json` 配置中，增加：

```json
{
  "character_core_v2_enabled": true
}
```

只增加上述字段，不覆盖其他已有配置。新功能**默认关闭**，旧版 `fangyi_scene_skill=true` 默认保留。设置为 `true` 后，对于识别为庄方宜的现有自定义提示词 / 内置人格包，CCV2 优先取代 V1 场景指导；未选庄方宜人格时不会强行注入。关闭则完整恢复旧场景策略。QQ Bridge 和桌面模型进程需要重启以载入新的 PlannerSettings；新配置不修改 `llm_settings.json`。

若已经修改过 `resources/personas/zhuang_fangyi_v1/prompt.md` 或自己的提示词，**不会覆盖自定义内容**。默认包与 QQ 后附加的渠道规则可识别；无明确身份声明的完全自定义提示词可能不识别，此时应在自己的 system prompt 中明确写出「你是庄方宜」，而不是开启一个会覆盖其他角色的宽松关键词匹配。

注意：CCV2 的主动消息会多一次 reply 阶段的模型调用，通常意味着额外延迟与 Token 开销。它不改变本地 `proactive_settings.json` 的开关、频率和安静时段。对于 Planner 决定 silence/defer 的情形不会进入主动 Replyer；生成失败会记录日志并跳过本次发送，不无限每分钟重复生成同一个候选。

## 固定评测基线（离线）

`tests/unit/test_agent_v2_character_core.py` 包含：

| 情境 | 想观察的稳定行为 |
|---|---|
| 独自承担风险 | 温和反对、不迎合、提出共同分担 |
| 疲惫或失败 | 先听与理解，不把安慰变成待办 |
| 项目成功 | 先分享喜悦，不抢功或泼冷水 |
| Qt / OSG 技术讨论 | 先证据与约束，再给可执行建议 |
| 武陵和人物经历 | 区分设定证据与个人演绎，不虚构过去 |
| 亲近话题 | 克制的亲近与真实关系，不制造依赖 |
| 平常吃饭、天气、散步 | 允许简短自在的生活交流 |
| 技术 + 疲惫 + 一人承担 | 可以组合主要场景，不被第一个匹配覆盖 |
| 非庄方宜人格 | 不注入角色策略 |
| 已批准的主动交流 | 经同一个 Replyer stage 写作，无法直接调用发送工具 |

这批测试证明**路由和约束**，而不能单独证明模型的输出已经达到角色还原标准。真实体验需要采集不泄露私人数据的匿名评测案例，将 V1 与 V2 在相同模型、温度与上下文下盲评：角色一致性、真实度、交流时机、表达自然度与冗余率。不要简单用角色名出现次数、温柔形容词数量或者表情符号数量评估。

## 后续迭代（不包含在本 PR）

1. 建立有版权与事实出处的 Character Knowledge 检索，按需召回剧情场景、人物关系、武陵世界知识，不把原作管理员经历写成现实共同记忆；
2. Relationship State：共同项目/明确约定的连续经历、真实时间线和兴趣，按已授权的账号/渠道查询。CCV2 目前**没有**创造新的关系数据库；
3. 将固定评价集扩展为模型端回归，验证 Planner 和 Replyer 的成品对话而不只验证提示词字符串；
4. 调试主动发送触发原因（P0）暂缓，沿用现有主动发送频率/时段规则；
5. **授权提醒 Skill**（例如「半小时后提醒我做某事」）单独研发：意图提取、确认、持久计划、一次性幂等 QQ/语音投递与可取消权限。不能把真实提醒当作 CCV2 的自由聊天、主动问候或 B1 可重复只读任务，也不在此 PR 实现。
