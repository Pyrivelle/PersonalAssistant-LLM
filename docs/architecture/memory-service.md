# M4 — 单进程 Memory Service 与跨渠道接入（默认关闭）

M4 在 M1 身份、Timeline、M2 Curator 与 M3 Hybrid RAG 的基础上提供本机 HTTP 服务。服务进程独占 Qdrant Local 路径；**不要让 QQ Bridge 和 Qt 分别打开同一 Qdrant 目录**。

## 启动（手动，独立终端）

```powershell
python -m pip install -e ".[memory-vector]"
$env:ZHUANGFY_MEMORY_TOKEN = "请自行生成不低于32字符的随机客户端令牌"
$env:ZHUANGFY_MEMORY_ADMIN_TOKEN = "请使用另一份随机且不低于32字符的管理员令牌"
python -m zhuangfy.memory.http_server
```

监听固定为 `127.0.0.1:8769`，可通过 `ZHUANGFY_MEMORY_PORT` 修改端口。数据目录默认 `data/memory_core/`，可用 `ZHUANGFY_MEMORY_DIR` 指定。向量首次用到 `FastEmbedProvider` 时可能下载中文 BGE 权重。令牌通过环境变量提供，绝不要提交到 GitHub 或保存到公开脚本。

## 显式授权与人物账户关联

管理员客户端需要使用 `ZHUANGFY_MEMORY_ADMIN_TOKEN`。应用内管理界面、可信本地配置流程可以完成：

1. `admin_create_person(display_name)` 创建内部 Person UUID。
2. 人工核验账号归属后，`admin_link(person_id, MemoryChannel("qq", bot_id, qq_id), "审核凭据编号")` 关联 QQ 账号。
3. 分别 `admin_consent(person_id, "timeline", True)`、`admin_consent(person_id, "curation", True)` 启用长期保存和整理。

桌面语音需另行关联 `MemoryChannel("desktop", "local", ZHUANGFY_MEMORY_DESKTOP_USER_ID)` 到同一个 Person UUID。**昵称、用户名和自称都不能作为账户合并证明**；API 只能把通过可信客户端验证的 identity 绑定到 Person。

管理令牌仅供本机可信管理工具使用；绝不放入 QQ 进程、Web 前端或普通回复提示词。客户端令牌代表一个受信渠道进程，但**不等于最终用户登录认证**，不能暴露给未授权程序或公网。

## 运行时可选接入

QQ Bridge 环境配置：

```powershell
$env:ZHUANGFY_MEMORY_TOKEN = "与服务端一致的客户端令牌"
$env:ZHUANGFY_MEMORY_QQ = "1"
```

仅 QQ 白名单**私聊**、已验证账号、经授权人物可写入。单个有稳定 OneBot message ID 的真实消息写入 Timeline，合并的多段 QQ 输入不冒充某一条真实消息。只有 QQ 全部回复气泡成功确认发送后，助手回复才标记 delivered。群聊与尚未验明的人不会自动建档。暂时保留旧 QQ 记忆作兼容，尚未迁移。

桌面语音：

```powershell
$env:ZHUANGFY_MEMORY_TOKEN = "同一客户端令牌"
$env:ZHUANGFY_MEMORY_DESKTOP = "1"
$env:ZHUANGFY_MEMORY_DESKTOP_USER_ID = "已在服务端验证的本机用户标识"
```

`ChatWorker` 会在回复前尝试记录语音文字并读取人物相关记忆；主界面**确实接受、显示了回复后**，才通过后台线程记录 delivered。插话被丢弃、失效的回复不算共同经历。所有新逻辑按 opt-in 开关关闭，服务不可用时原有回复流程继续运行（不会阻塞聊天）。

用户在 QQ 私聊发送 `/forget_me`，在原有 QQ 记忆之外，还会删除**新 Memory Core 内当前已验证 Person 的全部私聊历史与提炼记忆**，包括其已关联的桌面等渠道，而不仅是当前 QQ 账号；同时原子撤销该人物的 `timeline` 和 `curation` 授权，防止下一条消息又自动重建记忆。重新收集必须经新的明确授权。若向量清理失败会报告待修复状态，**旧 Mem0 数据库、其它遗留 QQ 存储、备份及外部数据副本不在本操作的彻底擦除范围内**。

## Curator 真实模型（单独授权）

默认 M4 服务不调用云端提炼模型，也没有读取既有 QQ/语音的老数据。启用后台整理需要：

```powershell
$env:ZHUANGFY_MEMORY_REFLECT = "1"
$env:ZHUANGFY_MEMORY_LLM_BASE_URL = "https://your-provider.example/v1"
$env:ZHUANGFY_MEMORY_LLM_MODEL = "your-json-capable-model"
$env:ZHUANGFY_MEMORY_LLM_API_KEY = "独立密钥"
$env:ZHUANGFY_MEMORY_ALLOW_REMOTE = "1"
```

其中 `ZHUANGFY_MEMORY_ALLOW_REMOTE` 是确认将经授权的私聊内容传给**指定供应商**进行记忆提炼，不会随着回复 API 的候选故障转移到其他平台。访问令牌不应作为模型输入。仅在本机模型地址 `127.0.0.1` 可不设置远程许可标志。管理员也可通过 `/v1/admin/reflect-one` 手动触发一个队列任务。

后台 Worker 会按约八秒间隔执行一轮反思并同步向量索引，LLM 调用始终在数据库事务外。当前 HTTP API 未提供完整的任务进度 UI 或多副本高可用部署。非本机访问、群聊共享记忆、自动身份归并都不属于本阶段。

## 测试

```bash
PYTHONPATH=src python -m unittest -v tests.integration.test_memory_http
```

真实本机 HTTP 测试验证客户端/管理员令牌分权、身份校验、重复消息幂等、不同人物隔离、撤销同意、私聊全部擦除以及服务配置只能监听回环地址。
