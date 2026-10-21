# ZhuangFY

ZhuangFY 是一个正在开发的个人语音助手。核心包已开始迁入 `src/zhuangfy/`；桌面控制台、语音会话和 QQ 桥接目前仍由 `SenseVoice/` 中的兼容入口运行，原有配置、数据库和本地模型保持原位。它能持续监听、用本地 VAD 截取语音、本地识别文字、按“庄方宜”或“小庄”“庄天师”唤醒、调用兼容 OpenAI Chat Completions 的模型接口，并用系统语音或本机 GPT-SoVITS 分段朗读回复。界面显示 `Listening / Thinking / Speaking`；播报时可用唤醒称呼插话打断。可选的 Mem0 长期记忆使用本地向量库和硅基流动 Embedding；首次启用方式与数据边界见 [语音与配置说明](SenseVoice/README.md)。

| 目录 | 用途 | 当前运行是否依赖 |
| --- | --- | --- |
| `src/zhuangfy/` | 新包结构；已有统一路径、情绪、唤醒、QQ 格式化、人格包和活动记录 | 是 |
| `SenseVoice/` | 兼容运行目录；仍包含控制台、语音、对话、QQ 桥接和本地模型 | 是 |
| `resources/` | 角色图标与版本化人格提示词 | 是 |
| `config/examples/`、`tests/`、`scripts/`、`docs/` | 配置示例、验证与后续迁移位置 | 部分 |
| `data/` | 后续本地运行数据的目标目录；当前不自动迁移 | 否 |
| `third_party/licenses/` | 已引用第三方项目的许可文本 | 文档 |
| `ARCHITECTURE.md` | 数据流、文件职责与协作边界 | 文档 |
| `THIRD_PARTY.md` | 第三方来源与许可线索 | 文档 |

QQ 文字消息可通过独立安装的 SnowLuma 接入 OneBot WebSocket，由 `SenseVoice/qq_bridge.py` 接收并调用现有模型回复。指定私聊账号可选用本机庄方宜模型生成语音回复；默认只发文字。群聊须另行列出群号且必须 @ 机器人。配置、速率限制和启动方法见 [QQ 桥接说明](SenseVoice/README.md#qq-消息桥接snowluma)。

从这里开始：[架构与模块分工](ARCHITECTURE.md)；首次安装及运行方法见 [语音与配置说明](SenseVoice/README.md)。现有安装仍可双击 `SenseVoice/启动界面.cmd`。在已有 Python 3.12 虚拟环境中运行 `pip install -e . --no-deps` 后，也可以从项目根目录执行 `python -m zhuangfy` 启动同一个控制台。当前包入口面向源码检出和可编辑安装，完整的独立发行包会在迁移结束后处理。密钥保存在本机 `.env.local`，不要提交到 Git。

当前已形成基础对话闭环，但还没有桌宠/Live2D 形象、声纹识别或真正的声学回声消除。长期记忆及云端 Embedding 的启用方式见 [语音与配置说明](SenseVoice/README.md)；第三方来源与许可见 [第三方清单](THIRD_PARTY.md)。ZhuangFY 自有代码的根目录许可证尚待决定。
