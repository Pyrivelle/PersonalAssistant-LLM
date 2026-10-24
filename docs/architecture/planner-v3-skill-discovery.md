# Planner Decision V3 / Skill Discovery V1

状态：**实验能力，默认关闭**。本次只升级底层决策协议与技能可见性；保留 Planner V2、QQ 社交等待和 Replyer 的现有行为。没有自动启用写入型 Skill、设备控制、提醒和技能安装，也没有增加 UI 开关。

## 目的与分层

- Planner Decision V3：第一次模型请求就要求固定、带版本号的 JSON。包括 action、意图、简单操作依据、显示渠道、工具名称、工具参数的 JSON 字符串。
- Skill Directory：skill.json 的静态 description、core_tool、keywords。只是说明：发现阶段不能导入未启用的 Python 技能。
- ToolBinding：真正的函数签名及 Schema。只有宿主加载过并显式启用的可信技能才能进入 Registry。
- SkillDiscovery V1：每轮先在**已经启用、作用域允许、只读许可**的 Registry 内做纯本地搜索。最多展示 4 个 core、3 个 deferred；没有关键词匹配的低频工具不会暴露完整 Schema。
- Planner 判断：从本轮白名单里自主选择是否调用工具。选择理由用于简短审计，不能代替宿主的授权事实。
- AgentCycleEngine 复用 V2：执行前二次检查已注册工具、私聊/群聊、写入权限、参数 Schema 和超时；失败只向 Replyer 提供失败回执，不假装成功。

不应将 core_tool 当作执行许可证。QQ 现有执行器仍强制 allow_mutation=False，即使安装了修改型 Skill，也不会被本轮工具发现或执行。

## 配置

在 SenseVoice/cognition_settings.json 中显式使用：

    "agent_v2_enabled": true,
    "agent_v2_tools_enabled": true,
    "agent_v3_enabled": true

agent_v3_enabled 默认 false，若没有启用 agent_v2_enabled 却开启 V3，会直接拒绝配置。

在技能 skill.json 可新增可选字段：

    "core_tool": false,
    "keywords": ["字数", "字符统计", "文字长度"]

不写关键词的 deferred 技能不会因为纯聊天中的泛词而意外出现。多语言关键词和同义词需明确声明；之后可替换为 BM25/工具发现服务。

## Planner 与原生结构化输出

第一个模型请求仍使用原有 ModelRouter 的路由、限时、重试和模型故障切换。V3 额外传入固定 DECISION_SCHEMA：

- 默认 auto：SiliconFlow 从第一次请求发送 response_format=json_object；其他未认证能力的供应商使用 JSON Prompt + 本地严格验证。
- 可在 model_routes.json 的 planner.candidates 项为已验证支持的模型显式设置 planner_format=json_object。
- 若供应商确实支持严格 JSON Schema，可显式设置 planner_format=json_schema；客户端会发送 JSON Schema 对象，供应商若拒绝协议则按原路由处理模型失败，不会静默声称支持。
- planner_format=prompt 可强制使用最保守的提示词模式。
- 模型返回仍由本地 V3 校验器检查版本、必需字段、字段类型、枚举、合法工具名称、白名单与参数 JSON。任何不符合格式的结构都回退到既有补救/候选模型路由，全部失败则退回正常对话而非执行工具。
- tool_arguments_json 是对象形式的 JSON **字符串**，以使外层严格响应 Schema 在不同技能之间保持稳定。真正的参数对象只能由 Executor 按该技能的 JSON Schema 校验后执行。

## 性能与成本

普通私聊可只有 core 工具。先做本地关键词匹配，额外 token 开销主要来自最多几个候选 schema；未匹配低频工具不发给 LLM。这里尚无模型驱动 tool_search 的二次发现和语义检索，不保证识别隐晦的需求。未明确满足关键词的 Skill 不会自动可见，稳定后可接 BM25。

启用/停用仍使用 skills_settings.json 显式策略，需重启 QQ Bridge；WebUI 技能开关、热重载、完整账号级 ACL、动态 tool_search 和版本兼容治理是后续方向。**第三方 Python skill.py 在启用时拥有宿主进程权限，不是沙盒。**

## 测试

- V3 JSON 必须完整、类型正确、不能包含额外字段或未展示的 Tool。
- 40 个低频工具安装后仅匹配文本统计关键词时才展示目标工具；完全无关的生活聊天没有多余 Schema。
- 显式启用及渠道作用域、写入权限、参数嵌套数据校验仍独立生效。
- SiliconFlow 第一次 JSON Mode + 显式严格 JSON Schema 的路由参数正确。
- V2 默认路径仍受现有 CI 测试约束。

后续再做：真正的自动 tool_search/按需二次发现、复杂检索和 Skill 参数完整 Draft 2020-12 JSON Schema 支持、独立能力开关 UI 与审计。
