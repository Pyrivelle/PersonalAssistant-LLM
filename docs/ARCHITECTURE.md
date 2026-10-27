# ZhuangFY 当前架构

状态基准：2026-10-27。项目仓库为 `https://github.com/Pyrivelle/SpringsReturn.git`。本文区分 `main` 与尚未合并的 PR #54；代码是最终依据，运行状态以每次本机检查为准。[当前交接](HANDOFF.md) 记录具体工作树与测试。`docs/architecture/` 中的专题文档可作设计背景，其中有些早期“尚未接入”描述已经过时。

## 代码与进程边界

| 位置 | 当前职责 | 主要入口或类 |
| --- | --- | --- |
| `src/zhuangfy/core/` | 基础情绪状态；PR #54 另加入跨入口近期上下文 | `emotion.py`；PR #54 的 `recent_context.py` |
| `src/zhuangfy/llm/` | 供应商、模型目录、候选路由和连接测试 | `providers.py`、`routes.py`、`model_registry.py` |
| `src/zhuangfy/persona/`、`resources/personas/` | 角色包、稳定行为规则、关系表达建议 | `packs.py`、`character_core_v2.py` |
| `src/zhuangfy/agent_v2/` | 自有 Planner/Replyer、工具与自主任务策略 | `planner_v3.py`、`replyer.py` |
| `src/zhuangfy/memory/`、`relationship/` | 人物、身份、Timeline、检索、整理、关系证据与授权 | `MemoryCore`、`MemoryClient`、`RelationshipCore` |
| `src/zhuangfy/speech/` | SenseVoice STT、VAD/唤醒、交互状态、GPT-SoVITS | `sensevoice.py`、`vad.py`、`interaction.py`、`gpt_sovits.py` |
| `src/zhuangfy/observability/` | 活动事件与聊天流持久化投影 | `activity_log.py`、`chat_journal.py` |
| `SenseVoice/` | 尚在运行的 Qt 控制台、语音窗、文字对话、QQ 桥接与兼容入口 | `dashboard.py`、`gui.py`、`chat_worker.py`、`qq_bridge.py`、`chat_stream.py` |
| `src/zhuangfy/engine_fusion/`、`SenseVoice/hybrid_qq_bridge.py` | **仅 PR #54**：经核验 QQ 宿主、共享上下文、IPC 和原生 Maisaka Worker | `SharedContextProvider`、`Envelope`、`NativeMaisakaAdapter`、`HybridQQBridge` |

`pyproject.toml` 要求 Python ≥3.12，`zhuangfy` 命令进入 `src/zhuangfy/bootstrap.py`，其当前仍导入 `SenseVoice/dashboard.py`。Windows 常用入口 `SenseVoice/启动界面.cmd` 会按本地记忆运行配置选择 `scripts/windows/start-memory-enabled.ps1` 或直接启动 Dashboard。`Paths.current()` 统一确定项目、`resources/`、旧 `SenseVoice/`、外部引擎与数据目录。默认新数据在 `data/`，旧运行数据仍可位于 `SenseVoice/`；设置 `storage_location.json` 后分别用指定位置的 `data/` 与 `legacy/`。文件位置改变不能理解为数据库已自动迁移。

SnowLuma 提供 OneBot WebSocket，ZhuangFY 为客户端；GPT-SoVITS 提供独立本地语音合成。它们位于仓库外的相邻目录或由 `ZHUANGFY_*_DIR` 指定。API 供应商是外部服务，密钥在忽略的 `.env.local` 等本地配置。PR #54 的 MaiBot 位于 `third_party/MaiBot` 子模块，固定提交 `062d45cf8bd4272ac57d7ec03fcf405e5a458211`，有独立环境和忽略的 `.maisaka-hybrid-preview/` 配置/数据库。不要把上游历史库或模型输出作为 ZhuangFY 可信身份资料。

## 对话与语音流程

桌面文字/语音由 Qt 层采集；语音路径是麦克风 PCM → VAD/唤醒与分句 → SenseVoice STT → `ChatWorker`/LLM → GPT-SoVITS TTS → 播放。语音交互有 Listening / Thinking / Speaking 状态、播放打断和扬声器回声的保守处理；仍需按真实设备复核长时稳定性。文字和语音都应借同一身份、人格、近期上下文与记忆服务，不应再生一套“桌面专属人物认识”。聊天流由事件日志投影，保留跨重启的近期记录；运行控制台更偏向当次进程。

原 QQ 引擎的 `SenseVoice/qq_bridge.py` 负责 OneBot 验证、私聊/群聊准入、消息合并与速率策略、图片/语音输入、Planner/Replyer、QQ 投递。主动问候、群聊和搜索按本地开关、白名单、工具授权分别控制；无凭据的联网搜索不能假定已启用。回复成功发送与长期记忆提交是两个阶段，不能把“已生成文字”当作送达。

PR #54 的 Hybrid 则用 `scripts/chat_engine.py` 显式选引擎，不能设为默认，必须确认独占 QQ 通道且指定一个已授权私聊测试用户。`HybridQQBridge` 接收已认证消息 → `SharedContextProvider.prepare()` 读取 PersonID、`timeline` 同意、关系快照、受限长期记忆和绑定主人的跨入口近期记录 → v2 `ipc_contract.Envelope` → 独立 Worker 中上游原生 Maisaka Planner/Replyer/`reply`/`wait`/`stop` 与 ImageManager。宿主仍是唯一 QQ 发送者；OneBot ACK 后调用 `MemoryClient.complete_reply()` 并做关系证据处理。图片原始字节只走受控附件通道，不在普通 IPC JSON 中传播；VLM 描述作为有来源的观察记录，不自动变成用户意图。Wait 唤醒后需延续同一可信身份上下文。

Hybrid 的 `HybridSocialContext` 区分 `person_id`、档案名、确认的偏好称呼、关系状态/层级/版本、表达建议与单独确认的关系定义。确认资料按 PersonID 存在本地忽略文件，关系层级本身不能推导“朋友/恋人”。Planner 和 Replyer 均应得到相同可信快照；服务不可用须标为 unavailable，而非降格成陌生人。IPC v2 严格校验字段、大小与 QQ 私聊 scope，升级双方时要同步改契约和测试。

## 记忆与可观测性

`MemoryCore` 协调 `IdentityRepository`（渠道身份↔PersonID、同意）、`TimelineRepository`（received/generated/delivered 状态）、SQLite `MemoryDatabase`、Curator、向量/全文混合检索以及 `RelationshipCore`。HTTP 服务由 `http_server.py` 在本机回环地址提供，`MemoryClient` 按客户端/管理员令牌分权；当前默认端口 8769。QQ 自动建档只应发生在已验证且获准的私聊入口，不能凭昵称跨渠道合并。`timeline` 同意允许保存时间线，`curation` 单独决定能否云端整理；阈值为 36 条未整理消息、保守估算 8,000 Token 批次上限及最早消息满 24 小时兜底。删除需清理人物资料、时间线、关系和检索索引；旧 Mem0/QQ 数据未自动迁入新库。专题说明见 [记忆服务](architecture/memory-service.md)、[R1 关系](architecture/relationship-core-r1b.md)。

`activity_log` 提供结构化阶段事件，`chat_journal`/`chat_stream` 投影为按角色分组的聊天流。PR #54 的 `native_monitor.py` 从上游监控事件与受限本地 Planner 工件读取轮次、历史、上下文尺寸、模型、决策、确实存在的推理和规划正文；拒绝把未知推理编造成“分析”。监控事件、输入历史与真实 QQ 送达是不同证据。聊天流可显示私人内容，公开日志/文档不要复制原文或凭据。

## 设计决策与限制

- 自有引擎和上游原生引擎以进程分离；Hybrid 只在独占 QQ 通道和单人预览下测试，不静默回退、不平行连接同一账号。这个边界保留 ZhuangFY 的人格、授权与记忆所有权。
- 人格包借鉴公开庄方宜 Skill 的行为逻辑，不以冗长自我声明或原作共同经历制造亲近；用户希望自然、可靠、能提出不同意见。已核验主人可有明确确认的亲密关系设定，其他用户不得继承。
- 模型推理是否展示取决于供应商/上游确实返回的内容；Planner 的自然语言正文、模型原始 reasoning、`reply/wait/stop` 调用和最终发送结果需分别显示。
- `README.md`、`docs/architecture/README.md` 与 `docs/maisaka-native-transplant.md` 有早期状态描述；它们是历史背景，不覆盖本文和代码。运行配置、账号授权与进程状态每次启动前都要重新查。
