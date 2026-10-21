# ZhuangFY 当前结构与模块分工

实际启动入口仍是 `SenseVoice/启动界面.cmd`（统一控制台）；可编辑安装后也可运行 `python -m zhuangfy`。控制台可打开主语音窗口、文字测试、记忆管理以及配置/启动 QQ 连接。`SenseVoice/启动LLM测试.cmd` 仍是独立的文字对话调试窗口。以下是迁移中的目录快照；大型界面与 QQ 流程尚未迁入新包。

```text
ZhuangFY/
├─ README.md                 项目入口与当前范围
├─ ARCHITECTURE.md           本文件
├─ THIRD_PARTY.md            第三方来源与许可线索
├─ pyproject.toml            可编辑安装与包入口
├─ src/zhuangfy/             正式 Python 包
│  ├─ paths.py               资源、配置、模型、数据库与外部服务路径
│  ├─ core/emotion.py        情绪状态
│  ├─ speech/wake.py         唤醒校正与对话时间窗
│  ├─ persona/packs.py       人格包登记与读取
│  ├─ channels/qq/reply_formatter.py  QQ 回复格式化
│  └─ observability/activity_log.py  当前会话事件流
├─ resources/images/         桌面角色图标
├─ resources/personas/       版本化人格包
├─ config/examples/          路径配置示例
├─ tests/                    新包的单元与集成测试
├─ scripts/                  后续迁移的启动与训练脚本位置
├─ docs/                     后续整理的架构、开发和使用文档
├─ data/                     后续运行数据位置（当前不迁移）
├─ third_party/licenses/     第三方许可文本
├─ SenseVoice/               ZhuangFY 当前可运行的独立原型
│  ├─ branding.py            应用名称与图标路径
│  ├─ gui.py                 语音窗口、录音线程、流程协调与语音播放
│  ├─ dashboard.py           统一控制台、设置编辑、服务状态与启停
│  ├─ activity_log.py        新包模块的兼容导入
│  ├─ continuous.py          流式 Silero VAD，分段与句尾检测
│  ├─ recognize.py           SenseVoice 本地识别及命令行入口
│  ├─ wake.py                新包模块的兼容导入
│  ├─ interaction.py         交互状态、分段播报与回声文本过滤
│  ├─ llm.py                 模型配置、密钥读取和 HTTP 请求
│  ├─ model_catalog.py       后台获取供应商 /models 列表
│  ├─ persona.py             默认人格包入口与旧版默认词迁移
│  ├─ persona_packs.py       新包模块的兼容导入
│  ├─ memory.py              Mem0 本地向量库、检索、提取、纠错与删除
│  ├─ memory_gui.py          长期记忆查看、手动更正与遗忘界面
│  ├─ chat_worker.py         模型请求的 Qt 后台线程
│  ├─ llm_gui.py             供应商/模型/提示词配置与文字测试
│  ├─ qq_bridge.py           SnowLuma / OneBot QQ 消息桥接，可选语音回复
│  ├─ qq_settings.json       本机生成：QQ 白名单与回复频率
│  ├─ download_models.py     获取并校验本地语音资源
│  ├─ settings.json          本机生成：唤醒、自动回复和 TTS 设置
│  ├─ llm_settings.json      本机生成：供应商、模型、提示词
│  ├─ .env.local             本机生成：API 密钥；绝不提交
│  ├─ models/                下载获得：SenseVoice、词表和 VAD 模型
│  ├─ samples/zh.wav         可选下载：识别测试音频
│  ├─ test_wake.py           唤醒状态测试
│  ├─ test_llm.py            模型客户端测试
│  ├─ test_memory.py         长期记忆命令、纠错与删除测试
│  ├─ test_voice_loop.py     语音对话流程测试
│  ├─ requirements.txt       Python 依赖版本
│  ├─ 启动界面.cmd            统一控制台启动器
│  ├─ 启动LLM测试.cmd         文字测试界面启动器
│  └─ README.md              操作说明、验证记录和来源
```

`.venv/`、`python-runtime/`、`.uv-cache/`、`__pycache__/` 是本机环境或生成物。`models/` 的权重、词表和 `samples/` 的音频通过 `download_models.py` 获取，不纳入 Git；本地密钥和个人设置也被 `.gitignore` 排除。仓库中的 README 和 `THIRD_PARTY.md` 说明安装与来源。

`dashboard.py` 只协调已有模块：模型配置仍通过 `llm.py` 保存，`model_catalog.py` 在后台调用供应商的 `GET /models`；QQ 参数写入 `qq_settings.json` 并由 `qq_bridge.py` 在启动时读取；SnowLuma 的具体设置仍由其 WebUI 管理。控制台以本机端口检测服务可达性，用 `QProcess` 管理由它启动的 SnowLuma、QQ 桥接和 GPT-SoVITS。保存 GPT-SoVITS 为语音引擎后，下次打开控制台自动启动其本机服务；启动子进程时清除桌面 Python 的环境提示，避免污染引擎独立的虚拟环境。OneBot 端口就绪后可自动启动桥接；桥接进程内部负责断线重连。退出控制台时停止由它启动的进程。端口可达不能证明 QQ 已登录，也不表示某个在外部启动的服务受控制台管理。

## 一句话从麦克风走到扬声器

```text
麦克风 → gui.py 的 ContinuousWorker → continuous.py 的 StreamingVad
       → recognize.py 的 SenseVoice 解码 → wake.py 的 ConversationGate
       → gui.py 判断自动回复 → chat_worker.py → llm.py 的模型接口
       → gui.py 显示回复 → Qt 系统语音或本机 GPT-SoVITS → 扬声器
```

主界面用 `sounddevice` 以 16 kHz 单声道持续采集。`StreamingVad` 只判断语音片段的开始和结束，约 0.8 秒静音后输出一段语音，并保留约 0.25 秒句首声音；它不识别文字。`recognize.py` 使用本地 `sherpa-onnx`、SenseVoice ONNX 模型和 `tokens.txt` 解码，因此这一步不调用云端对话接口。

`ConversationGate` 在**识别之后**校正句首称呼：例如“小庄”“庄天师”或识别误写的“庄方怡”可按配置视作“庄方宜”。它区分待唤醒与对话中，默认 30 秒无交流回到待唤醒。单独呼唤名字只开启对话窗口；带内容的唤醒句和窗口内后续句子在“唤醒后自动发送给模型”打开时才交给 LLM。手动录音和示例音频是识别调试入口，不走唤醒及自动发送流程。

`llm.py` 从 `llm_settings.json` 读取供应商、模型 ID、URL 和角色提示词，从 `.env.local` 读取密钥；它把角色提示词、最近六轮成功对话和当前**文字**发往 Chat Completions 接口。模型服务可选硅基流动或自定义兼容接口。API 调用放在 `ChatWorker` 中，避免卡住界面；成功后才将这一轮加入内存历史。主语音界面和文字测试界面的历史分别保存在各自窗口内存中，关闭即清空。

`resources/personas/zhuang_fangyi_v1/prompt.md` 是首套正式人格包，参考 MengPaul 的场景驱动角色 Skill，整理为现实桌面助手可用的角色背景、称呼、说话习惯和事实边界。`persona_packs.py` 登记内置包；`persona.py` 提供默认词和旧版默认词迁移。控制台及 `llm_gui.py` 可显式载入人格包并编辑，只有保存后才写入本机设置。旧版内置默认提示词会在加载时升级为新默认，用户自定义提示词不改动。人格指令与最近六轮历史一起构成当前对话的短期上下文。

QQ 接入是独立进程 `qq_bridge.py`：SnowLuma 把 QQ 事件转换成 OneBot v11 WebSocket 消息，桥接层先检查私聊/群聊白名单、机器人 @、消息去重与回复限速，再把本轮文字交给现有 `llm.chat`。每个发送者维护独立的短期历史。指定私聊账号可由思考模型选择文字或语音回复；语音使用本机 GPT-SoVITS，再通过 OneBot `record` 段交给 SnowLuma 转码、发送，仍计入同一限额。群聊只发文字，QQ 入站语音尚不识别。个人记忆按明确白名单隔离，不混入桌面主人的 Mem0。`qq_settings.json` 与 SnowLuma token 只保存在本机，不纳入 Git。

`memory.py` 在用户启用长期记忆后，以 Mem0 + 本地 Qdrant 存储提炼出的事实，并通过硅基流动 `BAAI/bge-m3` 进行 Embedding 检索。`ChatWorker` 在回复前检索最多五条相关记忆，`MemoryWriteWorker` 只在有效回复被界面接受后后台提炼。当前六轮历史仍只用于短期上下文；长期记忆跨窗口重启保留。明确的记住/遗忘命令由 `ChatWorker` 直接处理。更新冲突时先写入新事实，再严格筛选要删除的旧记忆；无法确认则保留供用户在 `memory_gui.py` 中更正。原始消息不长期保存在 Mem0 的消息表，单条删除清除对应历史，“遗忘全部”删除整个 `memory_data/`。长期记忆默认关闭，启用意味着对话文字将再用于云端 Embedding 与提取。

`gui.py` 用 `Listening / Thinking / Speaking` 状态协调监听、模型请求与播报。麦克风在生成和播放期间保持工作；新讲话可以令旧请求失效，或中断正在分段播放的 TTS。扬声器模式要求插话以唤醒名开头，并用 `interaction.py` 对照当前回复过滤疑似回声；这是文字层防护，不等于 AEC。耳机模式支持 VAD 起声后立即停播。语音输出可选 PySide6 `QTextToSpeech` 或本机 GPT-SoVITS；后者在播放当前段时预生成下一段。当前在收到完整模型回复后按句分段播放，没有 LLM token 流式播放。

## 协作边界

| 修改目标 | 主要文件 | 先确认的接口或影响 |
| --- | --- | --- |
| 控制台、设置入口和服务启停 | `SenseVoice/dashboard.py` | 复用 `llm.py` 与 `QqSettings` 的验证；SnowLuma WebUI 是独立服务 |
| 麦克风、界面、TTS、流程编排 | `SenseVoice/gui.py` | Qt 信号/线程、监听暂停与恢复、手动录音入口 |
| 说话起止与分段参数 | `SenseVoice/continuous.py` | `StreamingVad.accept()` 输出完整语音数组；`gui.py` 消费该输出 |
| 本地语音识别或模型替换 | `SenseVoice/recognize.py`、`SenseVoice/models/` | `create_recognizer()`、`decode_segment()`；音频须为 16 kHz 单声道 |
| 唤醒名称与超时规则 | `SenseVoice/wake.py` | `ConversationGate.process()` 返回 ignored / woke / accepted；别名校正在 ASR 后 |
| 实时状态、分段和疑似回声判断 | `SenseVoice/interaction.py`、`SenseVoice/gui.py` | 区分完整回复的分段播报与真正的流式模型输出；扬声器模式仍依赖唤醒称呼 |
| 模型供应商与请求格式 | `SenseVoice/llm.py`、`SenseVoice/model_catalog.py`、`SenseVoice/chat_worker.py` | `chat()` 输入设置、密钥、历史、当前文字，返回回复文字；`list_models()` 返回模型 ID |
| 默认人格与表达规则 | `SenseVoice/persona.py`、`SenseVoice/llm_gui.py` | 编辑默认提示词或本机自定义提示词；保留旧设置与对话历史边界 |
| 长期记忆 | `SenseVoice/memory.py`、`SenseVoice/memory_gui.py` | Mem0、Qdrant 与远程 Embedding；启用开关、记忆纠错、遗忘与本地目录隔离 |
| QQ 消息桥接 | `SenseVoice/qq_bridge.py` | SnowLuma / OneBot 正向 WebSocket；白名单、@ 触发、去重、频率和独立历史 |
| 跨端个人记忆 | `SenseVoice/owner_memory.py`、`SenseVoice/member_memory.py` | 本机显式关联机器人 QQ 与主人 QQ；语音和 QQ 共用该成员档案，短期上下文仍分开 |
| 语音交互 | `SenseVoice/dashboard.py`、`SenseVoice/gui.py` | 设备选择、持续监听、单句识别与 TTS 测试；共用同一个语音窗口实例 |
| 可选专属音色 | `SenseVoice/gpt_sovits.py`、`SenseVoice/gui.py`、`SenseVoice/dashboard.py` | 扫描成对 GPT/SoVITS 权重并在控制台切换本机模型；参考音频与台词独立配置。播放时预生成下一段，支持打断、清空预存音频与系统语音回退。引擎权重位于仓库外 |
| 文字调试与配置编辑 | `SenseVoice/llm_gui.py` | 调用 `llm.py` 的配置和聊天接口；不使用主界面的历史 |
| 角色形象或桌宠窗口 | 尚无独立模块 | 可以新增表现层，优先消费现有状态与回复，避免复制语音/LLM 逻辑 |

`SenseVoice/README.md` 记录启动、配置和验证步骤。协作时不要提交或传送 `.env.local`；`llm_settings.json` 虽然不存密钥，但可能含个人角色提示词，也应由每位开发者自己配置。`models/` 和示例音频的来源与许可线索见 `SenseVoice/README.md`，依赖版本见 `requirements.txt`。
