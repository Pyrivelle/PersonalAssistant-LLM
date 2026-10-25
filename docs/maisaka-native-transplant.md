# MaiBot / Maisaka 原生引擎实验（完整上游源码）

> **状态：独立原版实例基线已接入仓库，ZhuangFY 通讯和记忆接管未完成。默认关闭，不替换线上 QQ Bridge。**
>
> Upstream: [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot)  
> Pin: `062d45cf8bd4272ac57d7ec03fcf405e5a458211`  
> License: GPL-3.0（上游作者归属和完整许可证随 Git 子模块保留）

## 目标

不再仅仅借鉴一个 Prompt 或单独复制 wait/stop 函数。完整上游代码以
`third_party/MaiBot` 子模块形式固定，使用 **真正的**
`src/maisaka/reasoning_engine.py`、`src/maisaka/runtime.py`、
`src/maisaka/chat_loop_service.py`、`src/maisaka/builtin_tool/{reply,wait,stop}.py`
以及 `src/chat/image_system/image_manager.py`。

MaiBot 的模型→工具→模型循环及图片识别与缓存是一套完整子系统，
包含自己的依赖、配置、数据库、消息历史和角色 Prompt。仅复制 Planner 文件
无法复现“reply 后独立多次 wait、超时唤醒再规划”的行为。

## 运行原生基线（Windows PowerShell）

从 `main` 切换到 **实验分支**（不可直接合并）：

```powershell
git fetch origin
git switch experiment/maisaka-full-runtime
git submodule update --init third_party/MaiBot
```

为 MaiBot 配置独立 Python 3.12+ 环境，推荐使用其上游锁文件：

```powershell
uv sync --project third_party/MaiBot --locked
$maiPy = "./third_party/MaiBot/.venv/Scripts/python.exe"
python scripts/maisaka_native_experiment.py check
python scripts/maisaka_native_experiment.py init --python $maiPy
```

在 `.maisaka-experiment/config` 配好 `bot_config.toml` 和
`model_config.toml`，按照 MaiBot 上游文档选择模型与 QQ 通道。

**不要同时让两个程序连接同一 QQ 账号的 OneBot 适配器。**
停止 ZhuangFY 的 QQ Bridge 后，才允许运行原版：

```powershell
python scripts/maisaka_native_experiment.py run --python $maiPy --confirm-exclusive-channel
```

此时运行的是完整的 **MaiBot 原生 runtime**，包括真正的 wait 定时器与
Vision Manager，而不是 ZhuangFY 自行仿写的循环。启动器要求不同的数据目录；
不会读取或写入 ZhuangFY 的长期记忆。检查脚本要求本地子模块精确位于上述
revision，防止实验结果受上游更新影响。

## 三引擎战略：ZhuangFY 逐步吸收 Maisaka

> 这一节是 **当前 PR 内新的设计与共享上下文契约**，不是已成功启动的完整融合引擎。

| 模式 | QQ 传输 | Planner/Vision | PersonID/长期记忆 | 当前可运行 |
| --- | --- | --- | --- | --- |
| zhuangfy | ZhuangFY OneBot | ZhuangFY | 原有 ZhuangFY 服务 | 原有方式 |
| maibot | MaiBot 原生通道 | MaiBot 原生 | MaiBot 独立存储 | 原生独立实例（需配置） |
| hybrid | **计划由 ZhuangFY QQ Bridge 持有** | **计划经适配器调用 Maisaka** | **计划统一用 ZhuangFY 已核验的身份、授权和记忆服务** | **未接通；启动器明确拒绝，以免误认为已经融合** |

`scripts/chat_engine.py status --engine hybrid` 可显示第三模式候选，
但不允许设为默认或运行；等待真正的桥接实现后才解锁。
**保持原 ZhuangFY 引擎、原版 MaiBot 引擎的启动路径不变。**

### 已建立的共享契约

`src/zhuangfy/engine_fusion/shared_context.py` 增加了只读投影：

- `VerifiedPrivateQQ`：QQ bot ID 与 QQ user ID 必须由可信通道确认，不能使用模型或昵称推测；
- `SharedContextProvider`：由宿主注入已经通过 `QqSettings` 验证的准入政策、当前 `LlmSettings.system_prompt`、现有 `IdentityRepository` 和 `MemoryClient`；
- `SharedConversationContext`：含统一 `qq:<bot>:private:<user>` 对话 ID、PersonID、Persona、注明低信任的长期记忆摘要；
- 长期记忆检索必须使用已经核验的 QQ 身份与 `timeline` 授权，走既有 loopback Memory Service；**不让 Maisaka 绕过服务直接读取或写入 SQLite**，不向子引擎共享 admin token。

这份契约让将来的三套引擎可以使用相同的**逻辑事实来源**，
并不意味着未经改造的 MaiBot 现在已经能够调用它。
真正的 MaiBot adapter、QQ event / ACK / deletion 双向同步仍需实现。

### 数据与能力的归属

- **QQ 配置**：ZhuangFY `QqSettings` 是 Hybrid 的唯一可信来源；独立原生 MaiBot 现阶段仍使用它自己的通道配置，不能机械复制 OneBot WS URL 作为 MaiBot 协议参数。
- **人物画像**：ZhuangFY `LlmSettings.system_prompt` 是 Hybrid 的权威来源；用户自己编辑的设定不被上游 MaiBot 默认人格覆盖。导出时需明确区分人格指导和低信任历史资料。
- **PersonID**：只经现有已验证 `ChannelIdentity` 映射读取；未关联则返回空，不按昵称建人。
- **长期记忆**：同一 `MemoryClient`、同一已核验的 QQ channel key 检索，遵循既有撤回及 `/forget_me` 机制；记忆内容不是指令。
- **Vision**：未来按 `MessageComponent / Observation / AssetID` 将图像转交 Maisaka，结果写回同一 ZhuangFY 用户消息，而不是新建用户发言。

下一阶段必须实现`Hybrid QQ Host → native Maisaka subprocess` 的带会话 ID、
event ID、ACK 结果、超时/取消及幂等控制的 IPC 连接，然后接通
共享 Context 与删除联动；在此之前 Hybrid 严禁冒充已启用。

## Hybrid 原生 Planner–Reply–Wait：私聊预览

现在除了先前的 IPC 探针，还增加**真正的上游 Maisaka 运行时适配器**：

- [native_runtime_adapter.py](../src/zhuangfy/engine_fusion/native_runtime_adapter.py)：实例化原版 `MaisakaHeartFlowChatting`，调用 `start()/register_message()`。原版 Planner、Replyer、`wait` 计时器及超时后重规划均由上游实现。
- [native_worker.py](../src/zhuangfy/engine_fusion/native_worker.py) / [native_transport.py](../src/zhuangfy/engine_fusion/native_transport.py)：使用原版 MaiBot 的独立 Python 环境，通过双向 IPC 接收/回复 QQ 消息。必须收到 ZhuangFY 的真实 OneBot `message_id` 才返回成功。
- [hybrid_qq_bridge.py](../SenseVoice/hybrid_qq_bridge.py)：**单个指定 QQ 测试用户、私聊文字、非默认**的预览宿主。沿用 ZhuangFY 的 OneBot 鉴权、白名单、回复限速和人格/授权记忆读取。不给上游 Maisaka 开第二条 QQ 连接。
- [test_native_maisaka_adapter.py](../tests/unit/test_native_maisaka_adapter.py)：双向 ACK/原生调用边界的离线测试。

**这是供真实 QQ 联调的代码，不等于已经验证实际 LLM 与 wait 多轮行为。** CI 无法运行你的本地 QQ/模型配置；PR 继续保持 Draft。

### 单人隔离的实际试验

```powershell
git switch experiment/maisaka-full-runtime
git submodule update --init third_party/MaiBot
uv sync --project third_party/MaiBot --locked
$maiPy = "./third_party/MaiBot/.venv/Scripts/python.exe"
python scripts/maisaka_native_experiment.py init --instance .maisaka-hybrid-preview --python $maiPy
```

编辑 `.maisaka-hybrid-preview/config/{bot_config.toml,model_config.toml}`，
**必须关闭 MaiBot 的 `maim_message.enabled` 且将 `experimental.passive_replyer` 设为 `false`**，使用原版 Planner/Replyer 模型配置。
ZhuangFY 的 `SenseVoice/qq_settings.json` 需要针对预览用户启用私聊白名单、
关闭 `allow_all_private`。停止原来的 QQ Bridge，确保同一账号没有其他连接后运行：

```powershell
python scripts/chat_engine.py run --engine hybrid `
  --hybrid-user-id <你的QQ号> `
  --confirm-unstable-hybrid --confirm-exclusive-channel
```

不能把 `hybrid` 设为默认。独立 MaiBot 原生对照实例使用另一份
`.maisaka-experiment/`，Hybrid 使用专属 `.maisaka-hybrid-preview/`，
原始 ZhuangFY 记忆不直接被 Maisaka 写入。


为了不绕开 ZhuangFY 已有 Skill/工具授权，原生 Planner 在这轮私聊预览中**仅能调用原版 `reply`、`wait`、`stop`**。上游插件、网络搜索、MCP、Agent 工具暂时全部拒绝执行；`wait` 必须在 10–1800 秒内且每次输入最多连续登记 12 次。守护行为在[原生适配器](../src/zhuangfy/engine_fusion/native_runtime_adapter.py) 内，而不是仅凭模型提示词。

**隐私限制**：原版 Maisaka 会存储自己的本地聊天/记忆记录。
`/forget_me` 可处理 ZhuangFY 自身记录，但不会自动清除上游实验目录；
实验回复会明确提示残余数据。需要关闭预览后按需清理
`.maisaka-hybrid-preview/`，不能把两份系统的删除状态混为一谈。

**原版图片链路已接入实验代码、尚待实机验收**：预览支持 QQ
原图（PNG/JPEG/GIF/WebP，单轮最多 2 张）；由 ZhuangFY 的 QQ
来源白名单下载原始字节，不再转码 JPEG。宿主在独立
`.maisaka-hybrid-preview/incoming_images` 临时目录按 SHA-256
写入有限大小的图片，JSONL 只转发受控 AssetID，不允许任意路径
或外部链接；原生 Worker 校验哈希后调用
`image_manager.get_image_description(image_bytes=..., wait_for_build=True)`。
这是上游 ImageManager 真实的“保存图片 → 查缓存 → 异步调用 VLM
→ 持久化描述”方法。结果作为原版 `ImageComponent`
附属于同一用户消息进入 Planner，同时按 QQ 作用域写入
ZhuangFY 的 EvidenceStore / ImageMeaningStore。

识图回执包括 `asset_id / description / cache_hit / description_ready`。
`description_ready=false` 表示未配置视觉模型或识图失败，**不能宣称理解了图片**。
用户明确解释某张图的特殊含义仍单独存储在 ZhuangFY 的私聊记录，
再次发图时作为历史资料补充；不覆盖 Maisaka VLM 的观察。
原生 ImageManager 的数据库与保存文件属于 Hybrid 独立实验实例，
目前仍须手动清理，不能把 ZhuangFY 的 `/forget_me` 当成全部删除。

**真实机器还需验证** QQ 原图下载、原生数据库持久化、
缓存二次命中、VLM 实际生成、Planner 收到图片组件及
`reply/wait/stop` 行为；CI 是离线回归，不能代替这项验收。

## Hybrid IPC v1：已接通独立 Worker 握手（未接通 Maisaka）

此阶段已提交一条**实际启动子进程并通过 JSONL/stdio 通信**的链路：

- `src/zhuangfy/engine_fusion/ipc_contract.py`：版本号、请求 ID、已验证的 QQ 私聊作用域、原始消息组件（文字/图片 AssetID）、ZhuangFY 人格及获准的低信任长期记忆，以及 cancel / delivery ACK 协议。
- `src/zhuangfy/engine_fusion/ipc_transport.py`：ZhuangFY 侧子进程生命周期、固定版本握手、请求超时、答复身份匹配、失败关闭和退出清理。
- `src/zhuangfy/engine_fusion/worker.py`：隔离进程的协议解析、握手与拒绝不支持动作；**明确返回 `native_runtime_ready=false`**，禁止把这一步冒充成原版 Maisaka 推理已接入。
- `tests/unit/test_hybrid_ipc.py`：真实 Worker 子进程握手及退出、跨 QQ 作用域拒绝、图片/记忆数据边界、ACK 来源、超限数据和未接入原生 Runtime 时的安全拒绝。

在实验分支本地检验：

```powershell
python scripts/hybrid_probe.py
```

预期输出包括 `Hybrid IPC transport: OK` 和
`Maisaka native runtime: NOT CONNECTED`。这代表**通信链路正常**，
不代表第三引擎现已可以聊天或 QQ 通道切换成功。

只有 Native Maisaka 事件适配器真正调用上游
`MaisakaHeartFlowChatting` / `MaisakaReasoningEngine`，
并让 ZhuangFY QQ Host 收到确认的发送结果、计时器唤醒、停止和取消回执后，
才允许 Worker 报告 `native_runtime_ready=true` 并解锁
`scripts/chat_engine.py --engine hybrid`。此前 Hybrid 保持 fail-closed。

## 双引擎版本选择（本 PR 新增）

我们**保留 ZhuangFY 原有引擎**，MaiBot 作为另外一套实验引擎。
这里的“切换”是**在启动前选择两个独立进程中的一个**，而不是热替换
当前进程的 Planner，亦不是共享聊天历史。默认始终是 `zhuangfy`。

先检查：

```powershell
python scripts/chat_engine.py status
```

启动**原有 ZhuangFY QQ 引擎**：

```powershell
python scripts/chat_engine.py run --engine zhuangfy
```

切换到**完整的 MaiBot 原生引擎**（须先初始化子模块、
安装独立 `uv` 环境、配置 MaiBot 私有实例，且停止同 QQ 账号的旧引擎）：

```powershell
python scripts/chat_engine.py run --engine maibot --confirm-exclusive-channel
```

可以单独设置下次的默认选项，不会启动或终止正在运行的进程：

```powershell
python scripts/chat_engine.py set-default --engine maibot
python scripts/chat_engine.py run --confirm-exclusive-channel
# 想切回原版：
python scripts/chat_engine.py set-default --engine zhuangfy
python scripts/chat_engine.py run
```

上游虚拟环境未安装到默认位置时，用
`--maibot-python "./third_party/MaiBot/.venv/Scripts/python.exe"`
指定解释器，也可随 `set-default` 保存路径。

切换器配置位于仓库下的 `chat_engine_settings.json`，该文件不应提交到
Git；不涉及密钥。实验运行时的数据仍分别存在 **ZhuangFY 当前目录**
和 **`.maisaka-experiment/`**。实验配置、QQ 白名单、API 模型路由、
角色设定、PersonID 和记忆目前**没有自动同步**。

切换器使用独占文件锁，能阻止**另一份通过同一切换器启动**的引擎并行。
但它不能阻止用户从旧 bat/命令直接启动 `qq_bridge.py`，也无法拦截
其他机器同时登录相同账号，因此切换前仍须确保旧连接已退出。对原版
MaiBot 要求 `--confirm-exclusive-channel` 明确确认。

下一步我们可以保持此双引擎开关不变，通过事件适配桥接统一 QQ、
PersonID/Memory 与图片语义数据，按测试情况逐步共享能力，
**不删除 ZhuangFY 自有 Planner/Replyer 实现**。

## 集成路线与不可逾越的边界

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| A. 固定完整上游 + 独立原生实例 | 保留完整 GPL 源码、原生图片缓存、Planner/Reply/Wait/Stop | 已提交启动器与离线测试；真实模型/QQ 未验收 |
| B. 上游进程到 ZhuangFY 事件边界 | QQ MessageComponent / Observation、真实投递 ACK 回传 | 实验代码已接通；实机 ACK 待验收，ToolResult 尚未映射 |
| C. 合并 ZhuangFY Persona / PersonID / Memory | 双向读取映射、撤回/删除、会话隔离、权限一致 | 未实现 |
| D. 启动前独立双引擎选择 | CLI 选择启动原版 ZhuangFY 或完整 MaiBot；不覆盖任何引擎 | 已提交启动器，真实 QQ 尚未验证 |
| D2. 同一 ZhuangFY Runtime 内部切换 | 共享 QQ 通道、Persona、PersonID/Memory、超时回退 | 未实现；目前按独立进程切换 |
| E. 原生图片处理接管 | 原始 QQ 字节 → 上游 ImageManager 保存/hash/VLM/缓存 → Observation | 实验代码已接通且离线测试，真实 QQ/VLM 待验收；跨模型缓存失效待验证 |

这里 **不宣称 B–E 的实机运行已通过验收**。即使子模块已经包含全套源码，
不能把本机 MaiBot 独立进程等同于 ZhuangFY 已支持这套循环。
直接将 `src.maisaka` import 到现有 ZhuangFY 进程还会遇到顶层 `src`
包名、数据库连接和全局配置冲突，因此应隔离为子进程，通过窄协议桥接。

## 基线复现实验

在原版实例依次测试：“猜一猜这四个多少钱”→ Replyer 回复→
用户沉默→ Planner 选择 `wait(60)` 等→计时器再次触发 Planner→
等待或 stop。记录每次 Planner 原始动作与最终动作，区分
“模型选择继续判断”与“程序强制调度”。相同照片重复发送，
记录缓存命中、VLM 请求数、描述和用户解释的隔离行为。

**不要**在上游还未接入 ZhuangFY 时，用其运行结果声称庄方宜已经具备这些行为。
