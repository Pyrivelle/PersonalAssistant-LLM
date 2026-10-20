# Shimano

Shimano 是一个正在开发的个人语音助手。当前可运行的部分位于 `SenseVoice/`：它能持续监听、用本地 VAD 截取语音、本地识别文字、按“庄方宜”或“小庄”“庄天师”唤醒、调用兼容 OpenAI Chat Completions 的模型接口，并用系统语音分段朗读回复。界面显示 `Listening / Thinking / Speaking`；播报时可用唤醒称呼插话打断。可选的 Mem0 长期记忆使用本地向量库和硅基流动 Embedding；首次启用方式与数据边界见 [SenseVoice 使用说明](SenseVoice/README.md)。

| 目录 | 用途 | 当前运行是否依赖 |
| --- | --- | --- |
| `SenseVoice/` | Shimano 自己的 Python / PySide6 练习原型；包含界面、语音、对话代码和本地模型 | 是 |
| `ARCHITECTURE.md` | 数据流、文件职责与协作边界 | 文档 |
| `THIRD_PARTY.md` | 第三方来源与许可线索 | 文档 |

QQ 文字消息可通过独立安装的 SnowLuma 接入 OneBot WebSocket，由 `SenseVoice/qq_bridge.py` 接收并调用现有模型回复。默认只允许显式白名单内的私聊；群聊须另行列出群号且必须 @ 机器人。配置、速率限制和启动方法见 [QQ 桥接说明](SenseVoice/README.md#qq-消息桥接snowluma)。

从这里开始：[架构与模块分工](ARCHITECTURE.md)；首次安装及运行方法见 [SenseVoice 使用说明](SenseVoice/README.md)。新成员可在 `SenseVoice/` 中创建 Python 3.12 虚拟环境、安装 `requirements.txt`，再运行 `download_models.py --sample` 获取本地语音资源。双击 `SenseVoice/启动界面.cmd` 打开统一控制台，在其中配置 API、人格、QQ 桥接并启动语音窗口；密钥保存在本机 `.env.local`，不要提交到 Git。

当前已形成基础对话闭环，但还是独立的练习原型：没有桌宠/Live2D 形象、声纹识别或真正的声学回声消除。长期记忆默认关闭，云端 Embedding 与事实提取需启用后另行验证。Sakura 的桌面程序与插件并未接入这个运行链路，也不包含在本仓库中；保留必要的许可署名和来源说明。
