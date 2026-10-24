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
2. 对从可信 OneBot 接入的真实 QQ 私聊，Bridge 可用客户端令牌自动建 Person 并关联 QQ 身份；离线或其它渠道仍需人工核验账号归属后，使用 `admin_link(person_id, MemoryChannel("qq", bot_id, qq_id), "审核凭据编号")` 关联账号。
3. 当 `ZHUANGFY_MEMORY_QQ=1` 且 Memory Service 正常运行时，QQ 私聊文本默认本地保存 Timeline，无需逐人审批。首次私聊建档，同时补齐以前已关联但尚未配置保存策略的账号。曾撤销 Timeline 或执行过 `/forget_me` 的人物不会被静默重新授权。管理端可以单独关闭本地保存。`admin_consent(person_id, "curation", True)` **始终必须管理员逐人批准**，才允许后台模型提炼长期记忆；修改已有授权后需要重启 QQ Bridge 刷新本地缓存。

桌面语音需另行关联 `MemoryChannel("desktop", "local", ZHUANGFY_MEMORY_DESKTOP_USER_ID)` 到同一个 Person UUID。**昵称、用户名和自称都不能作为账户合并证明**；API 只能把通过可信客户端验证的 identity 绑定到 Person。

管理令牌仅供本机可信管理工具使用；绝不放入 QQ 进程、Web 前端或普通回复提示词。客户端令牌代表一个受信渠道进程，但**不等于最终用户登录认证**，不能暴露给未授权程序或公网。

## 运行时可选接入

QQ Bridge 环境配置：

```powershell
$env:ZHUANGFY_MEMORY_TOKEN = "与服务端一致的客户端令牌"
$env:ZHUANGFY_MEMORY_QQ = "1"
```

QQ **通过准入的私聊**首次发送消息时才会自动创建或复用 Person（不是加入白名单时）。昵称仅作首次展示名，唯一渠道身份为 `(qq, bot_id, qq_user_id)`；同名账号不合并。默认只建档，不自动保存 Timeline。如运行 Memory Service 的管理员设置 `ZHUANGFY_MEMORY_QQ_AUTO_TIMELINE=1`，**仅首次创建的新 QQ 人物**可同步开启本地私聊文本 Timeline 留存；既有账号不补授权限，曾撤销或执行 `/forget_me` 的人也绝不自动重授。原始私聊单条消息在准入后、消息防抖和回复限额前单独记录（图片二进制不包含在文本 Timeline 中），已确认发送的机器人回复同样按原回执记入。该设置需要服务运营者事先明确告知聊天者本地保存政策、提供删除入口，并保护本地数据库和备份。**Curator 长期记忆提炼仍须逐人管理员显式授权 `curation`，默认不启用**，即使有 Timeline 也不会自动消耗云端提炼 Token。QQ 自然交流默认使用保守 L0 表达参考，更高关系档位仍须经过原 R1 注册与授权。跨微信/桌面身份统一留待专门的验证流程。单个有稳定 OneBot message ID 的真实消息写入 Timeline，合并的多段 QQ 输入不冒充某一条真实消息。只有 QQ 全部回复气泡成功确认发送后，助手回复才标记 delivered。群聊与尚未验明的人不会自动建档。设置 `ZHUANGFY_MEMORY_QQ=1` 后，QQ 桥接不再读取、写入或反思旧 `qq_members.sqlite3`；旧数据保留但尚未迁移。新服务临时离线时也不会回退到旧库。

桌面语音：

```powershell
$env:ZHUANGFY_MEMORY_TOKEN = "同一客户端令牌"
$env:ZHUANGFY_MEMORY_DESKTOP = "1"
$env:ZHUANGFY_MEMORY_DESKTOP_USER_ID = "已在服务端验证的本机用户标识"
```

`ChatWorker` 会在回复前尝试记录语音文字并读取人物相关记忆；主界面**确实接受、显示了回复后**，才通过后台线程记录 delivered。插话被丢弃、失效的回复不算共同经历。设置 `ZHUANGFY_MEMORY_DESKTOP=1` 后，桌面文字和语音不再读取或写入旧 Mem0，语音也不再读取或写入旧 QQ 主人档案。服务不可用时对话仍可继续，但该轮新记忆可能无法保存，旧库不会作为隐式备用。

这只是**运行路径接管**。旧 QQ 档案和 Mem0 中已有的历史尚未迁入新 Memory Core；旧数据留在本地，避免未验证来源和冲突事实直接进入新库。桌面“记住……”在新模式下先记录到 Timeline，达到自动整理条件后才可能形成长期记忆，不能保证立即入库；“忘记……”需要在记忆中心选择具体记忆操作，不会再调用旧 Mem0 的删除命令。旧 QQ `/forget_me` 仍会尝试清除旧档案和新库数据。

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

新记忆服务按**单段私聊会话**累计尚未整理的真实消息（用户 `received` + 助手 `delivered`）。累计 **36 条消息**时排入反思任务；如果下一条会使批次超过 **8,000 Token 的保守估算上限**，则提前封批。估算按实际传给整理器的每条消息（最多前 1,600 字）序列化后的 UTF-8 字节数计算，不依赖供应商特定分词器；它会偏保守，提示词及现有记忆不计入这个对话材料批次上限。少于阈值的会话在最早一条待整理消息满 **24 小时**后兜底排队，即使这期间没有新对话也会触发。服务停机期间不会运行，重新启动后会补查到期会话。

后台 Worker 约每八秒检查待整理会话，并处理**一个**反思任务及同步向量索引；实际完成时间仍取决于队列与模型延迟。管理员仍可显式手动排队或通过 `/v1/admin/reflect-one` 执行已有任务。LLM 调用始终在数据库事务外。当前 HTTP API 未提供完整的任务进度 UI 或多副本高可用部署。非本机访问、群聊共享记忆、自动身份归并都不属于本阶段。

## 测试

```bash
PYTHONPATH=src python -m unittest -v tests.integration.test_memory_http
```

真实本机 HTTP 测试验证客户端/管理员令牌分权、身份校验、重复消息幂等、不同人物隔离、撤销同意、私聊全部擦除以及服务配置只能监听回环地址。
