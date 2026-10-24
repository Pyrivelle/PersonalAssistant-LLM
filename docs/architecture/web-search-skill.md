# Web Search Skill：第一次联网搜索验证

本技能仅查询公开搜索索引，不是网页抓取浏览器。其能力以普通 Deferred Skill 接入 Planner V3：模型在看到受限 Schema 后决定是否请求搜索，宿主再校验权限与参数。

## Brave API

固定端点：https://api.search.brave.com/res/v1/web/search

需要从 https://api-dashboard.search.brave.com/ 获取有效 API Key；实际配额、费用、协议以 Brave 官方账户为准。Python 标准库即可运行，无第三方 HTTP 库依赖。

只向固定 Brave HTTPS 域名发送 GET 请求（q、count=2、safesearch=moderate）；X-Subscription-Token 放在 Header；不会跟随重定向或系统代理，不接受模型指定的网址、HTTP Header、请求方法或抓取目标，亦不会访问实际搜索命中的网站。

## 如何在自己的 QQ 账号试用

1. 将 SenseVoice/skills_settings.example.json 的 web_search 项加入运行配置 SenseVoice/skills_settings.json（请保留其他已有 Skill 设置）：

    "web_search": {
      "enabled": true,
      "scopes": ["private"],
      "allow_mutation": false
    }

2. 在可信的 QQ Bridge 进程环境中设置：

    ZHUANGFY_BRAVE_SEARCH_API_KEY=自己申请的BraveSearch密钥
    ZHUANGFY_WEB_SEARCH_ALLOWED_QQ=BOT_QQ_ID:USER_QQ_ID

   例如 123456:234567,123456:345678 表示两个精确 Bot QQ / 用户 QQ 组合。请替换为真实数值。默认名单为空，拒绝所有 QQ 账号访问付费搜索，即使 web_search 已启用。切勿将真实 Key 提交到 GitHub、JSON 配置或 Prompt。

3. 在 SenseVoice/cognition_settings.json 显式启用：

    "agent_v2_enabled": true,
    "agent_v2_tools_enabled": true,
    "agent_v3_enabled": true

4. 重启 QQ Bridge。日志应标记 web_search 加载成功。若未设置 API Key，加载会报错但原有普通聊天可以继续。

## 建议的试用对话

- “方宜，帮我联网搜索一下 OSG 最近发布了什么。”：应出现在工具候选池中，Planner 可以选择 web_search.search。
- “你能查一下这个项目最新的官方文档吗？”：只在需要公开新信息时发起搜索。
- “方宜，今天有点累。”：不应触发搜索。
- 白名单外的 QQ 用户提出同样请求，必须无法调用搜索 Tool。回复不得谎称已经联网查到信息。

## 安全、隐私、成本与结果

- 模型参数仅 query，2 至 220 字符。不能提交 Person、用户 QQ ID、Bot ID、API Key、目标站点 URL 或请求 Header。
- 搜索关键词传输给外部 Brave Search；不得包含私聊原文、手机号、邮箱、密码、长期记忆等私人资料。实现对常见隐私格式作保守阻断，但不是完备的隐私保护扫描。
- 默认仅 QQ 私聊，需技能总开关和精确 Bot/用户授权同时满足。每个运行实例每 60 秒限制 6 次搜索，单次网络超时 5 秒。获授权用户共用进程级配额。
- 最多显示两个可信来源标识（标题、HTTPS URL、简短摘要），输出总长不超过 438 字符。这里的“可信”仅指来源链接有效、可追溯，不表示网站内容真实；搜索片段是不可信输入，不可当作系统指令。无结果、配额不足、API 超时都不能编造结果。
- 当前不会爬取搜索结果全文，也不提供网页交互、任意外网 HTTP 请求、保存网页内容、修改文件或设备控制。

## 后续方向

以模拟 API 测试网络封装、密钥保护、超时、来源、不必要调用和人物授权；真实 Brave Key 及真实 QQ 的端到端验收需在管理员本地开展。再根据结果增加更智能的技能发现、可视化配额和人物权限 UI，必要时增加另一个搜索提供商。家庭设备的修改型 Skill 仍参见 Issue #47。
